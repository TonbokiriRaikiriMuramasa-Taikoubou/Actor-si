"""Gemini API プロバイダ（標準ライブラリのみ）と、オフライン用シミュレータ。

``GeminiProvider`` は Google AI Studio の API キーで REST API を直接叩く。
``MockProvider`` は API キーが無い環境でも Actor の全機能（スキル選択・
ストリーミング・思考ログ・UI）を試せるようにするための決定的なシミュレータ。
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator, Sequence

from .config import Settings
from .keys import KeyPool, mask_key, parse_retry_delay, redact
from .skills import fenced_blocks

__all__ = [
    "ProviderError",
    "Provider",
    "GeminiProvider",
    "MockProvider",
    "get_provider",
    "ROTATE_STATUSES",
]

#: キーを替えれば直る見込みがある HTTP ステータス（429 = レート制限、401/403 = キーが無効）
ROTATE_STATUSES = frozenset({401, 403, 429})


class ProviderError(RuntimeError):
    """プロバイダ側のエラー（HTTP エラー / ネットワーク断など）。"""

    def __init__(self, message: str, status: int | None = None, detail: Any = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.retry_after = retry_after   # 429 の RetryInfo から拾えたら秒数

    @property
    def retryable(self) -> bool:
        return self.status in {408, 429, 500, 502, 503, 504}

    def redacted(self, secrets: Sequence[str]) -> "ProviderError":
        """API のエラー本文にキーが混ざっていても伏せ字にしたコピーを返す。"""
        message = redact(str(self), secrets)
        if message == str(self):
            return self
        clone = ProviderError(message, status=self.status, detail=self.detail, retry_after=self.retry_after)
        return clone


# ----------------------------------------------------------------------
# 会話の正規化
# ----------------------------------------------------------------------
def to_rest_tools(declarations: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """ツール宣言のリストを REST API の tools 形式へ包む。"""
    if not declarations:
        return []
    if declarations and "functionDeclarations" in declarations[0]:
        return list(declarations)  # すでに包まれている
    return [{"functionDeclarations": list(declarations)}]


def to_gemini_contents(messages: Sequence[dict[str, Any]], system_text: str = "") -> list[dict[str, Any]]:
    """CLI/Web から来た会話履歴を Gemini の contents 形式へ変換する。

    連続する同一ロールはマージする（Gemini API は交互のロールを期待するため）。
    """
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = "model" if message.get("role") in {"model", "assistant", "ai"} else "user"
        if message.get("parts"):  # functionCall / functionResponse を含む生の parts
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(message["parts"])
            else:
                contents.append({"role": role, "parts": list(message["parts"])})
            continue
        text = message.get("content") or message.get("text") or ""
        if not str(text).strip():
            continue
        part = {"text": str(text)}
        images = message.get("images") or []
        parts: list[dict[str, Any]] = []
        for image in images:
            if isinstance(image, dict) and image.get("data") and image.get("mime_type"):
                parts.append({"inlineData": {"mimeType": image["mime_type"], "data": image["data"]}})
        parts.append(part)
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    return contents


# ----------------------------------------------------------------------
# Provider 基底
# ----------------------------------------------------------------------
class Provider:
    name = "base"
    is_mock = False

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def model(self) -> str:
        return self.settings.model

    def _model(self, override: str | None = None) -> str:
        """スキルがモデルを指定していればそれを使う（1ターン単位で切り替え可能）。"""
        return (override or "").strip() or self.settings.model

    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        system_instruction: dict[str, Any] | None = None,
        *,
        generation_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def stream(
        self,
        messages: Sequence[dict[str, Any]],
        system_instruction: dict[str, Any] | None = None,
        *,
        generation_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        """{"type": "thought"|"text"|"done"|"error", ...} を順に返す。"""
        result = self.generate(
            messages, system_instruction,
            generation_config=generation_config, tools=tools, model=model, **kwargs,
        )
        if result.get("thoughts"):
            yield {"type": "thought", "text": result["thoughts"]}
        text = result.get("text", "")
        for i in range(0, len(text), 24):
            yield {"type": "text", "text": text[i:i + 24]}
        yield {
            "type": "done",
            "text": "",
            "usage": result.get("usage", {}),
            "model": result.get("model", self.model),
            "raw": result.get("raw", {}),
        }

    def model_catalog(self) -> list[str]:
        return []

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, "mock": self.is_mock}


# ----------------------------------------------------------------------
# Gemini (REST)
# ----------------------------------------------------------------------
class GeminiProvider(Provider):
    """generativelanguage.googleapis.com を urllib で直接叩く軽量クライアント。

    追加依存なしで動くため、既定のプロバイダはこちら。
    ``google-genai`` SDK を使いたい場合は ``GenAiProvider``（``--provider genai``）を選ぶ。

    429（ResourceExhausted / Quota exceeded）や 401/403 を受け取ったら、``settings.key_pool()``
    の次のキーへ自動で切り替えて同じリクエストを投げ直す（:meth:`_open`）。
    """

    name = "gemini"
    API_VERSION = "v1beta"
    MAX_RETRIES = 3

    def __init__(self, settings: Settings):
        super().__init__(settings)

    @property
    def keys(self) -> KeyPool:
        """キーのプール（``Settings`` が持つ実体を毎回見る＝実行中の設定変更にも追従する）。"""
        return self.settings.key_pool()

    @property
    def model_chain(self) -> list[str]:
        """主モデル → 逃げ道（``ACTOR_MODEL_FALLBACKS``）の順（重複なし）。"""
        chain: list[str] = []
        for name in [self.model, *(self.settings.model_fallbacks or [])]:
            name = (name or "").strip()
            if name and name not in chain:
                chain.append(name)
        return chain

    def _model_order(self, override: str | None = None) -> tuple[list[str], list[dict[str, Any]]]:
        """試すモデルの順番と、その理由の通知を返す。

        主モデルのキーが全部 429 なら、**使えるキーが残っているモデルを先に**回す
        （枠は「モデル × プロジェクト」単位なので、別モデルなら同じキーでも通る）。
        無駄な 1 リクエスト（必ず 429 になる試行）を省くための入れ替え。
        """
        primary = self._model(override)
        chain = [primary] + [name for name in self.model_chain if name != primary]
        pool = self.keys
        notices: list[dict[str, Any]] = []
        if pool.size:
            ready = [name for name in chain if pool.ready_count_for(name) > 0]
            if ready and ready[0] != chain[0]:
                rest = [name for name in chain if name not in ready]
                notices.append({
                    "kind": "model_fallback",
                    "from": chain[0],
                    "to": ready[0],
                    "text": f"{chain[0]} has all keys rate-limited; trying {ready[0]} instead.",
                })
                chain = ready + rest
        return chain, notices

    # ---- 低レベル HTTP ----
    def _url(self, method: str, *, stream: bool = False, model: str | None = None) -> str:
        base = self.settings.base_url.rstrip("/")
        version = (self.settings.api_version or self.API_VERSION).strip("/")
        path = (self.settings.model_path or "models/{model}").replace(
            "{model}", urllib.parse.quote(self._model(model))
        )
        url = f"{base}/{version}/{path.lstrip('/')}:{method}"
        params = {}
        if stream:
            params["alt"] = "sse"
        return url + ("?" + urllib.parse.urlencode(params) if params else "")

    def _request(self, url: str, payload: dict[str, Any] | None = None,
                 api_key: str | None = None) -> urllib.request.Request:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Actor/0.1 (+https://github.com/)",
        }
        key = self.settings.api_key if api_key is None else api_key
        if key:
            headers["x-goog-api-key"] = key
        return urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")

    def _attempt(self, url: str, payload: dict[str, Any] | None, key: str) -> tuple[Any, ProviderError | None]:
        """1 本のキーで送る（5xx とネットワーク断は同じキーで指数バックオフ）。"""
        error: ProviderError | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                return urllib.request.urlopen(self._request(url, payload, key), timeout=self.settings.timeout), None
            except urllib.error.HTTPError as exc:
                body = _read_body(exc)
                message = redact(_extract_error(body) or str(exc.reason), self.keys.keys)
                error = ProviderError(
                    f"{exc.code} {message}",
                    status=exc.code,
                    detail=_safe_json(body),
                    retry_after=parse_retry_delay(_safe_json(body)),
                )
            except urllib.error.URLError as exc:
                error = ProviderError(f"Network error: {redact(str(exc.reason), self.keys.keys)}")
            except TimeoutError:  # pragma: no cover - 実環境依存
                error = ProviderError("Request timed out.")
            if self.settings.key_rotate and error.status in ROTATE_STATUSES:
                return None, error            # キーを替えた方が早い（同じキーで粘らない）
            if not error.retryable or attempt == self.MAX_RETRIES - 1:
                return None, error
            time.sleep(min(4.0, 0.8 * (2 ** attempt)))
        return None, error or ProviderError("Request failed.")

    def _open(self, build_url: "Callable[[str], str]", payload: dict[str, Any] | None = None,
              *, override: str | None = None) -> tuple[Any, str, list[dict[str, Any]]]:
        """キーのローテーション → モデルのフォールバック、の順で粘って開く。

        戻り値: ``(response, 実際に使ったモデル, 通知)``。全部だめなら理由の分かる
        :class:`ProviderError` を投げる（キーは伏せ字・復帰までの秒数つき）。
        """
        pool = self.keys
        models, notices = self._model_order(override)
        if not pool.size and not self.settings.api_key:
            raise ProviderError(
                "No API key configured. Use `actor key set` or set GEMINI_API_KEY in .env."
            )
        error: ProviderError | None = None
        for model in models:
            tried: list[str] = []
            while True:
                key = pool.current or self.settings.api_key
                if not key:
                    break
                if key in tried:
                    break
                tried.append(key)
                response, error = self._attempt(build_url(model), payload, key)
                if error is None:
                    pool.mark_ok(key, model)
                    return response, model, notices
                if self.settings.key_rotate and error.status in ROTATE_STATUSES:
                    if pool.rotate(key=key, model=model, status=error.status, reason=str(error),
                                   retry_after=error.retry_after):
                        continue
                    break          # このモデルは全キー制限 → 次のモデル（逃げ道）へ
                raise error
            else:  # pragma: no cover - while True は break / return で抜ける
                pass
            if model != models[-1]:
                next_model = models[models.index(model) + 1]
                notices.append({
                    "kind": "model_fallback",
                    "from": model,
                    "to": next_model,
                    "text": f"{model} has all keys rate-limited; falling back to {next_model} instead.",
                })
        if error is None:  # pragma: no cover - ロジック上ほぼ起きない
            raise ProviderError("Request failed.")
        if error.status in ROTATE_STATUSES and pool.size:
            raise ProviderError(
                pool.exhaustion_message(status=error.status, last_error=error, models=models),
                status=error.status, detail=error.detail, retry_after=error.retry_after,
            ) from error
        raise error

    # ---- 公開 API ----
    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        system_instruction: dict[str, Any] | None = None,
        *,
        generation_config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        current = self._model(model)
        contents = to_gemini_contents(messages)
        if not contents:
            raise ProviderError("No messages to send.")
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config or self.settings.generation_config(),
        }
        if system_instruction and system_instruction.get("parts"):
            payload["systemInstruction"] = system_instruction
        if tools:
            payload["tools"] = to_rest_tools(tools)

        response, used_model, notices = self._open(
            lambda name: self._url("generateContent", model=name), payload, override=model
        )
        with response:
            raw = json.loads(response.read().decode("utf-8"))
        result = _normalize_response(raw, used_model)
        result["notices"] = notices
        if used_model != current:
            result["requested_model"] = current
        return result

    def stream(
        self,
        messages: Sequence[dict[str, Any]],
        system_instruction: dict[str, Any] | None = None,
        *,
        generation_config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        requested = self._model(model)
        contents = to_gemini_contents(messages)
        if not contents:
            raise ProviderError("No messages to send.")
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config or self.settings.generation_config(),
        }
        if system_instruction and system_instruction.get("parts"):
            payload["systemInstruction"] = system_instruction
        if tools:
            payload["tools"] = to_rest_tools(tools)

        usage: dict[str, Any] = {}
        function_calls: list[dict[str, Any]] = []
        try:
            response, current, notices = self._open(
                lambda name: self._url("streamGenerateContent", stream=True, model=name),
                payload, override=model,
            )
        except ProviderError as exc:
            # 429 で全キー（全モデル）が使えないときはここに来る（キーは伏せ字・対処つきのメッセージ）
            yield {"type": "error", "text": str(exc), "status": exc.status}
            return
        for notice in notices:
            yield {"type": "notice", **notice}
        with response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usageMetadata"):
                    usage = _usage_of(chunk["usageMetadata"])
                for candidate in chunk.get("candidates", []):
                    for part in candidate.get("content", {}).get("parts", []):
                        if part.get("functionCall"):
                            call = part["functionCall"]
                            function_calls.append(call)
                            yield {"type": "function_call", "call": call}
                        text = part.get("text")
                        if not text:
                            continue
                        yield {"type": "thought" if part.get("thought") else "text", "text": text}
        yield {
            "type": "done",
            "text": "",
            "usage": usage,
            "model": current,
            "requested_model": requested if current != requested else "",
            "function_calls": function_calls,
        }

    def model_catalog(self, strict: bool = False) -> list[str]:
        """利用できるモデル名の一覧（``strict=True`` なら失敗時に例外を投げる）。"""
        url = f"{self.settings.base_url.rstrip('/')}/{(self.settings.api_version or self.API_VERSION).strip('/')}/models"
        try:
            response, _model, _notices = self._open(lambda _name: url)
            with response:
                payload = json.loads(response.read().decode("utf-8"))
        except ProviderError:
            if strict:
                raise
            return []
        names = []
        for model in payload.get("models", []):
            name = str(model.get("name", "")).replace("models/", "")
            methods = model.get("supportedGenerationMethods") or []
            if name and ("generateContent" in methods or not methods):
                names.append(name)
        return names

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "mock": False,
            "base_url": self.settings.base_url,
            "api_key_set": bool(self.settings.api_key),
            "api_key_masked": mask_key(self.settings.api_key),
            "keys": self.keys.health(),
            "model_chain": self.model_chain,
            "api_version": self.settings.api_version,
            "model_path": self.settings.model_path,
        }


# ----------------------------------------------------------------------
# Mock（オフライン・シミュレータ）
# ----------------------------------------------------------------------
class MockProvider(Provider):
    """API キー無しでも全機能を試せる、規則ベースのシミュレータ。

    本物のモデルではないが、``system_instruction`` に含まれるスキル定義を
    実際に読み取って「どのスキルが効いているか」「どんな手順で進めるか」を
    明示するため、パイプラインの検証には十分な出力を返す。
    """

    name = "mock"
    is_mock = True

    def __init__(self, settings: Settings):
        super().__init__(settings)
        try:
            self.delay = float(os.environ.get("ACTOR_MOCK_DELAY", "0.012"))
        except ValueError:
            self.delay = 0.012
        self._rng = random.Random(20261012)

    # ---- 生成 ----
    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        system_instruction: dict[str, Any] | None = None,
        *,
        generation_config: dict[str, Any] | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from .prompt import system_text_of

        system_text = system_text_of(system_instruction or {})
        user_text = _last_user_text(messages)
        skills = _extract_skills(system_text)
        thoughts = self._thoughts(skills, user_text)
        # ツールが使えるときは、必要そうなら 1 回だけ呼び出す（function calling の再現）
        calls = self._plan_tool_calls(tools, user_text, messages)
        if calls:
            return {
                "text": "",
                "thoughts": thoughts + "\n" + "\n".join(
                    f"Calling tool `{c['name']}` with arguments: {c['args']}" for c in calls
                ),
                "function_calls": calls,
                "model": f"{self._model(kwargs.get('model'))} (simulation)",
                "usage": {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0, "simulated": True},
                "raw": {"simulated": True, "tool_call": True},
            }
        text = self._compose(skills, user_text, messages)
        prompt_chars = len(system_text) + sum(len(str(m.get("content", ""))) for m in messages)
        current = self._model(kwargs.get("model"))
        return {
            "text": text,
            "thoughts": thoughts,
            "model": f"{current} (simulation)",
            "usage": {
                "promptTokenCount": int(prompt_chars / 2.2),
                "candidatesTokenCount": int(len(text) / 2.2),
                "totalTokenCount": int((prompt_chars + len(text)) / 2.2),
                "simulated": True,
            },
            "raw": {"simulated": True, "skills": [s["name"] for s in skills]},
        }

    def stream(
        self,
        messages: Sequence[dict[str, Any]],
        system_instruction: dict[str, Any] | None = None,
        *,
        generation_config: dict[str, Any] | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        result = self.generate(
            messages, system_instruction,
            generation_config=generation_config, tools=tools, model=model, **kwargs,
        )
        for chunk in _chunks(result.get("thoughts", ""), 12):
            _sleep(self.delay * 0.5)
            yield {"type": "thought", "text": chunk}
        calls = result.get("function_calls") or []
        for call in calls:
            yield {"type": "function_call", "call": call}
        for chunk in _chunks(result.get("text", ""), 10):
            _sleep(self.delay)
            yield {"type": "text", "text": chunk}
        yield {
            "type": "done", "text": "",
            "usage": result.get("usage", {}),
            "model": result.get("model", self.model),
            "function_calls": calls,
            "simulated": True,
        }

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "mock": True,
            "note": "Simulation mode: GEMINI_API_KEY is not set",
        }

    def _plan_tool_calls(
        self,
        tools: Sequence[dict[str, Any]] | None,
        user_text: str,
        messages: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """「ツールが使えるなら使う」という流れを決定的に再現する。"""
        if not tools:
            return []
        available = {t.get("name") for t in tools}
        # 直前がツール結果なら、もう呼ばない（無限ループ防止）
        for message in reversed(list(messages)):
            if message.get("parts") and any("functionResponse" in p for p in message["parts"]):
                return []
            break
        text = user_text or ""
        plan: list[dict[str, Any]] = []
        if "now" in available and re.search(r"(何時|今日|日付|曜日|current time|today)", text):
            plan.append({"name": "now", "args": {"timezone_name": "Asia/Tokyo"}})
        elif "list_skills" in available and re.search(r"(スキル|SKILL\.md).*(一覧|どんな|ある|list)", text):
            plan.append({"name": "list_skills", "args": {}})
        if not plan and "read_file" in available:
            match = re.search(r"([\w./-]+\.(?:md|txt|json|ya?ml|csv|py|js|ts|html|css))", text)
            if match:
                plan.append({"name": "read_file", "args": {"path": match.group(1)}})
        return plan[:2]

    # ---- 内部 ----
    def _thoughts(self, skills: list[dict[str, Any]], user_text: str) -> str:
        lines = ["Analyzing the request."]
        if skills:
            lines.append("Active skills: " + ", ".join(s["name"] for s in skills))
            for skill in skills:
                steps = _extract_steps(skill["instructions"])
                if steps:
                    lines.append(f"`{skill['name']}`: {len(steps)} procedure step(s) detected.")
        else:
            lines.append("No skills are active; generating a general response.")
        lines.append(f"Input length: {len(user_text)} characters. Selecting the output format from the skill instructions.")
        return "\n".join(lines)

    def _compose(self, skills: list[dict[str, Any]], user_text: str, messages: Sequence[dict[str, Any]]) -> str:
        turn = sum(1 for m in messages if m.get("role") in {"user", "assistant"}) or 1
        tool_notes = _tool_results_in(messages)
        out: list[str] = []
        out.append(
            "> ⚠️ **Simulation mode**: `GEMINI_API_KEY` is not set, so "
            "Actor is returning a rule-based preview instead of a live model response. "
            "Set the key in `.env` and restart the server to switch to a live Gemini model."
        )
        out.append("")
        if tool_notes:
            out.append("### Tool results")
            out.append("")
            for name, result in tool_notes:
                out.append(f"**`{name}`**")
                out.append("")
                out.append("```")
                out.append(result[:800])
                out.append("```")
                out.append("")
        if skills:
            names = " ".join(f"`{s['name']}`" for s in skills)
            out.append(f"### Applied skills\n{names}")
            out.append("")
            for skill in skills:
                steps = _extract_steps(skill["instructions"])
                if skill["description"]:
                    out.append(f"**{skill['name']}** — {skill['description']}")
                if steps:
                    out.append("")
                    out.append("Procedure steps:")
                    for i, step in enumerate(steps[:8], 1):
                        mark = "x" if i < len(steps) else " "
                        out.append(f"{i}. {step}")
                out.append("")
            out.append("### Draft output")
            out.append("")
            out.append(self._deliverable(skills, user_text, turn))
        else:
            out.append("### Response")
            out.append("")
            out.append(
                "No skills were selected, so this is a general response."
                f"Here is the content you provided.\n\n> {_first_lines(user_text, 6)}"
            )
            out.append("")
            out.append("Select a skill from the list on the left or describe the task in more detail to trigger automatic selection.")
        out.append("")
        out.append("---")
        out.append(
            f"*turn {turn} / skills: {len(skills)} / This is a preview from Actor. "
            "Configure an API key to get responses from Gemini.*"
        )
        return "\n".join(out)

    def _deliverable(self, skills: list[dict[str, Any]], user_text: str, turn: int) -> str:
        """スキルが定義した出力フォーマットと、入力の要点を突き合わせて提示する。"""
        primary = skills[0]
        template = _extract_template(primary["instructions"])
        origin = "SKILL.md output format"
        if not template:
            ref_path, ref_template = _extract_reference_template(primary.get("references_text", ""))
            if ref_template:
                template, origin = ref_template, f"reference `{ref_path}`"
        topic = _first_lines(user_text, 1) or "(No input provided)"
        lines: list[str] = [f"**Request**: {topic}", ""]

        if template:
            lines.append(f"{origin} (the live model fills in this template):")
            lines.append("")
            lines.append("```markdown")
            lines.append(template.strip())
            lines.append("```")
            lines.append("")
        else:
            for heading in _extract_headings(primary["instructions"])[:6]:
                lines.append(f"#### {heading}")
                lines.append(f"- {topic} from the perspective of `{primary['name']}`.")
                lines.append("")

        points = [line.strip("・-* \t") for line in user_text.splitlines() if line.strip()]
        lines.append("#### Key points from the input")
        if points:
            for point in points[:5]:
                lines.append(f"- {point[:120]}")
        else:
            lines.append("- (No input provided)")
        lines.append("")
        lines.append(
            f"Note: This preview was assembled by reading the procedure and output format for `{primary['name']}`. "
            f"Turn {turn}. Configure an API key for a live model to fill in this format."
        )
        return "\n".join(lines)


# ----------------------------------------------------------------------
# ファクトリ / ヘルパ
# ----------------------------------------------------------------------
def get_provider(settings: Settings | None = None, name: str | None = None) -> Provider:
    """プロバイダを解決する。

    ``auto``    : API キーがあれば ``GEMINI_PROVIDER``（既定 gemini）、無ければシミュレータ
    ``gemini``  : urllib 直叩き（追加依存なし・既定）
    ``genai``   : 公式 SDK ``google-genai``（未導入なら urllib 版へフォールバック）
    ``mock``    : オフラインシミュレータ
    """
    settings = settings or Settings()
    requested = (name or settings.effective_provider).lower()
    if requested == "mock":
        return MockProvider(settings)
    if requested in {"genai", "sdk", "google-genai", "google_genai"}:
        from .genai_provider import GenAiProvider, available

        if available():
            return GenAiProvider(settings)
        if settings.api_key:
            import sys

            print(
                "Warning: google-genai SDK not found; continuing with the urllib implementation"
                " (install google-genai with `pip install google-genai`)",
                file=sys.stderr,
            )
            return GeminiProvider(settings)
        return MockProvider(settings)
    if requested in {"gemini", "google", "aistudio"}:
        return GeminiProvider(settings)
    if requested == "auto":
        if not settings.api_key:
            return MockProvider(settings)
        configured = (os.environ.get("ACTOR_GEMINI_IMPL") or "gemini").lower()
        return get_provider(settings, configured)
    raise ValueError(f"Unknown provider: {requested}")


def _chunks(text: str, size: int) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i:i + size]


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _tool_results_in(messages: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    """contents に含まれる functionResponse を (ツール名, 結果) で取り出す。"""
    results: list[tuple[str, str]] = []
    for message in messages:
        for part in message.get("parts") or []:
            response = part.get("functionResponse")
            if not response:
                continue
            payload = response.get("response") or {}
            body = payload.get("result") if isinstance(payload, dict) else str(payload)
            results.append((str(response.get("name") or "tool"), str(body or "")))
    return results


def _last_user_text(messages: Sequence[dict[str, Any]]) -> str:
    for message in reversed(list(messages)):
        if message.get("role") in {None, "user"}:
            return str(message.get("content") or message.get("text") or "")
    return ""


def _extract_skills(system_text: str) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    for match in re.finditer(r'<skill name="([^"]+)">(.*?)</skill>', system_text, re.S):
        name, block = match.group(1), match.group(2)
        description = ""
        desc_match = re.search(r"^(?:Description|概要): (.+)$", block, re.M)
        if desc_match:
            description = desc_match.group(1).strip()
        block = re.sub(r"^(?:Description|概要): .+$", "", block, count=1, flags=re.M)
        parts = re.split(r"^## (?:Reference materials \(included with this skill\)|参照資料（スキルに同梱）)$", block, flags=re.M)
        instructions = parts[0].strip()
        references_text = parts[1].strip() if len(parts) > 1 else ""
        skills.append({
            "name": name,
            "description": description,
            "instructions": instructions,
            "references_text": references_text,
        })
    return skills


def _extract_reference_template(references_text: str) -> tuple[str, str]:
    """参照資料セクションから、最も有用なテンプレート（ファイル名, 中身）を取り出す。

    参照ファイル全体がフェンスで囲まれている場合は、その中の
    さらに内側のコードブロック（= 実際のテンプレート）を優先する。
    """
    best: tuple[str, str] = ("", "")
    for block in re.split(r"^### (?:Reference: |参照: )", references_text, flags=re.M)[1:]:
        path, _, rest = block.partition("\n")
        outer = fenced_blocks(rest)
        if not outer:
            continue
        body = max(outer, key=lambda item: len(item[1]))[1]
        inner = fenced_blocks(body)
        if inner:
            body = max(inner, key=lambda item: len(item[1]))[1]
        body = body.rstrip()
        if not body:
            continue
        preferred = any(word in path.lower() for word in ("format", "template", "フォーマット", "テンプレ"))
        if preferred or len(body) > len(best[1]):
            best = (path.strip(), body)
    return best


def _extract_steps(text: str) -> list[str]:
    """「手順」セクションの番号付き項目を優先して抽出し、無ければ箇条書きを使う。"""
    steps: list[str] = []
    fallback: list[str] = []
    in_procedure = False
    for line in text.splitlines():
        heading = re.match(r"^\s*#{1,4}\s+(.*\S)\s*$", line)
        if heading:
            in_procedure = any(word in heading.group(1) for word in ("手順", "やり方", "ステップ", "プロセス"))
            continue
        match = re.match(r"^\s*(?:(\d+)[.)]|[-*])\s+(.*\S)\s*$", line)
        if not match:
            continue
        step = match.group(2).strip()
        if len(step) > 140 or step.startswith("```"):
            continue
        (steps if in_procedure and match.group(1) else fallback).append(step)
    return steps or fallback


def _extract_headings(text: str) -> list[str]:
    headings = []
    for line in _strip_fences(text).splitlines():
        match = re.match(r"^\s*#{2,4}\s+(.*\S)\s*$", line)
        if match and "<" not in match.group(1):
            headings.append(match.group(1))
    return headings


def _strip_fences(text: str) -> str:
    """コードブロックの中身を落として、見出し抽出のノイズを減らす。"""
    stripped = re.sub(r"^`{3,}[^\n]*\n[\s\S]*?^`{3,}[ \t]*$", "", text, flags=re.M)
    return stripped


def _extract_template(text: str) -> str:
    """「出力フォーマット」セクションのフェンス内テンプレートを取り出す。

    該当セクションが無ければ、文書全体で最も大きいコードブロックを使う。
    """
    match = re.search(
        r"^#{1,4}\s*(?:出力フォーマット|出力形式|成果物)[^\n]*\n([\s\S]*?)(?=^#{1,4}\s|\Z)",
        text,
        re.M,
    )
    blocks = [body for _, body in fenced_blocks(match.group(1))] if match else []
    if not blocks:
        blocks = [body for _, body in fenced_blocks(text)]
    return max(blocks, key=len).rstrip() if blocks else ""


def _first_lines(text: str, count: int) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " / ".join(lines[:count])


def _normalize_response(raw: dict[str, Any], model: str) -> dict[str, Any]:
    texts: list[str] = []
    thoughts: list[str] = []
    function_calls: list[dict[str, Any]] = []
    for candidate in raw.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if part.get("functionCall"):
                function_calls.append(part["functionCall"])
            text = part.get("text")
            if not text:
                continue
            (thoughts if part.get("thought") else texts).append(text)
    blocked = raw.get("promptFeedback", {}).get("blockReason")
    return {
        "text": "".join(texts),
        "thoughts": "".join(thoughts),
        "function_calls": function_calls,
        "usage": _usage_of(raw.get("usageMetadata", {})),
        "model": raw.get("modelVersion", model),
        "finish_reason": (raw.get("candidates") or [{}])[0].get("finishReason", ""),
        "blocked": blocked,
        "raw": raw,
    }


def _usage_of(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "promptTokenCount": meta.get("promptTokenCount", 0),
        "candidatesTokenCount": meta.get("candidatesTokenCount", 0),
        "thoughtsTokenCount": meta.get("thoughtsTokenCount", 0),
        "cachedContentTokenCount": meta.get("cachedContentTokenCount", 0),
        "totalTokenCount": meta.get("totalTokenCount", 0),
    }


def _read_body(exc: urllib.error.HTTPError) -> bytes:
    """HTTP エラーの本文を読む（読めなくても落とさない）。"""
    try:
        return exc.read()
    except Exception:  # pragma: no cover
        return b""


def _extract_error(body: bytes) -> str:
    payload = _safe_json(body)
    if isinstance(payload, dict):
        error = payload.get("error", {})
        if isinstance(error, dict):
            return str(error.get("message", ""))
    return ""


def _safe_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None
