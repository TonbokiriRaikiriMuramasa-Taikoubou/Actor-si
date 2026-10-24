"""``google-genai``（公式 SDK）を使うプロバイダ。

このモジュールは **任意依存** です。SDK が入っていない環境でも Actor 本体は動くよう、
``actor.providers.get_provider`` から遅延インポートされます。

参考にしたくなる書き方::

    from google import genai
    from google.genai import types

    client = genai.Client()                      # GEMINI_API_KEY を自動で読む
    chat = client.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction=skill_text,       # SKILL.md の中身
            temperature=0.85,
        ),
    )
    response = chat.send_message("こんにちは。")
    print(response.text)

Actor では「ターンごとにスキル（system_instruction）とパラメータを差し替える」ため、
SDK の ``chats``（会話をクライアント側に持つ）ではなく、ステートレスな
``models.generate_content`` / ``generate_content_stream`` を使い、会話履歴は
``.actor/sessions/*.json`` に保持しています。system_instruction・temperature・
top_p・max_output_tokens・thinking_config の渡し方は上のコードと同じです。
"""

from __future__ import annotations

import base64
import itertools
from typing import Any, Iterator, Sequence

from .config import Settings
from .keys import KeyPool, mask_key, redact
from .providers import ROTATE_STATUSES, Provider, ProviderError, to_gemini_contents

__all__ = ["GenAiProvider", "available"]


def available() -> bool:
    """google-genai SDK が import できるか。"""
    try:
        import google.genai  # noqa: F401
    except Exception:
        return False
    return True


def sdk_version() -> str:
    try:
        import google.genai as sdk  # type: ignore

        return str(getattr(sdk, "__version__", ""))
    except Exception:
        return ""


class GenAiProvider(Provider):
    """公式 SDK 経由のプロバイダ（``--provider genai``）。

    429（Quota exceeded）では ``settings.key_pool()`` の次のキーへ、それでも全滅なら
    次のモデル（``ACTOR_MODEL_FALLBACKS``）へ切り替えて再試行する（:meth:`_run`）。
    ストリーミングは **まだ何も返していない** ときだけ切り替える
    （途中まで出した応答を混ぜないため）。
    """

    name = "genai"
    SDK_PACKAGE = "google-genai"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._client: Any = None      # 直近に使ったクライアント（後方互換）
        self._clients: dict[str, Any] = {}   # キーごとに 1 クライアント
        self._types: Any = None
        self._keys: KeyPool = settings.key_pool()

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

    # ------------------------------------------------------------------
    # 初期化（遅延）
    # ------------------------------------------------------------------
    def _api_key(self) -> str:
        key = self.keys.current or self.settings.api_key
        if not key:
            raise ProviderError("GEMINI_API_KEY（または --api-key）が必要です。")
        return key

    def _load(self, api_key: str | None = None) -> Any:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - 依存未導入
            raise ProviderError(
                "google-genai SDK が見つかりません。`pip install google-genai` を実行するか、"
                "追加依存なしの `--provider gemini` をお使いください。"
            ) from exc
        self._types = types
        key = api_key or self._api_key()
        client = self._clients.get(key)
        if client is not None:
            self._client = client
            return client
        http_options = types.HttpOptions(
            base_url=self.settings.base_url.rstrip("/"),
            api_version=(self.settings.api_version or "v1beta").strip("/"),
            timeout=int(self.settings.timeout * 1000),
        )
        client = genai.Client(api_key=key, http_options=http_options)
        self._clients[key] = client
        self._client = client
        return client

    # ------------------------------------------------------------------
    # 429 のときの切り替え（キー → モデル）
    # ------------------------------------------------------------------
    def _model_order(self, override: str | None = None) -> tuple[list[str], list[dict[str, Any]]]:
        """試すモデルの順番（キーの残っているモデルを優先）と通知。"""
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
                    "text": f"{chain[0]} は全キーが 429 のため {ready[0]} で試します",
                })
                chain = ready + rest
        return chain, notices

    def _run(self, invoke: Any, *, override: str | None = None
             ) -> tuple[Any, str, list[dict[str, Any]]]:
        """``invoke(model, client)`` を、キー → モデルの順で粘って実行する。

        戻り値: ``(結果, 実際に使ったモデル, 通知)``。全滅なら :class:`ProviderError`。
        """
        pool = self.keys
        models, notices = self._model_order(override)
        last_error: ProviderError | None = None
        for model in models:
            tried: list[str] = []
            while True:
                key = self._api_key()
                if key in tried:        # このモデルは全キー試した
                    break
                tried.append(key)
                try:
                    client = self._load(key)
                    result = invoke(model, client)
                except Exception as exc:
                    error = wrap_sdk_error(exc).redacted(pool.keys)
                    last_error = error
                    if self.settings.key_rotate and error.status in ROTATE_STATUSES:
                        if pool.rotate(key=key, model=model, status=error.status, reason=str(error),
                                       retry_after=error.retry_after):
                            continue
                        break           # このモデルは全キー制限 → 逃げ道へ
                    raise error from exc
                pool.mark_ok(key, model)
                return result, model, notices
            if model != models[-1]:
                next_model = models[models.index(model) + 1]
                notices.append({
                    "kind": "model_fallback",
                    "from": model,
                    "to": next_model,
                    "text": f"{model} で全キーが 429 → {next_model} に逃がします",
                })
        if last_error is None:  # pragma: no cover - ロジック上ほぼ起きない
            last_error = ProviderError("リクエストに失敗しました")
        if last_error.status in ROTATE_STATUSES and pool.size:
            raise ProviderError(
                pool.exhaustion_message(status=last_error.status, last_error=last_error, models=models),
                status=last_error.status, detail=last_error.detail,
                retry_after=last_error.retry_after,
            ) from last_error
        raise last_error

    def _open_stream(self, model: str, client: Any, messages: Sequence[dict[str, Any]],
                     generation_config: dict[str, Any] | None,
                     system_instruction: dict[str, Any] | None,
                     tools: list[dict[str, Any]] | None) -> Iterator[Any]:
        """ストリームを開き、**最初のチャンクを取るところまで**を試す（429 はここで分かる）。"""
        if hasattr(client.models, "generate_content_stream"):
            chunks = client.models.generate_content_stream(
                model=model,
                contents=self._to_contents(messages),
                config=self._to_config(generation_config, self._to_system(system_instruction), tools),
            )
        else:  # 古い SDK へのフォールバック
            chunks = [client.models.generate_content(
                model=model,
                contents=self._to_contents(messages),
                config=self._to_config(generation_config, self._to_system(system_instruction), tools),
            )]
        iterator = iter(chunks)
        try:
            first = next(iterator)
        except StopIteration:
            return iter(())
        return itertools.chain([first], iterator)

    # ------------------------------------------------------------------
    # 変換
    # ------------------------------------------------------------------
    def _parts_of(self, parts: Sequence[dict[str, Any]]) -> list[Any]:
        types = self._types
        out: list[Any] = []
        for part in parts:
            if "text" in part:
                out.append(types.Part(text=part["text"]))
            elif "inlineData" in part:
                blob = part["inlineData"]
                out.append(types.Part(inline_data=types.Blob(
                    mime_type=blob["mimeType"],
                    data=base64.b64decode(blob["data"]),
                )))
            elif "functionCall" in part:
                call = part["functionCall"]
                out.append(types.Part(function_call=types.FunctionCall(
                    name=call.get("name", ""), args=dict(call.get("args") or {})
                )))
            elif "functionResponse" in part:
                response = part["functionResponse"]
                out.append(types.Part(function_response=types.FunctionResponse(
                    name=response.get("name", ""), response=dict(response.get("response") or {})
                )))
        return out

    def _to_tools(self, tools: Sequence[dict[str, Any]] | None) -> list[Any]:
        """ツール宣言のリストを SDK の types.Tool へ変換する。"""
        if not tools:
            return []
        types = self._types
        declarations = tools[0].get("functionDeclarations", tools) if isinstance(tools[0], dict) else tools
        return [types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=d.get("name", ""),
                description=d.get("description", ""),
                parameters_json_schema=d.get("parameters") or {"type": "object", "properties": {}},
            )
            for d in declarations
        ])]

    def _to_contents(self, messages: Sequence[dict[str, Any]]) -> list[Any]:
        types = self._types
        return [
            types.Content(role=item["role"], parts=self._parts_of(item["parts"]))
            for item in to_gemini_contents(messages)
        ]

    def _to_system(self, system_instruction: dict[str, Any] | None) -> Any:
        if not system_instruction or not system_instruction.get("parts"):
            return None
        return self._types.Content(parts=self._parts_of(system_instruction["parts"]))

    def _to_config(self, generation_config: dict[str, Any] | None, system_instruction: Any,
                   tools: Sequence[dict[str, Any]] | None = None) -> Any:
        types = self._types
        config = dict(generation_config or self.settings.generation_config())
        kwargs: dict[str, Any] = {"system_instruction": system_instruction}
        tool_objects = self._to_tools(tools)   # tools は config に入れる（SDK の仕様）
        if tool_objects:
            kwargs["tools"] = tool_objects
        if config.get("temperature") is not None:
            kwargs["temperature"] = config["temperature"]
        if config.get("topP") is not None:
            kwargs["top_p"] = config["topP"]
        if config.get("maxOutputTokens"):
            kwargs["max_output_tokens"] = config["maxOutputTokens"]
        thinking = config.get("thinkingConfig") or {}
        if thinking.get("thinkingBudget"):
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking["thinkingBudget"],
                include_thoughts=bool(thinking.get("includeThoughts", True)),
            )
        return types.GenerateContentConfig(**kwargs)

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------
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
        if not messages:
            raise ProviderError("送信するメッセージがありません")
        requested = self._model(model)

        def invoke(name: str, client: Any) -> Any:
            return client.models.generate_content(
                model=name,
                contents=self._to_contents(messages),
                config=self._to_config(generation_config, self._to_system(system_instruction), tools),
            )

        response, used_model, notices = self._run(invoke, override=model)
        result = normalize_sdk_response(response, used_model)
        result["notices"] = notices
        if used_model != requested:
            result["requested_model"] = requested
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
        if not messages:
            raise ProviderError("送信するメッセージがありません")
        requested = self._model(model)
        usage: dict[str, Any] = {}
        try:
            chunks, current, notices = self._run(
                lambda name, client: self._open_stream(
                    name, client, messages, generation_config, system_instruction, tools
                ),
                override=model,
            )
        except ProviderError as exc:
            # 429 で全キー（全モデル）が使えないときはここに来る（キーは伏せ字・対処つきのメッセージ）
            yield {"type": "error", "text": str(exc), "status": exc.status}
            return
        for notice in notices:
            yield {"type": "notice", **notice}
        try:
            for chunk in chunks:
                normalized = normalize_sdk_response(chunk, current)
                if normalized["usage"].get("totalTokenCount"):
                    usage = normalized["usage"]
                if normalized["thoughts"]:
                    yield {"type": "thought", "text": normalized["thoughts"]}
                for call in normalized.get("function_calls") or []:
                    yield {"type": "function_call", "call": call}
                if normalized["text"]:
                    yield {"type": "text", "text": normalized["text"]}
        except ProviderError as exc:
            yield {"type": "error", "text": str(exc.redacted(self.keys.keys)), "status": exc.status}
            return
        except Exception as exc:
            error = wrap_sdk_error(exc).redacted(self.keys.keys)
            yield {"type": "error", "text": str(error), "status": error.status}
            return
        yield {"type": "done", "text": "", "usage": usage, "model": current,
               "requested_model": requested if current != requested else ""}

    def model_catalog(self, strict: bool = False) -> list[str]:
        """利用できるモデル名の一覧（``strict=True`` なら失敗時に例外を投げる）。"""
        try:
            client = self._load()
        except ProviderError:
            if strict:
                raise
            return []
        names: list[str] = []
        try:
            for model in client.models.list():
                name = str(getattr(model, "name", "")).replace("models/", "")
                actions = list(getattr(model, "supported_actions", None) or [])
                if name and (not actions or "generateContent" in actions):
                    names.append(name)
        except Exception as exc:
            if strict:
                raise wrap_sdk_error(exc).redacted(self.keys.keys) from exc
            return []
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
            "sdk": self.SDK_PACKAGE,
            "sdk_version": sdk_version(),
        }


def wrap_sdk_error(exc: Exception) -> ProviderError:
    """google-genai の例外を ProviderError（status つき）へ寄せる。"""
    if isinstance(exc, ProviderError):
        return exc
    raw_status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(raw_status, str) or not isinstance(raw_status, int):
        raw_status = _status_from_text(str(exc))
    message = str(exc) or type(exc).__name__
    return ProviderError(f"{type(exc).__name__}: {message}", status=raw_status, detail=message)


def _status_from_text(text: str) -> int | None:
    for code in (400, 401, 403, 404, 408, 429, 500, 502, 503, 504):
        if str(code) in text:
            return code
    return None


def normalize_sdk_response(response: Any, model: str) -> dict[str, Any]:
    """GenerateContentResponse を Actor 共通の dict に正規化する。"""
    texts: list[str] = []
    thoughts: list[str] = []
    function_calls: list[dict[str, Any]] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            call = getattr(part, "function_call", None)
            if call is not None:
                function_calls.append({
                    "name": getattr(call, "name", ""),
                    "args": dict(getattr(call, "args", None) or {}),
                })
            text = getattr(part, "text", None)
            if not text:
                continue
            (thoughts if getattr(part, "thought", False) else texts).append(text)
    if not texts and not thoughts and not function_calls:  # 関数呼び出しのときは .text に触らない（SDK が警告を出すため）
        try:
            fallback = getattr(response, "text", "") or ""
        except Exception:
            fallback = ""
        if fallback:
            texts.append(str(fallback))

    usage = getattr(response, "usage_metadata", None)
    return {
        "text": "".join(texts),
        "thoughts": "".join(thoughts),
        "function_calls": function_calls,
        "usage": {
            "promptTokenCount": getattr(usage, "prompt_token_count", 0) or 0,
            "candidatesTokenCount": getattr(usage, "candidates_token_count", 0) or 0,
            "thoughtsTokenCount": getattr(usage, "thoughts_token_count", 0) or 0,
            "cachedContentTokenCount": getattr(usage, "cached_content_token_count", 0) or 0,
            "totalTokenCount": getattr(usage, "total_token_count", 0) or 0,
        },
        "model": getattr(response, "model_version", "") or model,
        "raw": {"sdk": True},
    }
