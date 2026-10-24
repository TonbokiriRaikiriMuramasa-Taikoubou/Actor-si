"""Gemini API キーのプール（複数キーの保持・ローテーション・伏せ字・統計）。

プレビュー版のモデルは無料枠だと 429（ResourceExhausted / Quota exceeded）に当たりやすいため、
Actor は **複数のキーを優先順位つきで 1 本のリストとして持ち、429 が出たら自動で次へ切り替える**。

- 集める順番: ``--api-key`` > 環境変数 > ``.env`` > 保存ファイル > ``.actor/keys/``
- 単一キー（``GEMINI_API_KEY``）は今までどおり動く（後方互換）
- 複数キーは ``GEMINI_API_KEYS``（カンマ / 空白 / セミコロン区切り）や ``.actor/keys/*``
- 429 / 401 / 403 を受けたキーは ``cooldown`` 秒だけ「使わない」印を付ける
  （:meth:`KeyPool.rotate`）。全滅したら理由の分かる例外で止める
- **制限は「キー × モデル」で覚える**。Gemini の枠はモデルごとに別なので、
  ``gemini-3.8-flash`` で切れたキーも ``gemini-2.5-flash`` なら使える（＝モデルの逃げ道が効く）
- 429 の回数（``limits``）とモデル別の内訳をためて、:meth:`KeyPool.stats` で
  「よく切れるキー / よく切れるモデル」を出せる
- 生のキーは **状態ファイルにもログにも書かない**。伏せ字（:func:`mask_key`）と
  指紋（:func:`fingerprint`）だけを使う

このモジュールは標準ライブラリだけで完結する（``config`` からも ``providers`` からも使える）。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "MULTI_KEY_ENV",
    "SINGLE_KEY_ENVS",
    "GENERATED_KEY_GLOB",
    "KEY_SOURCES",
    "KeyEntry",
    "KeyPool",
    "collect_entries",
    "dedupe_entries",
    "entries_from_settings",
    "fingerprint",
    "key_sources",
    "mask_key",
    "parse_retry_delay",
    "pool_signature",
    "read_key_text",
    "redact",
    "split_keys",
    "state_path_for_settings",
]

#: 複数キーをまとめて入れる環境変数（カンマ / 空白 / セミコロン区切り）
MULTI_KEY_ENV = "GEMINI_API_KEYS"

#: 1 本だけ入れる従来の環境変数（この順に見る）
SINGLE_KEY_ENVS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")

#: ``actor key add`` が ``keys`` ディレクトリへ作るファイル名（``key clear`` の対象をこれに限定する）
GENERATED_KEY_GLOB = "key-*.key"

#: モデルに関係なく付けた印（古い状態ファイルからの移行など）
WILDCARD_MODEL = ""

#: キーを無料で用意できる場所（``actor key sources`` / docs/keys.md 用）
KEY_SOURCES: tuple[dict[str, str], ...] = (
    {
        "name": "Google AI Studio",
        "url": "https://aistudio.google.com/apikey",
        "kind": "無料枠",
        "note": "クレジットカード不要。Flash / Flash-Lite / 2.5 Pro などが無料枠で使える。"
                "枠は「プロジェクト × モデル」単位（キーを増やしても同じプロジェクトでは増えない）",
    },
    {
        "name": "Vertex AI（Gemini Enterprise Agent Platform）エクスプレスモード",
        "url": "https://console.cloud.google.com/expressmode",
        "kind": "90 日無料",
        "note": "新しい Google Cloud ユーザーは課金設定なしで 90 日無料（枠内）。API キーで使えて、"
                "ACTOR_BASE_URL=https://aiplatform.googleapis.com を設定すると Actor からそのまま使える",
    },
    {
        "name": "Google Cloud 無料トライアル",
        "url": "https://cloud.google.com/free/docs/free-cloud-features",
        "kind": "クレジット",
        "note": "新規顧客向け $300 / 90 日。課金設定（カード）が必要。Vertex AI 経由で使う",
    },
    {
        "name": "組織・Workspace の管理者",
        "url": "",
        "kind": "社内の枠",
        "note": "会社や学校の Google Workspace にプロジェクトがある場合、そこからキーを発行できることがある",
    },
    {
        "name": "Google for Startups / 研究・学生プログラム",
        "url": "https://cloud.google.com/startup",
        "kind": "クレジット",
        "note": "スタートアップ・研究者・学生向けのクレジットプログラム。条件と審査あり",
    },
)

#: やってはいけないこと（表示・ドキュメントで必ず一緒に出す）
KEY_SOURCES_CAUTION = (
    "注意: 複数アカウントを作って制限を回避するのは規約違反です（BAN され得ます）。"
    "また、無料枠では送信内容が Google の製品改善に使われ得るので、機密は送らないでください。"
)

#: 1 ファイルとして読む上限（壊れたファイルで固まらないように）
MAX_KEY_FILE_BYTES = 64 * 1024

_SPLIT_RE = re.compile(r"[\s,;]+")
_RETRY_DELAY_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)s")


# ----------------------------------------------------------------------
# 伏せ字・指紋・パース
# ----------------------------------------------------------------------
def mask_key(api_key: str) -> str:
    """``AIzaSy…abcd`` のように伏せる（ログ・画面表示はこれを使う）。"""
    if not api_key:
        return ""
    if len(api_key) <= 10:
        return "＊" * len(api_key)
    return f"{api_key[:6]}…{api_key[-4:]}"


def fingerprint(api_key: str) -> str:
    """キー本体の代わりに状態ファイルへ書く短い指紋（sha256 の先頭12文字）。"""
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:12]


def redact(text: str, secrets: Iterable[str]) -> str:
    """本文にキーが混ざっていても伏せ字に置き換える（API のエラーメッセージ対策）。"""
    out = str(text)
    for secret in secrets:
        secret = (secret or "").strip()
        if len(secret) >= 8 and secret in out:
            out = out.replace(secret, mask_key(secret))
    return out


def split_keys(value: str | None) -> list[str]:
    """``GEMINI_API_KEYS`` のような区切り文字つきの文字列をキーの配列にする。"""
    if not value:
        return []
    keys: list[str] = []
    for chunk in _SPLIT_RE.split(str(value)):
        token = chunk.strip().strip("\"'").strip()
        if token:
            keys.append(token)
    return keys


def read_key_text(path: Path) -> str:
    """キーを書いたファイルを読む（``#`` コメントと空行は無視・複数行 OK）。"""
    try:
        if not path.is_file() or path.stat().st_size > MAX_KEY_FILE_BYTES:
            return ""
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in raw.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return " ".join(lines)


def key_sources() -> list[dict[str, str]]:
    """キーを用意できる場所の一覧（表示用のコピー）。"""
    return [dict(item) for item in KEY_SOURCES]


# ----------------------------------------------------------------------
# 1 本のキー
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class KeyEntry:
    """キー 1 本と、その出どころ。"""

    value: str
    source: str = ""   # cli | env | .env | file | keys
    label: str = ""    # 表示名（環境変数名 / ファイル名など）
    path: str = ""     # 削除できる場合の置き場所（ファイル）

    @property
    def masked(self) -> str:
        return mask_key(self.value)

    @property
    def source_label(self) -> str:
        return self.label or {
            "cli": "--api-key",
            "env": "環境変数",
            ".env": ".env",
            "file": "保存ファイル",
            "keys": "keys ディレクトリ",
        }.get(self.source, self.source)

    def to_dict(self) -> dict[str, Any]:
        return {"masked": self.masked, "source": self.source, "label": self.source_label, "path": self.path}


def dedupe_entries(entries: Iterable[KeyEntry]) -> list[KeyEntry]:
    """同じキーが複数の場所にあっても 1 本にまとめる（先に見つかった方を優先）。"""
    seen: set[str] = set()
    out: list[KeyEntry] = []
    for entry in entries:
        value = (entry.value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(entry if entry.value == value else KeyEntry(value, entry.source, entry.label, entry.path))
    return out


def collect_entries(
    *,
    cli_key: str = "",
    env: Mapping[str, str] | None = None,
    dotenv: Mapping[str, str] | None = None,
    dotenv_path: Path | str | None = None,
    key_file: Path | None = None,
    keys_dir: Path | None = None,
) -> list[KeyEntry]:
    """優先順位どおりにキーを集める（重複は先勝ち）。

    1. ``--api-key``
    2. 環境変数 ``GEMINI_API_KEYS`` → ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` / ``GOOGLE_GENAI_API_KEY``
    3. ``.env`` の同じ変数（環境変数が優先なので、後ろに回す）
    4. 保存ファイル（``.actor/api_key``。1 行 1 キー・カンマ区切りも可）
    5. ``keys`` ディレクトリのファイル（``.actor/keys/key-1.key`` など）
    """
    env = dict(env or {})
    dotenv = dict(dotenv or {})
    dotenv_file = str(dotenv_path) if dotenv_path else ""
    entries: list[KeyEntry] = []

    def add(value: str | None, source: str, label: str, path: str = "") -> None:
        for token in split_keys(value):
            entries.append(KeyEntry(token, source, label, path))

    if cli_key:
        add(cli_key, "cli", "--api-key")
    add(env.get(MULTI_KEY_ENV), "env", MULTI_KEY_ENV)
    for name in SINGLE_KEY_ENVS:
        add(env.get(name), "env", name)
    add(dotenv.get(MULTI_KEY_ENV), ".env", MULTI_KEY_ENV, dotenv_file)
    for name in SINGLE_KEY_ENVS:
        add(dotenv.get(name), ".env", name, dotenv_file)
    if key_file is not None:
        add(read_key_text(Path(key_file)), "file", "", str(key_file))
    if keys_dir is not None:
        directory = Path(keys_dir)
        if directory.is_dir():
            for path in sorted(directory.iterdir(), key=lambda p: p.name):
                if path.name.startswith(".") or not path.is_file():
                    continue
                add(read_key_text(path), "keys", path.name, str(path))
    return dedupe_entries(entries)


def entries_from_settings(settings: Any) -> list[KeyEntry]:
    """``Settings`` からキーの一覧を組み立てる（先頭が ``api_key``・ファイルは読まない）。"""
    entries: list[KeyEntry] = [e for e in (getattr(settings, "key_entries", None) or []) if e.value]
    known = {entry.value for entry in entries}
    primary = (getattr(settings, "api_key", "") or "").strip()
    if primary and primary not in known:
        entries.insert(0, KeyEntry(primary, getattr(settings, "api_key_source", "") or "env", ""))
        known.add(primary)
    for key in getattr(settings, "api_keys", None) or []:
        key = (key or "").strip()
        if key and key not in known:
            entries.append(KeyEntry(key, "env", ""))
            known.add(key)
    return dedupe_entries(entries)


def state_path_for_settings(settings: Any) -> Path | None:
    """ローテーション状態の保存先（``<data_dir>/key_state.json``）。"""
    data_dir = getattr(settings, "data_dir", None)
    return (Path(data_dir) / "key_state.json") if data_dir else None


def pool_signature(entries: Iterable[KeyEntry], cooldown: float, state_path: Any,
                   model: str = "") -> tuple[Any, ...]:
    """キー構成の目印（キー本体は残さない）。設定が変わったときだけ作り直すために使う。"""
    return (tuple(fingerprint(entry.value) for entry in entries), float(cooldown),
            str(state_path) if state_path else "", (model or "").strip())


def parse_retry_delay(payload: Any) -> float | None:
    """``RetryInfo.retryDelay: "31s"`` のようなヒントを（再帰的に探して）秒へ直す。"""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in {"retryDelay", "retry_delay"} and isinstance(value, str):
                match = _RETRY_DELAY_RE.search(value)
                if match:
                    try:
                        return float(match.group(1))
                    except ValueError:  # pragma: no cover - 正規表現で担保
                        return None
            found = parse_retry_delay(value)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = parse_retry_delay(item)
            if found is not None:
                return found
    return None


# ----------------------------------------------------------------------
# プール
# ----------------------------------------------------------------------
@dataclass
class _KeyState:
    """1 本のキー × 1 モデルの状態（状態ファイルには指紋だけを書く）。"""

    limited_until: float = 0.0
    status: int | None = None
    reason: str = ""
    uses: int = 0
    errors: int = 0
    limits: int = 0          # 429（レート制限）で切り替えた回数
    last_used: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "limited_until": round(self.limited_until, 3),
            "status": self.status,
            "reason": self.reason,
            "uses": self.uses,
            "errors": self.errors,
            "limits": self.limits,
            "last_used": round(self.last_used, 3),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "_KeyState":
        def number(name: str, default: float = 0.0) -> float:
            try:
                return float(data.get(name) or default)
            except (TypeError, ValueError):
                return default

        status = data.get("status")
        return cls(
            limited_until=number("limited_until"),
            status=status if isinstance(status, int) else None,
            reason=str(data.get("reason") or "")[:200],
            uses=int(number("uses")),
            errors=int(number("errors")),
            limits=int(number("limits")),
            last_used=number("last_used"),
        )


class KeyPool:
    """複数のキーを順に使い、429 などで自動的に次へ切り替える（モデルごとに状態を分けて）。"""

    STATE_VERSION = 2

    def __init__(
        self,
        entries: Iterable[KeyEntry] = (),
        *,
        cooldown: float = 60.0,
        clock: Any = time.time,
        state_path: Path | str | None = None,
        max_cooldown: float = 900.0,
        model: str = "",
        fallback_models: Sequence[str] = (),
    ) -> None:
        self.entries: list[KeyEntry] = dedupe_entries(entries)
        self.cooldown = max(0.0, float(cooldown))
        self.max_cooldown = max(self.cooldown, float(max_cooldown))
        self.state_path = Path(state_path) if state_path else None
        self.model = (model or "").strip()
        self.fallback_models = [(name or "").strip() for name in fallback_models if (name or "").strip()]
        self.rotations = 0
        self.totals: dict[str, int] = {"requests": 0, "limits": 0, "rotations": 0}
        self.by_model: dict[str, dict[str, int]] = {}
        self.updated = 0.0
        self._clock = clock
        self._lock = threading.RLock()   # サーバーは複数スレッドから同じプールを使う
        self._states: dict[str, dict[str, _KeyState]] = {}   # 指紋 -> {モデル: 状態}
        self._index = 0
        self._load_state()
        self._index = self._first_ready_index()

    # ------------------------------------------------------------------
    # 構築
    # ------------------------------------------------------------------
    @classmethod
    def from_settings(cls, settings: Any) -> "KeyPool":
        """``Settings`` から作る（``api_key`` を先頭に、``api_keys`` を続ける）。"""
        return cls(
            entries_from_settings(settings),
            cooldown=float(getattr(settings, "key_cooldown", 60.0) or 0.0),
            state_path=state_path_for_settings(settings),
            model=getattr(settings, "model", "") or "",
            fallback_models=list(getattr(settings, "model_fallbacks", None) or []),
        )

    @classmethod
    def signature_for_settings(cls, settings: Any) -> tuple[Any, ...]:
        """その Settings から作ったプールの :attr:`signature`（設定が変わったかの判定用）。"""
        return pool_signature(
            entries_from_settings(settings),
            float(getattr(settings, "key_cooldown", 60.0) or 0.0),
            state_path_for_settings(settings),
            getattr(settings, "model", "") or "",
        )

    @property
    def signature(self) -> tuple[Any, ...]:
        """キー構成が変わったかを見るための目印（キー本体は含めない）。"""
        return pool_signature(self.entries, self.cooldown, self.state_path, self.model)

    @property
    def models(self) -> list[str]:
        """主モデル → 逃げ道（フォールバック）の順のモデル一覧。"""
        out: list[str] = []
        for name in [self.model, *self.fallback_models]:
            name = (name or "").strip()
            if name and name not in out:
                out.append(name)
        return out

    # ------------------------------------------------------------------
    # 参照（``model`` を省略すると主モデルの状態を見る）
    # ------------------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self.entries)

    @property
    def keys(self) -> list[str]:
        return [entry.value for entry in self.entries]

    @property
    def current_index(self) -> int:
        return self._index if self.entries else -1

    @property
    def current(self) -> str:
        return self.entries[self._index].value if self.entries else ""

    @property
    def current_entry(self) -> KeyEntry | None:
        return self.entries[self._index] if self.entries else None

    @property
    def enabled(self) -> bool:
        return bool(self.entries)

    def _bucket(self, key: str) -> dict[str, _KeyState]:
        return self._states.setdefault(fingerprint(key), {})

    def _peek(self, key: str, model: str | None) -> _KeyState | None:
        """状態を作らずに覗く（表示や判定で空の記録を増やさないため）。"""
        return self._states.get(fingerprint(key), {}).get((model or "").strip())

    def state_of(self, key: str, model: str | None = None) -> _KeyState:
        """そのキー × モデルの状態（無ければ作る）。"""
        bucket = self._bucket(key)
        name = (self.model if model is None else model) or ""
        return bucket.setdefault(name, _KeyState())

    def model_names(self, key: str) -> list[str]:
        """そのキーで記録があるモデル名（``""`` は「モデルを問わない」印）。"""
        return sorted(self._bucket(key))

    def limited_for(self, key: str | None = None, model: str | None = None) -> float:
        """あと何秒でそのキーが使えるようになるか（0 なら使える）。"""
        target = key if key is not None else self.current
        if not target:
            return 0.0
        now = float(self._clock())
        name = (self.model if model is None else model) or ""
        waits = [0.0]
        for candidate in {name, WILDCARD_MODEL}:
            state = self._peek(target, candidate)
            if state is not None:
                waits.append(state.limited_until - now)
        return max(0.0, max(waits))

    def is_ready(self, key: str | None = None, model: str | None = None) -> bool:
        target = key if key is not None else self.current
        return bool(target) and self.limited_for(target, model) <= 0.0

    def ready_indexes(self, model: str | None = None) -> list[int]:
        return [i for i, entry in enumerate(self.entries) if self.is_ready(entry.value, model)]

    @property
    def ready_count(self) -> int:
        """主モデルで使えるキーの数。"""
        return len(self.ready_indexes())

    def ready_count_for(self, model: str) -> int:
        return len(self.ready_indexes(model))

    def models_with_ready_keys(self) -> list[str]:
        """使えるキーが 1 本でもあるモデル（主モデル → 逃げ道の順）。"""
        return [name for name in self.models if self.ready_count_for(name) > 0]

    def next_available_in(self, model: str | None = None) -> float:
        """全滅しているとき、最短で復帰するまでの秒数（使えるキーがあれば 0）。"""
        waits = [self.limited_for(entry.value, model) for entry in self.entries]
        return min(waits) if waits else 0.0

    def _first_ready_index(self) -> int:
        for index, entry in enumerate(self.entries):
            if self.is_ready(entry.value):
                return index
        return 0

    # ------------------------------------------------------------------
    # 状態の更新
    # ------------------------------------------------------------------
    def _bump_model_totals(self, model: str, field: str) -> None:
        name = model or self.model or "(モデル不明)"
        bucket = self.by_model.setdefault(name, {"requests": 0, "limits": 0})
        bucket[field] = bucket.get(field, 0) + 1

    def mark_ok(self, key: str | None = None, model: str | None = None) -> None:
        """成功したキーの「制限中」を解除して使用回数を足す。"""
        target = key if key is not None else self.current
        if not target:
            return
        name = (self.model if model is None else model) or ""
        with self._lock:
            state = self.state_of(target, name)
            state.limited_until = 0.0
            state.status = None
            state.reason = ""
            state.uses += 1
            state.last_used = float(self._clock())
            self.totals["requests"] = self.totals.get("requests", 0) + 1
            self._bump_model_totals(name, "requests")
            self._save_state()

    def mark_limited(
        self,
        key: str,
        *,
        model: str | None = None,
        status: int | None = 429,
        reason: str = "",
        retry_after: float | None = None,
    ) -> float:
        """そのキー（そのモデル）を ``cooldown`` 秒だけ使わない印を付ける。"""
        name = (self.model if model is None else model) or ""
        with self._lock:
            state = self.state_of(key, name)
            wait = self.cooldown
            if retry_after:
                wait = min(max(wait, float(retry_after)), self.max_cooldown)
            state.limited_until = float(self._clock()) + max(0.0, wait)
            state.status = status
            state.reason = redact(reason, self.keys)[:200]
            state.errors += 1
            if status == 429:
                state.limits += 1
                self.totals["limits"] = self.totals.get("limits", 0) + 1
                self._bump_model_totals(name, "limits")
            state.last_used = float(self._clock())
            self._save_state()
        return max(0.0, wait)

    def mark_error(self, key: str, *, model: str | None = None,
                   status: int | None = None, reason: str = "") -> None:
        """制限ではないエラー（400 など）を記録する（切り替えの判断には使わない）。"""
        name = (self.model if model is None else model) or ""
        with self._lock:
            state = self.state_of(key, name)
            state.status = status
            state.reason = redact(reason, self.keys)[:200]
            state.errors += 1
            self._save_state()

    def rotate(
        self,
        *,
        key: str | None = None,
        model: str | None = None,
        status: int | None = 429,
        reason: str = "",
        retry_after: float | None = None,
        mark: bool = True,
    ) -> bool:
        """今のキーに見切りを付けて、次の使えるキーへ進む。

        戻り値: 進めたら ``True`` / 他に使えるキーが無ければ ``False``（＝このモデルは全滅）。
        """
        current = key if key is not None else self.current
        if mark and current:
            self.mark_limited(current, model=model, status=status, reason=reason, retry_after=retry_after)
        if not self.entries:
            return False
        with self._lock:
            for offset in range(1, len(self.entries) + 1):
                candidate = (self._index + offset) % len(self.entries)
                if self.is_ready(self.entries[candidate].value, model):
                    self._index = candidate
                    self.rotations += 1
                    self.totals["rotations"] = self.totals.get("rotations", 0) + 1
                    self._save_state()
                    return True
            return False

    def reset(self) -> None:
        """全部の「制限中」を消す（``ACTOR_KEY_COOLDOWN=0`` のときや手動リセット用）。"""
        with self._lock:
            for bucket in self._states.values():
                for state in bucket.values():
                    state.limited_until = 0.0
                    state.status = None
                    state.reason = ""
            self._index = 0
            self._save_state()

    # ------------------------------------------------------------------
    # 表示・メッセージ
    # ------------------------------------------------------------------
    def status(self, model: str | None = None) -> list[dict[str, Any]]:
        """``actor key list`` / ``/api/key`` 用の一覧（生のキーは含まれない）。

        ``ready`` / ``wait`` は指定モデル（既定は主モデル）の状態。
        モデルごとの詳細は ``models`` に入る（``{"<モデル>": {status, wait, limits}}``）。
        """
        name = (self.model if model is None else model) or ""
        items: list[dict[str, Any]] = []
        for index, entry in enumerate(self.entries):
            bucket = self._bucket(entry.value)
            wait = self.limited_for(entry.value, name)
            uses = sum(state.uses for state in bucket.values())
            errors = sum(state.errors for state in bucket.values())
            limits = sum(state.limits for state in bucket.values())
            last_used = max((state.last_used for state in bucket.values()), default=0.0)
            models_detail: dict[str, dict[str, Any]] = {}
            for key_name, state in bucket.items():
                if not (state.uses or state.errors or state.limits or state.limited_until):
                    continue          # 何も起きていない記録は出さない
                state_wait = self.limited_for(entry.value, key_name)
                models_detail[key_name or "(モデル指定なし)"] = {
                    "status": state.status,
                    "wait": round(state_wait, 1),
                    "limits": state.limits,
                    "limited": state_wait > 0,
                    "reason": state.reason,
                }
            limited_elsewhere = sorted(
                display for display, detail in models_detail.items()
                if detail["limited"] and display not in {name or "(モデル指定なし)"}
            )
            items.append({
                **entry.to_dict(),
                "index": index,
                "active": index == self._index,
                "ready": wait <= 0.0,
                "wait": round(wait, 1),
                "status": bucket.get(name, _KeyState()).status,
                "reason": bucket.get(name, _KeyState()).reason,
                "uses": uses,
                "errors": errors,
                "limits": limits,
                "last_used": last_used,
                "model": name,
                "models": models_detail,
                "limited_models": limited_elsewhere,
            })
        return items

    def stats(self, model: str | None = None) -> dict[str, Any]:
        """利用統計（``actor key stats`` 用）。429 でよく切れるキー・モデルが分かる。

        キーの本体は含めない（伏せ字と出どころだけ）。新しいキーほど試行回数が
        少ないので、率だけでなく回数も返す。
        """
        items: list[dict[str, Any]] = []
        for item in self.status(model):
            attempts = item["uses"] + item["errors"]
            by_model = {
                name: {"limits": state.limits, "uses": state.uses}
                for name, state in sorted(
                    ((name or "(モデル指定なし)", state)
                     for name, state in self._bucket(self.entries[item["index"]].value).items()),
                    key=lambda pair: pair[0],
                )
            }
            limits_by_model = {name: detail["limits"] for name, detail in by_model.items() if detail["limits"]}
            items.append({
                **item,
                "attempts": attempts,
                "limit_rate": round(item["limits"] / attempts, 3) if attempts else 0.0,
                "error_rate": round(item["errors"] / attempts, 3) if attempts else 0.0,
                "limits_by_model": limits_by_model,
                "worst_model": (max(limits_by_model, key=lambda name: limits_by_model[name])
                                if limits_by_model else ""),
            })
        # よく切れる順（429 の回数 → 失敗 → 試行回数の少ない順）
        ranked = sorted(items, key=lambda i: (-i["limits"], -i["errors"], i["attempts"]))
        known = {fingerprint(entry.value) for entry in self.entries}
        recorded = set(self._states)
        return {
            "totals": dict(self.totals),
            "by_model": {name: dict(values) for name, values in sorted(self.by_model.items())},
            "rotations": self.rotations,
            "count": self.size,
            "ready": self.ready_count,
            "ready_by_model": {name: self.ready_count_for(name) for name in self.models},
            "models": self.models,
            "model": self.model,
            "cooldown": self.cooldown,
            "updated": self.updated,
            "keys": items,
            "worst": ranked[0] if ranked else None,
            "stale_records": len(recorded - known),   # 消したキーの記録（指紋のみ）
            "state_path": str(self.state_path) if self.state_path else "",
        }

    def reset_stats(self) -> None:
        """統計と「制限中」の印をまとめて消す（``actor key stats --reset``）。"""
        with self._lock:
            for bucket in self._states.values():
                for state in bucket.values():
                    state.uses = 0
                    state.errors = 0
                    state.limits = 0
                    state.limited_until = 0.0
                    state.status = None
                    state.reason = ""
            self.totals = {"requests": 0, "limits": 0, "rotations": 0}
            self.by_model = {}
            self.rotations = 0
            self._index = 0
            self._save_state()

    def summary(self, model: str | None = None) -> str:
        """``3/5 本が使用可能`` のような 1 行。モデルを渡せばそのモデルの数になる。"""
        if not self.entries:
            return "キー未設定"
        name = (self.model if model is None else model) or ""
        return f"{self.ready_count_for(name)}/{self.size} 本が使用可能"

    def model_summary(self) -> list[tuple[str, str]]:
        """モデルごとの ``(モデル名, 何本使えるか)``（逃げ道があるときの表示用）。"""
        return [(name, self.summary(name)) for name in self.models]

    def exhaustion_message(self, *, status: int | None = None, last_error: Any = None,
                           models: Sequence[str] | None = None) -> str:
        """全キー（全モデル）が使えないときの、原因と対処が分かるメッセージ（キーは伏せ字）。"""
        tried = [name for name in (models or self.models) if name] or [self.model]
        count = self.size
        reason = ""
        if last_error is not None and str(last_error):
            first_line = redact(str(last_error), self.keys).splitlines()[0]
            reason = f" / 最後のエラー: {first_line}"
        tried_label = " / ".join(tried) if tried else "モデル不明"
        lines = [f"すべての Gemini API キーが使えません（{count} 本すべて・試したモデル: {tried_label}{reason}）"]
        for index, entry in enumerate(self.entries):
            parts: list[str] = []
            for name in tried:
                wait = self.limited_for(entry.value, name)
                if wait > 0:
                    parts.append(f"{name}: あと {wait:.0f} 秒")
            detail = " / ".join(parts) if parts else "使用不可"
            lines.append(f"  [{index}] {entry.masked}（{entry.source_label}）{detail}")
        wait_all = self.next_available_in()
        if wait_all > 0:
            lines.append(f"対処: {wait_all:.0f} 秒ほど待つか、別のキーを足してください（`python3 -m actor key add`）")
        else:
            lines.append("対処: 別のキーを足してください（`python3 -m actor key add`）")
        if status == 429:
            lines.append("ヒント: Gemini の枠は「プロジェクト × モデル」単位です。"
                         "キーを増やす（`actor key sources` に無料の用意先）か、"
                         "別モデルへ逃がす `ACTOR_MODEL_FALLBACKS=<モデル名>` を使ってください。")
        lines.append("状態の確認: `python3 -m actor key list` / 統計: `python3 -m actor key stats`")
        return "\n".join(lines)

    def health(self) -> dict[str, Any]:
        return {
            "count": self.size,
            "ready": self.ready_count,
            "active_index": self.current_index,
            "active_masked": self.current_entry.masked if self.current_entry else "",
            "rotations": self.rotations,
            "cooldown": self.cooldown,
            "model": self.model,
            "models": self.models,
            "ready_by_model": {name: self.ready_count_for(name) for name in self.models},
        }

    # ------------------------------------------------------------------
    # 状態ファイル（生のキーは書かない）
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        if not self.state_path or not self.state_path.is_file():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        totals = payload.get("totals")
        if isinstance(totals, dict):
            for name in ("requests", "limits", "rotations"):
                try:
                    self.totals[name] = int(totals.get(name) or 0)
                except (TypeError, ValueError):
                    self.totals[name] = 0
        by_model = payload.get("by_model")
        if isinstance(by_model, dict):
            for name, values in by_model.items():
                if not isinstance(values, dict):
                    continue
                bucket = {"requests": 0, "limits": 0}
                for field in bucket:
                    try:
                        bucket[field] = int(values.get(field) or 0)
                    except (TypeError, ValueError):
                        bucket[field] = 0
                self.by_model[str(name)] = bucket
        try:
            self.updated = float(payload.get("updated") or 0.0)
        except (TypeError, ValueError):
            self.updated = 0.0
        states = payload.get("keys")
        if not isinstance(states, dict):
            return
        for print_key, data in states.items():
            if not isinstance(data, dict):
                continue
            bucket = self._states.setdefault(str(print_key), {})
            models = data.get("models")
            if isinstance(models, dict):          # version 2 の形
                for model_name, state in models.items():
                    if isinstance(state, dict):
                        bucket[str(model_name)] = _KeyState.from_dict(state)
            elif "limited_until" in data:          # version 1 の形（モデルを問わない印として移行）
                bucket[WILDCARD_MODEL] = _KeyState.from_dict(data)

    def _save_state(self) -> None:
        if not self.state_path:
            return
        now = round(float(self._clock()), 3)
        self.updated = now
        payload = {
            "version": self.STATE_VERSION,
            "updated": now,
            "totals": dict(self.totals),
            "by_model": {name: dict(values) for name, values in self.by_model.items()},
            "keys": {
                print_key: {"models": {name: state.to_dict() for name, state in sorted(bucket.items())}}
                for print_key, bucket in self._states.items()
            },
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.state_path.chmod(0o600)
        except OSError:  # pragma: no cover - 読み取り専用など
            pass
