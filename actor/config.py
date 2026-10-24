"""設定の読み込み（環境変数 + .env ファイル + CLI 引数）。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from .keys import (
    MULTI_KEY_ENV,
    SINGLE_KEY_ENVS,
    KeyEntry,
    KeyPool,
    collect_entries,
    mask_key,
    split_keys,
)

# プロジェクトルート（actor/ の親）
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-3-flash"

# キーが未設定のときに UI / CLI で提示する候補
KNOWN_MODELS = [
    "gemini-3-pro",
    "gemini-3-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]

DEFAULT_TOOLS = ["now", "read_file", "list_files", "list_skills", "read_skill"]

# 429（枠切れ）のときだけ使う逃げ道。空文字（``ACTOR_MODEL_FALLBACKS=``）で無効化できる。
DEFAULT_MODEL_FALLBACKS = ["gemini-3.5-flash", "gemini-2.5-flash"]

# Vertex AI エクスプレスモード（API キーだけで使える別枠）を検出したときのパス
VERTEX_HOST = "aiplatform.googleapis.com"
VERTEX_API_VERSION = "v1"
VERTEX_MODEL_PATH = "publishers/google/models/{model}"
DEFAULT_MODEL_PATH = "models/{model}"

DEFAULT_PREAMBLE = """あなたは「Actor」という名前の、SKILL.md 駆動のアシスタントです。
ユーザーの依頼に対して、適用中のスキル（作業手順書）の指示に厳密に従って回答します。
スキルの指示は「その作業の正しいやり方」を定めたものです。指示と一般的な慣習が食い違う場合は、
スキルの指示を優先してください。ただし、ユーザーの明示的な要求がスキルの指示より優先されます。
適用中のスキルが無い場合は、通常の有能なアシスタントとして振る舞ってください。
回答は日本語で、簡潔かつ実務的に。冗長な前置きや過剰な敬語の連発は避けてください。"""


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _int(value: Any, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_env_file(path: Path) -> dict[str, str]:
    """`.env` を素朴にパースする（KEY=VALUE / export 対応 / # コメント）。"""
    data: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key] = value
    return data


@dataclass
class Settings:
    """Actor 全体の設定。"""

    # --- Gemini API ---
    api_key: str = field(default="", repr=False)    # 実際に使うキー（= 一覧の先頭）
    api_keys: list[str] = field(default_factory=list, repr=False, compare=False)  # 予備を含む全キー
    key_entries: list[KeyEntry] = field(default_factory=list, repr=False, compare=False)
    api_key_file: Path | None = None   # UI / CLI から保存したキーの置き場
    api_key_source: str = ""           # cli | env | .env | file | keys | ""
    cli_api_key: str = field(default="", repr=False)  # --api-key（最優先・消さない）
    env_file: Path | None = None       # .env のパス（キーの読み直しに使う）
    keys_dir: Path | None = None       # 複数キーを置くフォルダ（.actor/keys）
    key_cooldown: float = 60.0         # 429 のあと、そのキーを避ける秒数
    key_rotate: bool = True            # 429 などで次のキーへ自動で切り替える
    model_fallbacks: list[str] = field(default_factory=list)  # 429 のときだけ使う逃げ道モデル
    api_version: str = "v1beta"        # v1beta（AI Studio）/ v1（Vertex など）
    model_path: str = DEFAULT_MODEL_PATH  # URL のパス（Vertex は publishers/google/models/{model}）
    provider: str = "auto"  # auto | gemini | genai | mock
    gemini_impl: str = "gemini"  # APIキーがあるときの実装: gemini(urllib) | genai(公式SDK)
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 120.0

    # --- 生成パラメータ ---
    temperature: float = 0.7
    top_p: float = 0.95
    max_output_tokens: int = 4096
    thinking_budget: int = 0  # 0 = 無効（思考させない）
    stream: bool = True

    # --- スキル ---
    skills_dirs: list[Path] = field(default_factory=lambda: [PROJECT_ROOT / "skills"])
    auto_select: bool = True
    max_auto_skills: int = 3
    auto_select_threshold: float = 2.0
    inline_reference_limit: int = 24_000  # system_instruction に埋め込む参照ファイルの上限(文字)
    preamble: str = DEFAULT_PREAMBLE

    # --- ツール（function calling） ---
    tools: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    tools_root: Path = field(default_factory=lambda: Path.cwd())
    tools_allow_write: bool = False
    tools_allow_network: bool = False
    tools_timeout: float = 15.0
    max_tool_rounds: int = 4

    # --- 添付ファイル（画像 / PDF） ---
    max_attachment_bytes: int = 10 * 1024 * 1024
    max_attachments: int = 6
    store_attachments: bool = True

    # --- ルームの認証 ---
    room_token: str = ""
    room_protect_reads: bool = False
    cors_origins: list[str] = field(default_factory=list)
    allowed_hosts: list[str] = field(default_factory=list)

    # --- Webhook（メッセージを外へ通知） ---
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_events: list[str] = field(default_factory=lambda: ["message"])
    webhook_timeout: float = 10.0

    # --- データ / サーバ ---
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / ".actor")
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "info"

    # ------------------------------------------------------------------
    @property
    def api_key_count(self) -> int:
        """登録されているキーの本数（先頭の 1 本だけでも 1 と数える）。"""
        if self.api_keys:
            return len(self.api_keys)
        return 1 if self.api_key else 0

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def keys_path(self) -> Path:
        """複数キーを置くフォルダ（``ACTOR_KEYS_DIR``、既定は ``<data_dir>/keys``）。"""
        return Path(self.keys_dir) if self.keys_dir else (Path(self.data_dir) / "keys")

    def key_pool(self) -> KeyPool:
        """キーのプール（プロセス内で共有・ローテーションの状態もここに持つ）。"""
        cached = getattr(self, "_key_pool", None)
        if cached is None or cached.signature != KeyPool.signature_for_settings(self):
            cached = KeyPool.from_settings(self)
            self._key_pool = cached
        return cached

    @property
    def effective_provider(self) -> str:
        """実際に使われるプロバイダ名。auto の場合はキーの有無と実装設定で決まる。"""
        if self.provider == "auto":
            if not self.api_key:
                return "mock"
            return "genai" if self.gemini_impl in {"genai", "sdk", "google-genai", "google_genai"} else "gemini"
        return self.provider

    @property
    def is_demo(self) -> bool:
        return self.effective_provider == "mock"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    def model_dir(self) -> Path:
        return self.data_dir

    def generation_config(self, **overrides: Any) -> dict[str, Any]:
        """generationConfig を組み立てる。"""
        cfg: dict[str, Any] = {
            "temperature": _float(overrides.get("temperature"), self.temperature),
            "topP": _float(overrides.get("top_p"), self.top_p),
            "maxOutputTokens": _int(overrides.get("max_output_tokens"), self.max_output_tokens),
        }
        budget = _int(overrides.get("thinking_budget"), self.thinking_budget) or 0
        if budget > 0:
            cfg["thinkingConfig"] = {"thinkingBudget": budget, "includeThoughts": True}
        return cfg

    def to_dict(self, *, reveal_key: bool = False) -> dict[str, Any]:
        return {
            "provider": self.effective_provider,
            "provider_setting": self.provider,
            "gemini_impl": self.gemini_impl,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": (mask_key(self.api_key) if (reveal_key and self.api_key) else bool(self.api_key)),
            "has_api_key": bool(self.api_key),
            "api_key_source": self.api_key_source,
            "api_key_file": str(self.api_key_file) if self.api_key_file else "",
            "api_key_count": self.api_key_count,
            "api_keys": [entry.to_dict() for entry in self.key_entries],
            "keys_dir": str(self.keys_path),
            "key_cooldown": self.key_cooldown,
            "key_rotate": self.key_rotate,
            "model_fallbacks": list(self.model_fallbacks),
            "api_version": self.api_version,
            "model_path": self.model_path,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_output_tokens": self.max_output_tokens,
            "thinking_budget": self.thinking_budget,
            "stream": self.stream,
            "skills_dirs": [str(p) for p in self.skills_dirs],
            "auto_select": self.auto_select,
            "max_auto_skills": self.max_auto_skills,
            "auto_select_threshold": self.auto_select_threshold,
            "data_dir": str(self.data_dir),
            "host": self.host,
            "port": self.port,
            "demo": self.is_demo,
            "tools": list(self.tools),
            "tools_root": str(self.tools_root),
            "tools_allow_write": self.tools_allow_write,
            "tools_allow_network": self.tools_allow_network,
            "max_tool_rounds": self.max_tool_rounds,
            "max_attachments": self.max_attachments,
            "max_attachment_mb": round(self.max_attachment_bytes / (1024 * 1024), 1),
            "room_token_required": bool(self.room_token),
            "room_protect_reads": self.room_protect_reads,
            "webhook_url": self.webhook_url,
            "webhook_events": list(self.webhook_events),
        }

    def with_overrides(self, **kw: Any) -> "Settings":
        """空でない値だけを上書きしたコピーを返す（CLI 引数用）。"""
        clean = {k: v for k, v in kw.items() if v is not None}
        return replace(self, **clean) if clean else self


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace(",", " ").split() if item.strip()]


def _split_dirs(value: str | None) -> list[Path]:
    if not value:
        return []
    parts: Iterable[str] = value.replace(",", os.pathsep).split(os.pathsep)
    return [Path(p).expanduser() for p in (part.strip() for part in parts) if p]


def write_api_key(settings: "Settings", api_key: str) -> Path:
    """API キーを ``settings.api_key_file`` に保存する（パーミッション 600）。"""
    path = settings.api_key_file or (settings.data_dir / "api_key")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(api_key.strip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - Windows など
        pass
    return path


def clear_api_key(settings: "Settings") -> bool:
    """保存済みの API キーを削除する。"""
    path = settings.api_key_file or (settings.data_dir / "api_key")
    if path.is_file():
        path.unlink()
        return True
    return False


def write_keys_dir(settings: "Settings", keys: Iterable[str]) -> list[Path]:
    """``keys`` ディレクトリへ 1 本 1 ファイルで保存する（``key-1.key`` 形式・chmod 600）。"""
    directory = settings.keys_path
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:  # pragma: no cover - Windows など
        pass
    used = {path.name for path in directory.glob("*")}
    written: list[Path] = []
    index = 1
    for key in keys:
        key = (key or "").strip()
        if not key:
            continue
        while f"key-{index}.key" in used:
            index += 1
        path = directory / f"key-{index}.key"
        used.add(path.name)
        path.write_text(key + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover
            pass
        written.append(path)
    return written


# ``GEMINI_API_KEYS=...`` / ``export GEMINI_API_KEY=...`` / 先頭 # コメント を見分ける
_ENV_LINE_RE = re.compile(r"^(?P<head>\s*(?:export\s+)?)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*)$")


def _env_lines_for(lines: list[str], name: str) -> list[tuple[int, "re.Match[str]"]]:
    """その変数を代入している行（後ろの行が優先される = parse_env_file と同じ）を返す。"""
    found: list[tuple[int, "re.Match[str]"]] = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        match = _ENV_LINE_RE.match(line)
        if match and match.group("name") == name:
            found.append((index, match))
    return found


def _write_env_file(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - Windows など
        pass
    return path


def append_key_to_env_file(settings: "Settings", api_key: str, *,
                           var: str = MULTI_KEY_ENV) -> Path:
    """``.env`` の ``GEMINI_API_KEYS`` に 1 本追記する（``actor key add --env``）。

    - すでに同じキーがあれば何もしない（重複しない）
    - ``export `` や前後のコメント・他の変数はそのまま残す
    - 権限は 600 にする（キーが入るため）
    """
    api_key = (api_key or "").strip()
    path = Path(settings.env_file or (PROJECT_ROOT / ".env"))
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        text = ""
    lines = text.splitlines()
    matches = _env_lines_for(lines, var)
    if matches:
        index, match = matches[-1]
        values = split_keys(match.group("value"))
        if api_key not in values:
            values.append(api_key)
        lines[index] = f"{match.group('head')}{var}={','.join(values)}"
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{var}={api_key}")
    return _write_env_file(path, lines)


def remove_key_from_env_file(settings: "Settings", api_key: str, *,
                             var: str | None = None) -> bool:
    """``.env`` から 1 本だけ消す（``actor key rm``）。消せたら ``True``。

    その変数が空になったら ``GEMINI_API_KEYS=`` として行は残す
    （コメントや並び順を壊さないため）。
    """
    api_key = (api_key or "").strip()
    path = Path(settings.env_file or (PROJECT_ROOT / ".env"))
    if not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for name in ([var] if var else [MULTI_KEY_ENV, *SINGLE_KEY_ENVS]):
        for index, match in reversed(_env_lines_for(lines, name)):
            values = split_keys(match.group("value"))
            if api_key not in values:
                continue
            remaining = [value for value in values if value != api_key]
            lines[index] = f"{match.group('head')}{name}={','.join(remaining)}"
            _write_env_file(path, lines)
            return True
    return False


def list_generated_key_files(settings: "Settings") -> list[Path]:
    """``key add`` が作ったファイルだけを返す（``key clear`` の対象）。"""
    directory = settings.keys_path
    if not directory.is_dir():
        return []
    from .keys import GENERATED_KEY_GLOB

    return sorted(path for path in directory.glob(GENERATED_KEY_GLOB) if path.is_file())


def apply_key_entries(settings: "Settings", entries: Iterable[KeyEntry]) -> "Settings":
    """集めたキー一覧を Settings へ反映する（先頭が実際に使うキー）。"""
    settings.key_entries = list(entries)
    settings.api_keys = [entry.value for entry in settings.key_entries]
    settings.api_key = settings.api_keys[0] if settings.api_keys else ""
    settings.api_key_source = settings.key_entries[0].source if settings.key_entries else ""
    return settings


def refresh_keys(settings: "Settings", *, env: Mapping[str, str] | None = None) -> "Settings":
    """キーの出どころ（環境変数 / .env / 保存ファイル / keys ディレクトリ）を読み直す。

    UI からキーを足した・消したときに、``settings.api_keys`` をその場で作り直すために使う。
    ``--api-key``（``settings.cli_api_key``）は最優先のまま残す。
    """
    dotenv: dict[str, str] = {}
    if settings.env_file and Path(settings.env_file).is_file():
        dotenv = parse_env_file(Path(settings.env_file))
    entries = collect_entries(
        cli_key=settings.cli_api_key,
        env=dict(os.environ if env is None else env),
        dotenv=dotenv,
        dotenv_path=settings.env_file,
        key_file=settings.api_key_file,
        keys_dir=settings.keys_path,
    )
    apply_key_entries(settings, entries)
    settings._key_pool = None   # 次の key_pool() で作り直す
    return settings


def load_settings(
    env: Mapping[str, str] | None = None,
    env_file: Path | str | None = None,
    **overrides: Any,
) -> Settings:
    """環境変数 → .env → 引数の順でマージして Settings を作る。

    優先度: 引数 > 環境変数 > .env
    """
    file_map: dict[str, str] = {}
    candidate = Path(env_file).expanduser() if env_file else PROJECT_ROOT / ".env"
    if candidate.is_file():
        file_map.update(parse_env_file(candidate))
    env_map: dict[str, str] = dict(file_map)
    env_map.update(dict(os.environ if env is None else env))

    skills_dirs = _split_dirs(env_map.get("ACTOR_SKILLS_DIR")) or [PROJECT_ROOT / "skills"]
    data_dir = Path(env_map.get("ACTOR_DATA_DIR", str(PROJECT_ROOT / ".actor"))).expanduser()

    process_env = dict(os.environ if env is None else env)
    cli_key = str(overrides.get("api_key") or "").strip()
    api_key_file = Path(
        env_map.get("ACTOR_API_KEY_FILE") or (data_dir / "api_key")
    ).expanduser()
    keys_dir = Path(env_map.get("ACTOR_KEYS_DIR") or (data_dir / "keys")).expanduser()
    base_url = (env_map.get("ACTOR_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    # Vertex AI エクスプレスモード（aiplatform.googleapis.com）なら API の形が違うので自動で合わせる
    vertex_style = VERTEX_HOST in base_url
    api_version = (env_map.get("ACTOR_API_VERSION") or "").strip() or (
        VERTEX_API_VERSION if vertex_style else Settings.__dataclass_fields__["api_version"].default
    )
    model_path = (env_map.get("ACTOR_MODEL_PATH") or "").strip() or (
        VERTEX_MODEL_PATH if vertex_style else DEFAULT_MODEL_PATH
    )
    # 未設定（None）なら既定の逃げ道、空文字なら「無効」として扱う
    fallback_raw = env_map.get("ACTOR_MODEL_FALLBACKS")
    model_fallbacks = list(DEFAULT_MODEL_FALLBACKS) if fallback_raw is None else _split_list(fallback_raw)

    # 優先順位: --api-key > 環境変数 > .env > 保存ファイル > keys ディレクトリ
    entries = collect_entries(
        cli_key=cli_key,
        env=process_env,
        dotenv=file_map,
        dotenv_path=candidate,
        key_file=api_key_file,
        keys_dir=keys_dir,
    )
    api_key = entries[0].value if entries else ""
    api_key_source = entries[0].source if entries else ""

    settings = Settings(
        api_key=api_key,
        api_keys=[entry.value for entry in entries],
        key_entries=entries,
        api_key_file=api_key_file,
        api_key_source=api_key_source,
        cli_api_key=cli_key,
        env_file=candidate,
        keys_dir=keys_dir,
        key_cooldown=_float(env_map.get("ACTOR_KEY_COOLDOWN"), 60.0) or 0.0,
        key_rotate=_bool(env_map.get("ACTOR_KEY_ROTATE"), True),
        model_fallbacks=model_fallbacks,
        api_version=api_version,
        model_path=model_path,
        provider=(env_map.get("ACTOR_PROVIDER") or "auto").strip().lower(),
        gemini_impl=(env_map.get("ACTOR_GEMINI_IMPL") or "gemini").strip().lower(),
        model=(env_map.get("ACTOR_MODEL") or DEFAULT_MODEL).strip(),
        base_url=base_url,
        timeout=_float(env_map.get("ACTOR_TIMEOUT"), 120.0) or 120.0,
        temperature=_float(env_map.get("ACTOR_TEMPERATURE"), 0.7),
        top_p=_float(env_map.get("ACTOR_TOP_P"), 0.95),
        max_output_tokens=_int(env_map.get("ACTOR_MAX_OUTPUT_TOKENS"), 4096) or 4096,
        thinking_budget=_int(env_map.get("ACTOR_THINKING_BUDGET"), 0) or 0,
        stream=_bool(env_map.get("ACTOR_STREAM"), True),
        skills_dirs=skills_dirs,
        auto_select=_bool(env_map.get("ACTOR_AUTO_SELECT"), True),
        max_auto_skills=_int(env_map.get("ACTOR_MAX_AUTO_SKILLS"), 3) or 3,
        auto_select_threshold=_float(env_map.get("ACTOR_AUTO_SELECT_THRESHOLD"), 2.0),
        inline_reference_limit=_int(env_map.get("ACTOR_INLINE_REFERENCE_LIMIT"), 24_000) or 24_000,
        preamble=(env_map.get("ACTOR_PREAMBLE") or DEFAULT_PREAMBLE),
        data_dir=data_dir,
        tools=_split_list(env_map.get("ACTOR_TOOLS")),
        tools_root=Path(env_map.get("ACTOR_TOOLS_ROOT") or ".").expanduser(),
        tools_allow_write=_bool(env_map.get("ACTOR_TOOLS_ALLOW_WRITE"), False),
        tools_allow_network=_bool(env_map.get("ACTOR_TOOLS_ALLOW_NETWORK"), False),
        tools_timeout=_float(env_map.get("ACTOR_TOOLS_TIMEOUT"), 15.0),
        max_tool_rounds=_int(env_map.get("ACTOR_MAX_TOOL_ROUNDS"), 4) or 4,
        max_attachment_bytes=int(_float(env_map.get("ACTOR_MAX_ATTACHMENT_MB"), 10.0) * 1024 * 1024),
        max_attachments=_int(env_map.get("ACTOR_MAX_ATTACHMENTS"), 6) or 6,
        store_attachments=_bool(env_map.get("ACTOR_STORE_ATTACHMENTS"), True),
        room_token=(env_map.get("ACTOR_ROOM_TOKEN") or "").strip(),
        room_protect_reads=_bool(env_map.get("ACTOR_ROOM_PROTECT_READS"), False),
        cors_origins=_split_list(env_map.get("ACTOR_CORS_ORIGINS")),
        allowed_hosts=_split_list(env_map.get("ACTOR_ALLOWED_HOSTS")),
        webhook_url=(env_map.get("ACTOR_WEBHOOK_URL") or "").strip(),
        webhook_secret=(env_map.get("ACTOR_WEBHOOK_SECRET") or "").strip(),
        webhook_events=_split_list(env_map.get("ACTOR_WEBHOOK_EVENTS")) or ["message"],
        webhook_timeout=_float(env_map.get("ACTOR_WEBHOOK_TIMEOUT"), 10.0),
        host=(env_map.get("ACTOR_HOST") or "127.0.0.1").strip(),
        port=_int(env_map.get("ACTOR_PORT"), 8080) or 8080,
        log_level=(env_map.get("ACTOR_LOG_LEVEL") or "info").strip().lower(),
    )
    settings = settings.with_overrides(**overrides)
    if cli_key:
        settings.cli_api_key = cli_key         # --api-key が最優先（後で読み直しても残す）
        settings.api_key = cli_key             # with_overrides で入っていない場合の保険
        settings.api_key_source = "cli"
        if cli_key not in settings.api_keys:
            settings.api_keys = [cli_key, *settings.api_keys]
        if cli_key not in {entry.value for entry in settings.key_entries}:
            settings.key_entries = [KeyEntry(cli_key, "cli", "--api-key"), *settings.key_entries]
    return settings
