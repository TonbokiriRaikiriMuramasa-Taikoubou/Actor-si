"""Actor の CLI。

使い方::

    python -m actor                       # 対話を開始（サーバーも同時に立ち上げる）
    python -m actor serve --port 8080     # Web UI / API サーバーのみ
    python -m actor "依頼文"              # 1回だけ実行して終了
    python -m actor skills list           # スキル一覧
    python -m actor doctor                # 環境チェック
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .chat import ChatEngine
from .config import KNOWN_MODELS, PROJECT_ROOT, Settings, load_settings, mask_key
from .keys import MULTI_KEY_ENV
from .providers import ProviderError, get_provider
from .rooms import DEFAULT_ROOM, RoomStore
from .skills import SkillLibrary, slugify

COMMANDS = {"chat", "serve", "skills", "rooms", "bot", "tools", "key", "models", "doctor", "version", "help"}
SKILL_ACTIONS = {"list", "show", "new", "rm", "export", "path", "lint", "test"}
ROOM_ACTIONS = {"list", "show", "new", "rm", "say"}

# ---- 端末表示 --------------------------------------------------------
def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("ACTOR_FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


class C:
    RESET = "\033[0m" if _supports_color() else ""
    BOLD = "\033[1m" if _supports_color() else ""
    DIM = "\033[2m" if _supports_color() else ""
    CYAN = "\033[36m" if _supports_color() else ""
    GREEN = "\033[32m" if _supports_color() else ""
    YELLOW = "\033[33m" if _supports_color() else ""
    RED = "\033[31m" if _supports_color() else ""
    MAGENTA = "\033[35m" if _supports_color() else ""


BANNER = f"""{C.CYAN}{C.BOLD}Actor{C.RESET} {C.DIM}v{__version__} — SKILL.md 駆動の Gemini 対話クライアント{C.RESET}
{C.DIM}/help でコマンド一覧、/exit で終了。{C.RESET}"""

HELP_TEXT = f"""{C.BOLD}会話{C.RESET}
  そのまま入力                 依頼を送信（スキルは自動選択）
  / から始めて入力             スキル名の候補を表示（Gemini と同じ操作感）
  /use <名前...>               スキルを固定適用（/use none で解除）
  /auto on|off                 自動選択の ON/OFF
  /think on|off                思考ログ（thinkingConfig）の表示切り替え
  /stream on|off               ストリーミングの切り替え
  /model <名前>                モデルを切り替え
  /system                      現在の system_instruction を表示
  /show                        直近の適用スキルの詳細

{C.BOLD}常駐ボット / ルーム{C.RESET}
  actor bot --room <名前> --skills mika   ルームを監視して返事をする
  actor rooms list / show <名前>          ルームの一覧・中身
  actor rooms say "こんにちは" --reply    ルームに発言（サーバー側で即返事）
  --token <文字列> / ACTOR_ROOM_TOKEN     トークンで保護されたルームに接続

{C.BOLD}道具（function calling）と添付{C.RESET}
  モデルが自分で now / read_file / list_files などの道具を使います（🛠 表示）
  actor tools                             使える道具の一覧（--json / --run NAME key=value）
  --attach ./図.png                       画像 / PDF を添えて送る（複数可・10MB/件まで）

{C.BOLD}セッション{C.RESET}
  /new [タイトル]              新しい会話
  /sessions                    保存済みセッション一覧
  /load <id>                   セッションを読み込む
  /title <文字列>              タイトル変更

{C.BOLD}スキル{C.RESET}
  /skills                      スキル一覧（スコア付き）
  /<スキル名>                  そのスキルを固定して送信（例: /minutes 会議メモ）
  /create <作りたいスキルの説明>  会話から SKILL.md を生成して保存
  /export [名前...]            Gemini にアップロードできる ZIP を作る

{C.BOLD}API キー{C.RESET}
  actor key list               登録されているキーの一覧と状態（429 の残り時間つき・--json 可）
  actor key add                キーを追加（.actor/keys/ に 1 本ずつ・--env で .env へ）
  actor key rm <番号>          キーを 1 本消す（.env のキーもここから消せる）
  actor key check              1 本ずつ接続確認（どのキーが生きているかを確認）
  actor key stats              429 でよく切れるキー / モデルを見る（--json / --reset 可）
  actor key sources            キーを無料で用意できる場所（AI Studio / Vertex エクスプレス など）
  GEMINI_API_KEYS=key1,key2    環境変数 / .env でまとめて渡す（429 は自動で次のキーへ）
  --fallback-model <名前>       429 が続いたら逃がす別モデル（既定 gemini-3.5-flash,gemini-2.5-flash）

{C.BOLD}その他{C.RESET}
  /help                        このヘルプ
  /exit または Ctrl-D          終了
"""


# ---- 引数 ------------------------------------------------------------
def add_common_options(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("共通オプション")
    g.add_argument("--api-key", help="Gemini API キー（既定: 環境変数 GEMINI_API_KEY）")
    g.add_argument("--provider", choices=["auto", "gemini", "mock"], help="プロバイダ（auto はキーの有無で判定）")
    g.add_argument("--model", help=f"モデル名（既定: gemini-3-flash / 例: {', '.join(KNOWN_MODELS[:4])}）")
    g.add_argument("--fallback-model", action="append", dest="fallback_models", metavar="NAME",
                   help="429 のときだけ使う逃げ道のモデル（複数回指定可・既定 gemini-3.5-flash,gemini-2.5-flash）")
    g.add_argument("--no-model-fallback", action="store_true",
                   help="429 でも別モデルへ逃がさない（主モデルだけで粘る）")
    g.add_argument("--base-url", help="Gemini API のベースURL")
    g.add_argument("--skills-dir", action="append", dest="skills_dirs",
                   help="スキルフォルダ（複数回指定可 / 既定: ./skills）")
    g.add_argument("--data-dir", help="セッション等の保存先（既定: ./.actor）")
    g.add_argument("--temperature", type=float)
    g.add_argument("--max-output-tokens", type=int)
    g.add_argument("--thinking-budget", type=int, help="思考トークン上限（0 で無効）")
    g.add_argument("--timeout", type=float)
    g.add_argument("--no-auto", action="store_true", help="スキルの自動選択を無効化")
    g.add_argument("--env-file", help=".env のパス（既定: ./.env）")
    g.add_argument("-v", "--verbose", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="actor",
        description="SKILL.md を system_instruction に読み込んで Gemini と対話する",
    )
    parser.add_argument("--version", action="version", version=f"Actor {__version__}")
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="対話モード（既定）")
    add_common_options(chat)
    chat.add_argument("prompt", nargs="*", help="1回だけ実行する依頼文（省略すると対話モード）")
    chat.add_argument("-p", "--print", action="store_true", dest="oneshot", help="結果を出力して終了")
    chat.add_argument("--json", action="store_true", dest="as_json", help="結果を JSON で出力")
    chat.add_argument("--session", help="継続するセッションID")
    chat.add_argument("--use", dest="use_skills", action="append", help="固定適用するスキル名")
    chat.add_argument("--attach", action="append", default=[], metavar="PATH",
                      help="画像 / PDF などのファイルを添えて送る（複数可）")
    chat.add_argument("--no-banner", action="store_true")

    # `actor skills [list|show|new|rm|export|path] [名前...]`
    # 入れ子サブコマンドにせず位置引数で受ける（オプションの順序に依存しないため）
    skills = sub.add_parser("skills", help="スキルの管理")
    add_common_options(skills)
    skills.add_argument("args", nargs="*", help="[list|show|new|rm|export|path] [名前...]")
    skills.add_argument("--description", default="", help="new の説明")
    skills.add_argument("--body", help="new の本文をファイルから読む")
    skills.add_argument("--overwrite", action="store_true", help="new で上書きする")
    skills.add_argument("--yes", action="store_true", help="rm の確認をスキップ")
    skills.add_argument("--json", action="store_true", dest="as_json", help="show を JSON で出力")
    skills.add_argument("-o", "--output", help="export の出力先")
    skills.add_argument("--target", choices=("gemini", "portable", "agensi"), default="gemini", help="export/lint の形式")
    skills.add_argument("--keep-allowed-tools", action="store_true", help="portable lint/export に allowed-tools を含める")
    skills.add_argument("--force", action="store_true", help="lint Fail の場合も書き出す（要確認）")
    skills.add_argument("--live", action="store_true", help="skills test で実 API を利用する（課金対象）")

    # `actor rooms [list|show|new|rm|say] [名前/本文...]`
    rooms = sub.add_parser("rooms", help="ルーム（常駐ボットの共有ログ）")
    add_common_options(rooms)
    rooms.add_argument("args", nargs="*", help="[list|show|new|rm|say] [名前/本文...]")
    rooms.add_argument("--room", default=DEFAULT_ROOM, help="対象ルーム（show / say 用）")
    rooms.add_argument("--reply", action="store_true", help="say でサーバー側の返事も生成する")
    rooms.add_argument("--author", default="user", help="say の発言者名")
    rooms.add_argument("--after", type=int, default=0, help="show で表示する開始 seq")
    rooms.add_argument("--api", default="http://127.0.0.1:8080", help="say の接続先サーバー")
    rooms.add_argument("--attach", action="append", default=[], metavar="PATH",
                       help="say で画像 / PDF を添える（複数可）")
    rooms.add_argument("--token", default="", help="ルームのトークン（未指定なら ACTOR_ROOM_TOKEN）")
    rooms.add_argument("--skills", action="append", dest="room_skills", default=[], help="new で固定するスキル")
    rooms.add_argument("--yes", action="store_true", help="rm の確認をスキップ")

    # 常駐ボット
    bot = sub.add_parser("bot", help="常駐ボット（ルームを監視して返事をする）")
    add_common_options(bot)
    bot.add_argument("--room", default=DEFAULT_ROOM, help="監視するルーム名")
    bot.add_argument("--api", default="http://127.0.0.1:8080", help="接続先サーバー")
    bot.add_argument("--name", default="Actor", help="ボット名（ルーム上の表示名）")
    bot.add_argument("--skills", action="append", dest="use_skills", help="固定適用するスキル（複数可）")
    bot.add_argument("--interval", type=float, default=1.5, help="ポーリング間隔（秒）")
    bot.add_argument("--mention", default="", help="この文字列を含む発言にだけ反応する")
    bot.add_argument("--stream", action="store_true", help="生成中のテキストを端末にも流す")
    bot.add_argument("--once", action="store_true", help="1回だけ処理して終了（cron 等向け）")
    bot.add_argument("--from-now", action="store_true", help="起動後の発言にだけ反応する（履歴を読み飛ばす）")

    tools = sub.add_parser("tools", help="使えるツール（function calling）の一覧")
    add_common_options(tools)
    tools.add_argument("--json", action="store_true", dest="as_json", help="JSON で出力")
    tools.add_argument("--run", metavar="NAME", help="ツールを直接実行する（動作確認用）")
    tools.add_argument("args", nargs="*", help="--run に渡す引数（key=value 形式）")

    serve = sub.add_parser("serve", help="Web UI / HTTP API サーバー")
    add_common_options(serve)
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--open", action="store_true", help="起動後にブラウザを開く")

    key = sub.add_parser("key", help="Gemini API キーの管理（複数キー・ローテーション対応）")
    add_common_options(key)
    key.add_argument("args", nargs="*",
                     help="[show|list|stats|set|add|rm|clear|check|sources] [番号 / all]")
    key.add_argument("--value", help="キーをその場で渡す（履歴に残るので非推奨）")
    key.add_argument("--stdin", action="store_true", help="標準入力から読む")
    key.add_argument("--env", action="store_true",
                     help="add で .env の GEMINI_API_KEYS に追記する（既定は .actor/keys/）")
    key.add_argument("--reset", action="store_true", help="stats の統計をリセットする")
    key.add_argument("--sources", action="store_true", help="キーを無料で用意できる場所を表示する")
    key.add_argument("--all", action="store_true", help="check でフォールバックのモデルも確認する")
    key.add_argument("--check", action="store_true", help="保存 / 追加のあとに API へ接続して確認する")
    key.add_argument("--json", action="store_true", dest="as_json", help="list / check / stats を JSON で出力")
    key.add_argument("--yes", action="store_true", help="rm all の確認をスキップ")

    sub.add_parser("models", help="利用可能なモデル一覧（API 接続時）").add_argument("--all", action="store_true")

    doctor = sub.add_parser("doctor", help="環境チェック")
    add_common_options(doctor)
    doctor.add_argument("--check-api", action="store_true", help="実際に API へ接続して確認する")

    sub.add_parser("help", help="ヘルプ")
    sub.add_parser("version", help="バージョン")
    return parser


# 値を取らないフラグ（後続トークンを値として消費しない）
BOOL_FLAGS = {
    "-h", "--help", "--version", "-v", "--verbose", "--no-auto", "-p", "--print", "--json",
    "--open", "--no-banner", "--all", "--check-api", "--overwrite", "--yes", "--full",
    "--reply", "--stream", "--once", "--from-now", "--json",
    "--env", "--reset", "--stdin", "--check", "--no-model-fallback", "--all", "--sources",
}


def _split_command(argv: list[str]) -> list[str]:
    """コマンド名が省略されていれば chat を補う。

    例: ``actor doctor`` → ``["doctor"]`` / ``actor --model x serve`` → ``["serve", "--model", "x"]``
    / ``actor "こんにちは"`` → ``["chat", "こんにちは"]``
    """
    if not argv:
        return ["chat"]
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("-"):
            takes_value = "=" not in token and token not in BOOL_FLAGS
            index += 2 if takes_value else 1
            continue
        if token in COMMANDS:
            rest = argv[:index] + argv[index + 1:]
            return [token, *rest]
        break  # 最初の非フラグトークンが依頼文 → chat 扱い
    if argv[0] in {"-h", "--help", "--version"}:
        return argv
    return ["chat", *argv]


def make_settings(args: argparse.Namespace) -> Settings:
    overrides: dict[str, Any] = {}
    if getattr(args, "api_key", None):
        overrides["api_key"] = args.api_key
    if getattr(args, "provider", None):
        overrides["provider"] = args.provider
    if getattr(args, "model", None):
        overrides["model"] = args.model
    if getattr(args, "no_model_fallback", False):
        overrides["model_fallbacks"] = []
    elif getattr(args, "fallback_models", None):
        base = load_settings(env_file=getattr(args, "env_file", None)).model_fallbacks
        merged: list[str] = []
        for name in [*args.fallback_models, *base]:
            if name and name not in merged:
                merged.append(name)
        overrides["model_fallbacks"] = merged
    if getattr(args, "base_url", None):
        overrides["base_url"] = args.base_url.rstrip("/")
    if getattr(args, "skills_dirs", None):
        overrides["skills_dirs"] = [Path(p).expanduser() for p in args.skills_dirs]
    if getattr(args, "data_dir", None):
        overrides["data_dir"] = Path(args.data_dir).expanduser()
    if getattr(args, "temperature", None) is not None:
        overrides["temperature"] = args.temperature
    if getattr(args, "max_output_tokens", None) is not None:
        overrides["max_output_tokens"] = args.max_output_tokens
    if getattr(args, "thinking_budget", None) is not None:
        overrides["thinking_budget"] = args.thinking_budget
    if getattr(args, "timeout", None) is not None:
        overrides["timeout"] = args.timeout
    if getattr(args, "no_auto", False):
        overrides["auto_select"] = False
    if getattr(args, "host", None):
        overrides["host"] = args.host
    if getattr(args, "port", None):
        overrides["port"] = args.port
    env_file = getattr(args, "env_file", None)
    if env_file and not Path(str(env_file)).is_file():
        env_file = None       # まだ無い .env は既定の場所のままでよい（新規作成は key add --env が行う）
    return load_settings(env_file=env_file, **overrides)


# ---- 表示ヘルパ ------------------------------------------------------
def print_skills(library: SkillLibrary, query: str = "", scores: dict[str, float] | None = None) -> None:
    skills = library.all()
    if not skills:
        print(f"{C.YELLOW}スキルが見つかりません。{C.RESET} フォルダ: " + ", ".join(str(d) for d in library.dirs))
        print(f"{C.DIM}  actor skills new my-skill --description '説明' で作成できます。{C.RESET}")
        return
    ordered = skills
    if scores:
        ordered = sorted(skills, key=lambda s: (-scores.get(s.name, 0.0), s.name))
    print(f"{C.BOLD}スキル {len(skills)} 件{C.RESET} {C.DIM}({', '.join(str(d) for d in library.dirs)}){C.RESET}")
    for skill in ordered:
        score = f" {C.YELLOW}score={scores.get(skill.name, 0):.1f}{C.RESET}" if scores and scores.get(skill.name) else ""
        refs = f" {C.DIM}[ref:{len(skill.references)}]{C.RESET}" if skill.references else ""
        print(f"  {C.CYAN}/{skill.name}{C.RESET}{refs}{score}")
        print(f"    {C.DIM}{skill.description}{C.RESET}")
        for warning in skill.warnings:
            print(f"    {C.YELLOW}! {warning}{C.RESET}")
    for error in library.errors:
        print(f"  {C.RED}! {error['path']}: {error['error']}{C.RESET}")


def print_selection(event: dict[str, Any]) -> None:
    names = event.get("names") or []
    scores = {item["name"]: item["score"] for item in event.get("auto_scores", [])}
    if names:
        parts = []
        for name in names:
            score = scores.get(name)
            parts.append(f"{C.CYAN}{name}{C.RESET}" + (f"{C.DIM}({score:.1f}){C.RESET}" if score else ""))
        print(f"{C.MAGENTA}▶ 適用スキル:{C.RESET} " + "  ".join(parts))
    else:
        print(f"{C.DIM}▶ 適用スキル: なし（自動選択の閾値未満）{C.RESET}")
    for missing in event.get("missing", []):
        print(f"{C.YELLOW}  ! スキル '{missing}' が見つかりません{C.RESET}")
    print(f"{C.DIM}  system_instruction: {event.get('system_chars', 0):,} 文字 ≈ {event.get('system_tokens', 0):,} tokens{C.RESET}")


# ---- チャット --------------------------------------------------------
def load_attachments(paths: Sequence[str], settings: Settings) -> list[dict[str, Any]]:
    """``--attach`` で渡されたパスを base64 の添付に変換する。"""
    import base64
    import mimetypes

    files: list[dict[str, Any]] = []
    for raw in list(paths)[: settings.max_attachments]:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"添付が見つかりません: {path}")
        blob = path.read_bytes()
        if len(blob) > settings.max_attachment_bytes:
            raise ValueError(
                f"添付 '{path.name}' が大きすぎます"
                f"（{len(blob) / 1048576:.1f}MB / 上限 {settings.max_attachment_bytes / 1048576:.0f}MB）"
            )
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        files.append({
            "name": path.name,
            "mime_type": mime,
            "size": len(blob),
            "data": base64.b64encode(blob).decode("ascii"),
        })
    return files


def print_tools(registry: Any) -> None:
    print(f"{C.BOLD}ツール {len(registry)} 件{C.RESET}")
    for tool in registry.all():
        flag = f" {C.YELLOW}[danger]{C.RESET}" if tool.dangerous else ""
        print(f"  {C.CYAN}{tool.name}{C.RESET}  {tool.description}{flag}")


class Repl:
    def __init__(self, settings: Settings, engine: ChatEngine, session_id: str | None = None,
                 pinned: Sequence[str] = ()):
        self.settings = settings
        self.engine = engine
        self.show_thoughts = settings.thinking_budget > 0
        self.stream = settings.stream
        self.last_selection: dict[str, Any] = {}
        self.last_system: str = ""
        if session_id:
            self.session = engine.store.require(session_id)
        else:
            self.session = engine.store.create(model=settings.model, skills=list(pinned))
        if pinned:
            self.session.skills = list(pinned)

    # ---- プロンプト ----
    def prompt_string(self) -> str:
        pinned = ",".join(self.session.skills) if self.session.skills else "-"
        flags = []
        if self.settings.auto_select:
            flags.append("auto")
        if self.show_thoughts:
            flags.append("think")
        suffix = f" {C.DIM}[{pinned}|{'|'.join(flags) or 'manual'}]{C.RESET}"
        return f"{C.GREEN}you{C.RESET}{suffix} {C.DIM}›{C.RESET} "

    # ---- メインループ ----
    def run(self, initial_prompt: str | None = None) -> int:
        if initial_prompt:
            self.send(initial_prompt)
            return 0
        while True:
            try:
                line = input(self.prompt_string())
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if self.handle_command(line):
                    return 0
                continue
            self.send(line)

    def handle_command(self, line: str) -> bool:
        """True を返すと終了。"""
        parts = line[1:].split()
        cmd, rest = (parts[0] if parts else ""), parts[1:]

        if cmd in {"exit", "quit", "q"}:
            print(f"{C.DIM}またね。{C.RESET}")
            return True
        if cmd in {"help", "h", "?"}:
            print(HELP_TEXT)
            return False
        if cmd == "skills":
            self.engine.library.reload(force=True)
            print_skills(self.engine.library)
            return False
        if cmd == "use":
            if not rest or rest[0] in {"none", "off", "-"}:
                self.session.skills = []
                print(f"{C.DIM}固定スキルを解除しました。{C.RESET}")
            else:
                found, missing = self.engine.library.resolve(rest)
                self.session.skills = [s.name for s in found]
                print(f"{C.DIM}固定: {', '.join(self.session.skills) or 'なし'}{C.RESET}")
                for name in missing:
                    print(f"{C.YELLOW}  ! '{name}' は見つかりません{C.RESET}")
            self.engine.store.save(self.session)
            return False
        if cmd == "auto":
            value = (rest[0] if rest else "toggle").lower()
            if value in {"on", "true", "1"}:
                self.settings.auto_select = True
            elif value in {"off", "false", "0"}:
                self.settings.auto_select = False
            else:
                self.settings.auto_select = not self.settings.auto_select
            print(f"{C.DIM}自動選択: {'ON' if self.settings.auto_select else 'OFF'}{C.RESET}")
            return False
        if cmd == "think":
            value = (rest[0] if rest else "toggle").lower()
            self.show_thoughts = not self.show_thoughts if value not in {"on", "off"} else value == "on"
            if self.show_thoughts and self.settings.thinking_budget <= 0:
                self.settings.thinking_budget = 1024
                print(f"{C.DIM}thinkingBudget を 1024 に設定しました。{C.RESET}")
            print(f"{C.DIM}思考ログ: {'ON' if self.show_thoughts else 'OFF'}{C.RESET}")
            return False
        if cmd == "stream":
            value = (rest[0] if rest else "toggle").lower()
            self.stream = not self.stream if value not in {"on", "off"} else value == "on"
            print(f"{C.DIM}ストリーミング: {'ON' if self.stream else 'OFF'}{C.RESET}")
            return False
        if cmd == "model":
            if rest:
                self.settings.model = rest[0]
                self.engine.provider.settings = self.settings
                print(f"{C.DIM}モデル: {rest[0]}{C.RESET}")
            else:
                print(f"{C.DIM}現在: {self.settings.model}{C.RESET}")
            return False
        if cmd in {"system", "prompt"}:
            print(self.last_system or f"{C.DIM}（まだ送信していません）{C.RESET}")
            return False
        if cmd in {"show", "info"}:
            if not self.last_selection:
                print(f"{C.DIM}（まだ送信していません）{C.RESET}")
            else:
                print(json.dumps(self.last_selection, ensure_ascii=False, indent=2))
            return False
        if cmd == "new":
            self.session = self.engine.store.create(title=" ".join(rest) or "新しい会話", model=self.settings.model)
            print(f"{C.DIM}新しい会話: {self.session.id}{C.RESET}")
            return False
        if cmd == "sessions":
            for item in self.engine.store.list():
                mark = "*" if item["id"] == self.session.id else " "
                print(f"{mark} {C.CYAN}{item['id']}{C.RESET} {item['title']}  {C.DIM}{item['updated_at']} ({item['turn_count']}turn){C.RESET}")
            return False
        if cmd == "load":
            if not rest:
                print(f"{C.RED}使い方: /load <id>{C.RESET}")
                return False
            try:
                self.session = self.engine.store.require(rest[0])
                print(f"{C.DIM}読み込みました: {self.session.title} ({self.session.turn_count} turn){C.RESET}")
            except KeyError as exc:
                print(f"{C.RED}{exc}{C.RESET}")
            return False
        if cmd == "title":
            self.session.title = " ".join(rest) or self.session.title
            self.engine.store.save(self.session)
            print(f"{C.DIM}タイトル: {self.session.title}{C.RESET}")
            return False
        if cmd == "create":
            if not rest:
                print(f"{C.RED}使い方: /create 議事録を毎回同じ形式で作るスキル{C.RESET}")
                return False
            self.create_skill(" ".join(rest))
            return False
        if cmd == "export":
            self.export_zip(rest)
            return False
        if not rest:
            # `/スキル名` のショートカット
            if self.engine.library.get(slugify(cmd)):
                print(f"{C.DIM}スキル '{slugify(cmd)}' を固定しました。メッセージを入力してください。{C.RESET}")
                self.session.skills = [slugify(cmd)]
                self.engine.store.save(self.session)
                return False
            print(f"{C.YELLOW}未知のコマンド: /{cmd}（/help 参照）{C.RESET}")
            return False
        # `/スキル名 依頼文` の一発実行
        skill = self.engine.library.get(slugify(cmd))
        if skill:
            self.session.skills = [skill.name]
            self.send(" ".join(rest), explicit=[skill.name])
            return False
        print(f"{C.YELLOW}未知のコマンド: /{cmd}（/help 参照）{C.RESET}")
        return False

    # ---- 送信 ----
    def send(self, text: str, explicit: Sequence[str] | None = None,
             attachments: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
        final: dict[str, Any] = {}
        printed_header = False
        buffer: list[str] = []
        interactive = sys.stdout.isatty()
        try:
            for event in self.engine.turn(
                self.session, text, explicit_skills=explicit, stream=self.stream,
                attachments=list(attachments or []),
            ):
                etype = event["type"]
                if etype == "selection":
                    self.last_selection = event
                    if interactive and (event.get("names") or event.get("missing")):
                        print_selection(event)
                    printed_header = True
                elif etype == "prompt":
                    self.last_system = event.get("system_instruction", "")
                elif etype == "thought":
                    if self.show_thoughts and interactive:
                        sys.stdout.write(f"{C.DIM}{event['text']}{C.RESET}")
                        sys.stdout.flush()
                elif etype == "text":
                    buffer.append(event["text"])
                    if interactive and self.stream:
                        sys.stdout.write(event["text"])
                        sys.stdout.flush()
                elif etype == "notice":
                    text = event.get("text") or ""
                    if text:
                        print(f"{C.YELLOW}🔁 {text}{C.RESET}")
                elif etype == "tool_call":
                    print(f"{C.MAGENTA}🛠 {event.get('name')}{C.RESET}"
                          f"{C.DIM} {json.dumps(event.get('args') or {}, ensure_ascii=False)}{C.RESET}")
                elif etype == "tool_result":
                    snippet = " ".join(str(event.get("result") or "").split())[:120]
                    state = "" if event.get("ok", True) else f"{C.RED}(失敗) {C.RESET}"
                    print(f"{C.DIM}   ↳ {state}{snippet}{C.RESET}")
                elif etype == "tool_limit":
                    print(f"{C.YELLOW}ツールの実行回数が上限に達しました{C.RESET}")
                elif etype == "error":
                    print(f"\n{C.RED}エラー: {event['text']}{C.RESET}")
                elif etype == "done":
                    final = event
        except ProviderError as exc:
            print(f"{C.RED}プロバイダエラー: {exc}{C.RESET}")
            return final
        if self.show_thoughts and interactive:
            print()
        body = "".join(buffer)
        if not (interactive and self.stream):
            print(body)
        else:
            print()
        if final.get("usage"):
            usage = final["usage"]
            simulated = f" {C.YELLOW}[simulation]{C.RESET}" if final.get("simulated") else ""
            print(
                f"{C.DIM}tokens: in {usage.get('promptTokenCount', 0):,} / "
                f"out {usage.get('candidatesTokenCount', 0):,}"
                + (f" / think {usage.get('thoughtsTokenCount', 0):,}" if usage.get("thoughtsTokenCount") else "")
                + f"  model: {final.get('model', '')}{C.RESET}{simulated}"
            )
        if not printed_header and final.get("error"):
            print(f"{C.RED}{final['error']}{C.RESET}")
        return final

    # ---- スキル生成 / 書き出し ----
    def create_skill(self, request: str) -> None:
        print(f"{C.DIM}スキルを設計しています…{C.RESET}")
        draft = self.engine.draft_skill(request)
        print(f"\n{C.BOLD}name{C.RESET}:        {C.CYAN}{draft.name}{C.RESET}")
        print(f"{C.BOLD}description{C.RESET}: {draft.description}")
        print(f"{C.DIM}{'-' * 60}{C.RESET}")
        print(draft.instructions)
        print(f"{C.DIM}{'-' * 60}{C.RESET}")
        try:
            answer = input(f"保存しますか？ [Y/n/e=編集] {C.DIM}›{C.RESET} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer in {"n", "no"}:
            print(f"{C.DIM}破棄しました。{C.RESET}")
            return
        if answer in {"e", "edit"}:
            print(f"{C.DIM}本文を入力してください（空行2つで確定）:{C.RESET}")
            lines: list[str] = []
            blanks = 0
            try:
                while True:
                    line = input()
                    if not line.strip():
                        blanks += 1
                        if blanks >= 2:
                            break
                    else:
                        blanks = 0
                    lines.append(line)
            except (EOFError, KeyboardInterrupt):
                pass
            draft.instructions = "\n".join(lines).strip() or draft.instructions
        try:
            skill = self.engine.library.create(
                draft.name, draft.description, draft.instructions, overwrite=True
            )
        except Exception as exc:
            print(f"{C.RED}保存に失敗しました: {exc}{C.RESET}")
            return
        print(f"{C.GREEN}保存しました:{C.RESET} {skill.path}")
        self.session.skills = [skill.name]

    def export_zip(self, names: Sequence[str]) -> None:
        try:
            data = self.engine.library.export_zip(list(names) or None)
        except KeyError as exc:
            print(f"{C.RED}{exc}{C.RESET}")
            return
        label = "-".join(names) if names else "all-skills"
        out = Path.cwd() / f"{label}.zip"
        out.write_bytes(data)
        print(f"{C.GREEN}書き出しました:{C.RESET} {out} ({len(data):,} bytes)")
        print(f"{C.DIM}Gemini アプリ → 設定 → スキル → アップロード で取り込めます。{C.RESET}")


# ---- 各コマンド ------------------------------------------------------
def cmd_chat(args: argparse.Namespace) -> int:
    settings = make_settings(args)
    engine = ChatEngine(settings)
    prompt = " ".join(args.prompt).strip()

    if not sys.stdin.isatty() and not prompt:
        piped = sys.stdin.read().strip()
        if piped:
            prompt = piped

    if args.as_json or args.oneshot or prompt:
        if not prompt:
            print(f"{C.RED}依頼文がありません。{C.RESET}")
            return 2
        session = engine.store.require(args.session) if args.session else engine.store.create(
            model=settings.model, skills=args.use_skills or []
        )
        attachments = load_attachments(args.attach or [], settings)
        if args.as_json:
            events = list(engine.turn(
                session, prompt, explicit_skills=args.use_skills, stream=False,
                attachments=attachments,
            ))
            done = next((e for e in events if e["type"] == "done"), {})
            selection = next((e for e in events if e["type"] == "selection"), {})
            payload = {
                "session_id": session.id,
                "text": "".join(e.get("text", "") for e in events if e["type"] == "text"),
                "skills": selection.get("names", []),
                "model": done.get("model", ""),
                "usage": done.get("usage", {}),
                "simulated": done.get("simulated", False),
                "error": done.get("error"),
                "tools": done.get("tool_calls", []),
                "notices": done.get("notices", []),
                "requested_model": done.get("requested_model", ""),
                "attachments": [a["name"] for a in attachments],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        repl = Repl(settings, engine, session_id=session.id, pinned=args.use_skills or [])
        final = repl.send(prompt, explicit=args.use_skills, attachments=attachments)
        return 1 if final.get("error") else 0

    if not args.no_banner:
        print(BANNER)
        _print_mode(settings, engine)
    repl = Repl(settings, engine, session_id=args.session, pinned=args.use_skills or [])
    if args.attach:
        print(f"{C.DIM}添付を読み込みました: {', '.join(Path(a).name for a in args.attach)}{C.RESET}")
    return repl.run()


def _print_mode(settings: Settings, engine: ChatEngine) -> None:
    provider = engine.provider
    if provider.is_mock:
        print(f"{C.YELLOW}シミュレータで動作中{C.RESET}{C.DIM} — GEMINI_API_KEY を設定すると本物の Gemini に切り替わります。{C.RESET}")
    else:
        pool = settings.key_pool()
        extra = f" / キー {pool.size} 本（{pool.summary()}）" if pool.size > 1 else ""
        print(f"{C.DIM}Gemini 接続: {settings.model} / key {mask_key(settings.api_key)}{extra}{C.RESET}")
        if pool.size > 1:
            print(f"{C.DIM}429 のときは自動で次のキーへ切り替わります（状態: actor key list）{C.RESET}")
    print(f"{C.DIM}スキル {len(engine.library)} 件 / 保存先 {settings.data_dir}{C.RESET}")


def cmd_serve(args: argparse.Namespace) -> int:
    settings = make_settings(args)
    try:
        from .server import run_server
    except ImportError as exc:  # pragma: no cover
        print(f"{C.RED}サーバーの起動に失敗しました: {exc}{C.RESET}")
        return 1
    return run_server(settings, open_browser=args.open)


def cmd_skills(args: argparse.Namespace) -> int:
    settings = make_settings(args)
    library = SkillLibrary(settings.skills_dirs)
    tokens = list(getattr(args, "args", []) or [])
    sub = tokens[0] if tokens and tokens[0] in SKILL_ACTIONS else (tokens[0] if tokens else "list")
    names = tokens[1:] if tokens and tokens[0] in SKILL_ACTIONS else tokens

    if sub == "lint":
        from .portable import lint_skills
        reports = lint_skills(library.resolve(names)[0] if names else library.all(),
                              keep_allowed_tools=args.keep_allowed_tools)
        payload = [report.to_dict() for report in reports]
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for report in reports:
                print(f"{report.skill}: {'PASS' if report.passed else 'FAIL'} ({report.score}/100)")
                for finding in report.findings:
                    print(f"  {finding.severity.upper():7} {finding.category}: {finding.message}")
                if report.external_urls:
                    print("  URLs: " + ", ".join(report.external_urls))
                    print("  Domains: " + ", ".join(report.external_domains))
        return 0 if all(report.passed for report in reports) else 1
    if sub == "test":
        from .skill_tests import run_cases
        try:
            result = run_cases(library, names or None, live=args.live, settings=settings)
        except Exception as exc:
            print(f"{C.RED}skills test failed: {exc}{C.RESET}")
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("passed") else 1
    if sub == "list":
        print_skills(library)
        return 0
    if sub == "path":
        for directory in library.dirs:
            exists = "✓" if directory.is_dir() else "（未作成）"
            print(f"{directory} {exists}")
        return 0
    if sub == "show":
        if not names:
            print(f"{C.RED}使い方: actor skills show <名前>{C.RESET}")
            return 2
        skill = library.get(slugify(names[0]))
        if not skill:
            print(f"{C.RED}'{names[0]}' が見つかりません。{C.RESET}")
            return 1
        if args.as_json:
            print(json.dumps(skill.to_dict(), ensure_ascii=False, indent=2))
            return 0
        print(f"{C.BOLD}{skill.name}{C.RESET}  {C.DIM}{skill.path}{C.RESET}")
        print(f"{C.DIM}{skill.description}{C.RESET}")
        print(f"{C.DIM}hash={skill.content_hash()} / refs={len(skill.references)} / warnings={len(skill.warnings)}{C.RESET}")
        if skill.allowed_tools:
            print(f"{C.DIM}allowed-tools: {', '.join(skill.allowed_tools)}{C.RESET}")
        if skill.overrides:
            pairs = ", ".join(f"{k}={v}" for k, v in skill.overrides.items())
            print(f"{C.YELLOW}パラメータ上書き: {pairs}{C.RESET}")
        for warning in skill.warnings:
            print(f"{C.YELLOW}! {warning}{C.RESET}")
        print()
        print(skill.instructions)
        if skill.references:
            print(f"\n{C.BOLD}参照ファイル{C.RESET}")
            for ref in skill.references:
                note = f" {C.RED}{ref.error}{C.RESET}" if ref.error else ""
                print(f"  {ref.relpath} ({ref.kind}, {ref.size:,} bytes){note}")
        return 0
    if sub == "new":
        if not names:
            print(f"{C.RED}使い方: actor skills new <名前> --description '説明'{C.RESET}")
            return 2
        body = Path(args.body).read_text(encoding="utf-8") if args.body else None
        if body is None:
            body = "# 目的\n（この作業で達成したいこと）\n\n# 手順\n1. 目的を確認する\n2. 実行する\n3. 出力を整形する\n\n# 出力フォーマット\n```markdown\n## 結果\n```\n\n# 確認事項\n- 捏造しない\n"
        try:
            skill = library.create(names[0], args.description, body, overwrite=args.overwrite)
        except (FileExistsError, ValueError) as exc:
            print(f"{C.RED}{exc}{C.RESET}")
            return 1
        print(f"{C.GREEN}作成しました:{C.RESET} {skill.path}")
        return 0
    if sub == "rm":
        if not names:
            print(f"{C.RED}使い方: actor skills rm <名前>{C.RESET}")
            return 2
        skill = library.get(slugify(names[0]))
        if not skill:
            print(f"{C.RED}'{names[0]}' が見つかりません。{C.RESET}")
            return 1
        if not args.yes:
            try:
                answer = input(f"'{skill.name}' を削除しますか？ [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return 1
            if answer not in {"y", "yes"}:
                print(f"{C.DIM}中止しました。{C.RESET}")
                return 1
        library.delete(skill.name)
        print(f"{C.GREEN}削除しました:{C.RESET} {skill.name}")
        return 0
    if sub == "export":
        try:
            if args.target in {"portable", "agensi"}:
                from .portable import export_zip, lint_skills
                targets = library.resolve(names)[0] if names else library.all()
                if len(targets) != 1:
                    raise ValueError("portable/agensi export accepts exactly one skill; specify its name")
                data, reports = export_zip(targets, keep_allowed_tools=args.keep_allowed_tools, force=args.force)
                for report in reports:
                    print(f"{report.skill}: {'PASS' if report.passed else 'FAIL'} ({report.score}/100)")
                for skill in targets:
                    dropped = [key for key in ("temperature", "top_p", "max_output_tokens", "thinking_budget", "model")
                               if skill.meta.get(key) not in (None, "")]
                    if dropped:
                        print(f"WARNING {skill.name}: omitted Actor generation settings: {', '.join(dropped)}")
                    if skill.allowed_tools and not args.keep_allowed_tools:
                        print(f"WARNING {skill.name}: omitted allowed-tools; host tool names vary")
                    if re.search(r"(?i)Gemini|system_instruction|generated-by: Actor", skill.instructions):
                        print(f"WARNING {skill.name}: Actor/Gemini-specific wording may not be portable")
            else:
                data = library.export_zip(names or None)
        except (KeyError, ValueError) as exc:
            print(f"{C.RED}{exc}{C.RESET}")
            return 1
        out = Path(args.output) if args.output else Path.cwd() / (
            f"{'-'.join(names)}.zip" if names else ("skills-portable.zip" if args.target != "gemini" else "skills.zip")
        )
        out.write_bytes(data)
        print(f"{C.GREEN}書き出しました:{C.RESET} {out} ({len(data):,} bytes)")
        if args.target in {"portable", "agensi"}:
            print("ZIP構成: <name>/SKILL.md, references/, LICENSE。Agensi への提出は手動で行ってください。")
        return 0
    print(f"{C.RED}未知のサブコマンド: {sub}{C.RESET}{C.DIM}（list / show / new / rm / export / lint / test / path）{C.RESET}")
    return 2


def cmd_rooms(args: argparse.Namespace) -> int:
    settings = make_settings(args)
    store = RoomStore(settings.data_dir / "rooms")
    tokens = list(getattr(args, "args", []) or [])
    sub = tokens[0] if tokens and tokens[0] in ROOM_ACTIONS else "list"
    rest = tokens[1:] if tokens and tokens[0] in ROOM_ACTIONS else tokens

    if sub == "list":
        rooms = store.list()
        if not rooms:
            print(f"{C.YELLOW}ルームがありません。{C.RESET}{C.DIM}actor rooms new my-room で作成できます。{C.RESET}")
            return 0
        print(f"{C.BOLD}ルーム {len(rooms)} 件{C.RESET} {C.DIM}({settings.data_dir / 'rooms'}){C.RESET}")
        for room in rooms:
            skills = f" {C.DIM}[{', '.join(room['skills'])}]{C.RESET}" if room["skills"] else ""
            print(f"  {C.CYAN}{room['name']}{C.RESET}  {room['count']} 件{skills}  {C.DIM}{room['updated_at']}{C.RESET}")
            if room["preview"]:
                print(f"    {C.DIM}{room['preview']}{C.RESET}")
        return 0

    if sub == "show":
        name = rest[0] if rest else args.room
        room = store.get(name)
        if room is None:
            print(f"{C.RED}ルーム '{name}' が見つかりません{C.RESET}")
            return 1
        print(f"{C.BOLD}#{room.name}{C.RESET} {C.DIM}cursor={room.cursor} / session={room.session_id or '-'}{C.RESET}")
        for message in room.since(args.after):
            who = message.author or message.role
            color = C.CYAN if message.role == "user" else C.GREEN
            stamp = message.ts.replace("T", " ")[:19]
            print(f"{C.DIM}{stamp}{C.RESET} {color}{who}{C.RESET}: {message.text}")
        return 0

    if sub == "new":
        name = rest[0] if rest else args.room
        if not RoomStore.valid_name(name):
            print(f"{C.RED}ルーム名は英数字・ハイフン・アンダースコア 32 文字以内にしてください{C.RESET}")
            return 1
        room = store.create(name, skills=list(args.room_skills or []))
        print(f"{C.GREEN}ルームを作成しました:{C.RESET} {room.name}")
        return 0

    if sub == "rm":
        name = rest[0] if rest else args.room
        if not args.yes:
            try:
                answer = input(f"ルーム '{name}' を削除しますか？ [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return 1
            if answer not in {"y", "yes"}:
                print(f"{C.DIM}中止しました。{C.RESET}")
                return 1
        if store.delete(name):
            print(f"{C.GREEN}削除しました:{C.RESET} {name}")
            return 0
        print(f"{C.RED}ルーム '{name}' が見つかりません{C.RESET}")
        return 1

    if sub == "say":
        from .bot import RoomClient

        text = " ".join(rest)
        if not text.strip():
            print(f"{C.RED}使い方: actor rooms say 'こんにちは' --reply{C.RESET}")
            return 2
        store.get_or_create(args.room, skills=list(args.room_skills or []))
        client = RoomClient(args.api, token=args.token or settings.room_token)
        attachments = load_attachments(args.attach or [], settings)
        try:
            result = client.post(
                args.room, text, author=args.author, reply=bool(args.reply),
                attachments=attachments,
            )
        except RuntimeError as exc:
            # サーバーが無い場合はローカルにそのまま書き込む
            room = store.require(args.room)
            room.append("user", text, author=args.author)
            store.save(room)
            print(f"{C.YELLOW}サーバーに接続できないためローカルのルームへ書き込みました{C.RESET} {C.DIM}({exc}){C.RESET}")
            print(f"{C.CYAN}{args.author}{C.RESET}: {text}")
            print(f"{C.DIM}返事が欲しい場合は actor serve を起動して --reply を付けてください。{C.RESET}")
            return 0
        print(f"{C.CYAN}{args.author}{C.RESET}: {text}")
        if result.get("reply"):
            print(f"{C.GREEN}Actor{C.RESET}: {result['reply']['text']}")
        return 0

    print(f"{C.RED}未知のサブコマンド: {sub}{C.RESET}{C.DIM}（list / show / new / rm / say）{C.RESET}")
    return 2


def cmd_bot(args: argparse.Namespace) -> int:
    from .bot import run_bot

    settings = make_settings(args)
    return run_bot(
        settings,
        args.room,
        api_base=args.api,
        skills=args.use_skills or [],
        auto=False if args.no_auto else None,
        interval=args.interval,
        bot_name=args.name,
        stream=args.stream,
        once=args.once,
        from_now=args.from_now,
        mention=args.mention,
    )


KEY_ACTIONS = {"show", "list", "ls", "set", "add", "rm", "remove", "clear", "check", "stats", "sources"}


def _read_key_value(args: argparse.Namespace, prompt: str) -> str | None:
    """``--value`` / ``--stdin`` / パイプ / 対話入力の順でキーを 1 本読む。"""
    import getpass

    value = getattr(args, "value", None)
    if value:
        return str(value).strip()
    if getattr(args, "stdin", False) or not sys.stdin.isatty():
        return sys.stdin.read().strip()
    try:
        return getpass.getpass(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def _validate_key(api_key: str) -> int | None:
    """キーの見た目を確認する（問題なければ None / 問題があれば終了コード）。"""
    if not api_key:
        print(f"{C.RED}キーが空です。{C.RESET}")
        return 2
    if any(ch.isspace() for ch in api_key):
        print(f"{C.RED}キーに空白が含まれています。コピーし直してください。{C.RESET}")
        return 2
    if len(api_key) < 20:
        print(f"{C.RED}キーが短すぎます（Google AI Studio で発行したキーを貼り付けてください）。{C.RESET}")
        return 2
    return None


def _probe_key(settings: Settings, api_key: str, *, model: str | None = None) -> tuple[bool, str]:
    """キー 1 本でモデル一覧を引いて、生きているか確かめる（自動切り替えはしない）。"""
    from dataclasses import replace

    probe = replace(settings, api_key=api_key, api_keys=[api_key], key_entries=[], key_rotate=False,
                    model_fallbacks=[], model=model or settings.model)
    provider = get_provider(probe)
    if provider.is_mock:
        return False, "プロバイダが mock（--provider / ACTOR_GEMINI_IMPL を確認）"
    try:
        models = provider.model_catalog(strict=True)     # 429 などの理由をそのまま受け取る
    except ProviderError as exc:
        detail = str(exc).splitlines()[0]
        if exc.status and not detail.startswith(str(exc.status)):
            detail = f"{exc.status} {detail}"
        return False, detail
    if models:
        return True, f"OK（{len(models)} models）"
    return False, "接続できませんでした（キー / ネットワークを確認）"


def cmd_key(args: argparse.Namespace) -> int:
    """API キーを管理する（``show`` / ``list`` / ``set`` / ``add`` / ``rm`` / ``clear`` / ``check``）。

    複数キーは ``.actor/keys/``（``actor key add``）か ``GEMINI_API_KEYS`` で持てる。
    429 のときは自動で次のキーへ切り替わる（``actor key list`` で状態を確認できる）。
    """
    from .config import (
        append_key_to_env_file,
        clear_api_key,
        list_generated_key_files,
        refresh_keys,
        remove_key_from_env_file,
        write_api_key,
        write_keys_dir,
    )

    settings = make_settings(args)
    tokens = [str(token) for token in (getattr(args, "args", []) or [])]
    action = tokens[0].lower() if tokens else "show"
    rest = tokens[1:]
    if action not in KEY_ACTIONS:
        print(f"{C.RED}不明な操作です: {action}{C.RESET}")
        print(f"{C.DIM}使えるのは show / list / set / add / rm / clear / check です。{C.RESET}")
        return 2
    pool = settings.key_pool()

    # ---- 一覧 ----
    if action in {"list", "ls"}:
        if args.as_json:
            print(json.dumps({
                "count": pool.size,
                "ready": pool.ready_count,
                "active": pool.current_index,
                "provider": settings.effective_provider,
                "model": settings.model,
                "keys_dir": str(settings.keys_path),
                "cooldown": settings.key_cooldown,
                "rotate": settings.key_rotate,
                "model": settings.model,
                "model_fallbacks": list(settings.model_fallbacks),
                "ready_by_model": {name: pool.ready_count_for(name) for name in pool.models},
                "keys": pool.status(),
            }, ensure_ascii=False, indent=2))
            return 0
        if not pool.size:
            _print_no_key()
            return 0
        chain = settings.model_fallbacks or []
        chain_label = f" / 逃げ道: {', '.join(chain)}" if chain else " / 逃げ道: なし（--no-model-fallback）"
        print(f"{C.BOLD}API キー {pool.size} 本{C.RESET} {C.DIM}"
              f"（{pool.summary()} / モデル: {settings.model}{chain_label}）{C.RESET}")
        if len(pool.models) > 1:
            parts = " / ".join(f"{name} {label}" for name, label in pool.model_summary())
            print(f"{C.DIM}  モデルごとの空き: {parts}{C.RESET}")
        for item in pool.status():
            mark = "*" if item["active"] and item["ready"] else " "
            if not item["ready"]:
                state = f"{item['status'] or 429} 制限中（あと {item['wait']:.0f} 秒）"
                color = C.YELLOW
            elif item["active"]:
                state, color = "使用中", C.GREEN
            else:
                state, color = "待機", C.DIM
            extra = ""
            if item["limited_models"]:
                extra = f" {C.DIM}（{'/'.join(item['limited_models'])} では制限中）{C.RESET}"
            print(f"  {mark}[{item['index']}] {item['masked']:<24} {color}{state}{C.RESET} "
                  f"{C.DIM}{item['label']}{' ' + item['path'] if item['path'] else ''}{C.RESET}{extra}")
        print(f"{C.DIM}429 は {settings.key_cooldown:.0f} 秒そのキーを避けて次のキーへ回します"
              f"（ACTOR_KEY_COOLDOWN / ACTOR_KEY_ROTATE=false で停止）。{C.RESET}")
        if pool.totals.get("limits"):
            print(f"{C.DIM}これまでに 429 で切り替えた回数: {pool.totals['limits']:,}"
                  f"（内訳は actor key stats）{C.RESET}")
        print(f"{C.DIM}追加: actor key add   /   削除: actor key rm <番号>   /   "
              f"確認: actor key check   /   統計: actor key stats   /   入手先: actor key sources{C.RESET}")
        return 0

    # ---- 状態 ----
    if action == "show":
        if not pool.size:
            _print_no_key()
            return 0
        entry = pool.current_entry
        print(f"{C.BOLD}{entry.masked}{C.RESET} {C.DIM}（{entry.source_label}）{C.RESET}")
        extra = f" / キー {pool.size} 本（{pool.summary()}）" if pool.size > 1 else ""
        print(f"{C.DIM}プロバイダ: {settings.effective_provider} / モデル: {settings.model}{extra}{C.RESET}")
        if settings.model_fallbacks:
            print(f"{C.DIM}429 のときの逃げ道: {', '.join(settings.model_fallbacks)}"
                  f"（--no-model-fallback で無効化）{C.RESET}")
        if pool.size > 1:
            print(f"{C.DIM}`actor key list` で全キーの状態を確認できます。{C.RESET}")
        return 0

    # ---- 追加（keys ディレクトリ or .env） ----
    if action == "add":
        api_key = _read_key_value(args, "追加する Gemini API キー（入力は表示されません）: ")
        if api_key is None:
            return 1
        code = _validate_key(api_key)
        if code:
            return code
        if any(entry.value == api_key for entry in pool.entries):
            print(f"{C.YELLOW}このキーは既に登録されています:{C.RESET} {mask_key(api_key)}")
            return 0
        if args.env:
            path = append_key_to_env_file(settings, api_key)
            where = f"{path} の {MULTI_KEY_ENV}"
        else:
            [path] = write_keys_dir(settings, [api_key])
            where = str(path)
        refresh_keys(settings)
        print(f"{C.GREEN}追加しました:{C.RESET} {where} {C.DIM}(chmod 600){C.RESET}")
        print(f"{C.DIM}登録 {settings.api_key_count} 本（{settings.key_pool().summary()}）"
              f" / `actor key list` で一覧を確認できます。{C.RESET}")
        _print_priority_hint(settings, api_key)
        if args.check:
            return _print_check_result(settings, api_key, index=settings.key_pool().current_index)
        return 0

    # ---- 保存（従来の set。優先度はいちばん低いが 1 本目として使える） ----
    if action == "set":
        api_key = _read_key_value(args, "Google AI Studio の API キー（入力は表示されません）: ")
        if api_key is None:
            return 1
        code = _validate_key(api_key)
        if code:
            return code
        saved = write_api_key(settings, api_key)
        refresh_keys(settings)
        print(f"{C.GREEN}保存しました:{C.RESET} {saved} {C.DIM}(chmod 600){C.RESET}")
        print(f"{C.DIM}この場所は .gitignore 済みなので、コミットには含まれません。{C.RESET}")
        _print_priority_hint(settings, api_key)
        if args.check:
            return _print_check_result(settings, api_key, index=0, saved=True)
        print(f"{C.DIM}`actor key show` で確認、`actor key set --check` で接続確認ができます。{C.RESET}")
        return 0

    # ---- 削除 ----
    if action in {"clear"} or (action in {"rm", "remove"} and (rest[:1] == ["all"] or not rest)):
        if action in {"rm", "remove"} and not args.yes and sys.stdin.isatty():
            answer = input(f"登録されているキー（{pool.size} 本）を全部消しますか？ [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                print(f"{C.DIM}中止しました。{C.RESET}")
                return 0
        removed = []
        if clear_api_key(settings):
            removed.append(str(settings.api_key_file or (settings.data_dir / "api_key")))
        for path in list_generated_key_files(settings):
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:  # pragma: no cover - 権限など
                pass
        refresh_keys(settings)
        if removed:
            print(f"{C.GREEN}削除しました{C.RESET}{C.DIM}: {', '.join(removed)}{C.RESET}")
            print(f"{C.DIM}.env と環境変数は触っていません（残っていればそのまま使われます）。{C.RESET}")
        else:
            print(f"{C.DIM}保存されたキーはありません{C.RESET}")
        return 0

    if action in {"rm", "remove"}:
        target = rest[0]
        try:
            index = int(target)
        except ValueError:
            print(f"{C.RED}番号を指定してください（`actor key list` の [n]）。{C.RESET}")
            return 2
        if not pool.size:
            _print_no_key()
            return 0
        if not 0 <= index < pool.size:
            print(f"{C.RED}番号が範囲外です（0〜{pool.size - 1}）。{C.RESET}")
            return 2
        entry = pool.entries[index]
        if entry.source == "keys" and entry.path:
            try:
                Path(entry.path).unlink()
            except OSError as exc:
                print(f"{C.RED}削除できませんでした: {exc}{C.RESET}")
                return 1
            refresh_keys(settings)
            print(f"{C.GREEN}削除しました:{C.RESET} {entry.masked} {C.DIM}({entry.path}){C.RESET}")
            print(f"{C.DIM}残り {settings.api_key_count} 本（{settings.key_pool().summary()}）{C.RESET}")
            return 0
        if entry.source == "file":
            clear_api_key(settings)
            refresh_keys(settings)
            print(f"{C.GREEN}削除しました:{C.RESET} {entry.masked} "
                  f"{C.DIM}({entry.path or entry.source_label}){C.RESET}")
            return 0
        if entry.source == ".env":
            env_path = settings.env_file or (PROJECT_ROOT / ".env")
            if remove_key_from_env_file(settings, entry.value, var=entry.label or MULTI_KEY_ENV):
                refresh_keys(settings)
                print(f"{C.GREEN}削除しました:{C.RESET} {entry.masked} "
                      f"{C.DIM}({env_path} の {entry.label or MULTI_KEY_ENV}){C.RESET}")
                print(f"{C.DIM}残り {settings.api_key_count} 本（{settings.key_pool().summary()}）{C.RESET}")
                return 0
            print(f"{C.RED}{env_path} を書き換えられませんでした（権限などを確認してください）。{C.RESET}")
            return 1
        where = {
            "env": f"環境変数 {entry.label}",
            "cli": "--api-key（起動時に渡した値）",
        }.get(entry.source, entry.source_label)
        print(f"{C.YELLOW}このキーは {where} から読まれているため、ここからは削除できません。{C.RESET}")
        print(f"{C.DIM}元の設定ファイル / シェルを編集してください（キー: {entry.masked}）。{C.RESET}")
        return 1

    # ---- 接続確認（1 本 / 全キー × モデル） ----
    if action == "check":
        if not pool.size:
            _print_no_key()
            return 1
        candidates = pool.status()
        if rest:
            try:
                index = int(rest[0])
            except ValueError:
                print(f"{C.RED}番号を指定してください（`actor key list` の [n]）。{C.RESET}")
                return 2
            if not 0 <= index < pool.size:
                print(f"{C.RED}番号が範囲外です（0〜{pool.size - 1}）。{C.RESET}")
                return 2
            candidates = [candidates[index]]
        models = pool.models if args.all else [settings.model]
        results = []
        for item in candidates:
            key = pool.entries[item["index"]].value
            for model in models:
                ok, detail = _probe_key(settings, key, model=model)
                results.append({**item, "ok": ok, "detail": detail, "checked_model": model})
        if args.as_json:
            print(json.dumps({"results": results, "count": len(results), "models": models},
                             ensure_ascii=False, indent=2))
        else:
            print(f"{C.BOLD}API キーの接続確認{C.RESET} {C.DIM}"
                  f"（キー {len(candidates)} 本 × モデル {len(models)} 個 / モデル一覧を引きます）{C.RESET}")
            for item in results:
                mark = f"{C.GREEN}✓{C.RESET}" if item["ok"] else f"{C.RED}✗{C.RESET}"
                print(f"  {mark} [{item['index']}] {item['masked']:<24} "
                      f"{item['checked_model']:<18} {item['detail']}")
            ok_count = sum(1 for item in results if item["ok"])
            if len(models) == 1:
                print(f"{C.DIM}{ok_count}/{len(results)} 本が応答しました。"
                      f"（逃げ道のモデルも見るなら --all）{C.RESET}")
            else:
                print(f"{C.DIM}{ok_count}/{len(results)} 通り（キー × モデル）が応答しました。{C.RESET}")
            if ok_count and any(not item["ok"] for item in results):
                print(f"{C.DIM}✗ のキー / モデルは 429 か、失効・未提供の可能性があります"
                      f"（`actor key stats` でモデル別の 429 を見られます）。{C.RESET}")
        return 0 if any(item["ok"] for item in results) else 1

    # ---- 統計（429 でよく切れるキーを教える） ----
    if action == "stats":
        if args.reset:
            pool.reset_stats()
            if args.as_json:
                print(json.dumps({"reset": True, **pool.stats()}, ensure_ascii=False, indent=2))
            else:
                print(f"{C.GREEN}統計をリセットしました{C.RESET}"
                      f"{C.DIM}（カウントと「制限中」の印を消しました）{C.RESET}")
            return 0
        stats = pool.stats()
        if args.as_json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
            return 0
        if not pool.size:
            _print_no_key()
            return 0
        totals = stats["totals"]
        print(f"{C.BOLD}API キーの利用統計{C.RESET} {C.DIM}"
              f"（{stats['state_path'] or 'まだ記録がありません'}）{C.RESET}")
        print(f"  通算: リクエスト {totals['requests']:,} 回 / "
              f"429 で切れた {totals['limits']:,} 回 / 切り替え {totals['rotations']:,} 回")
        by_model = stats["by_model"]
        if by_model:
            print(f"{C.BOLD}  モデル別{C.RESET}")
            for name, values in sorted(by_model.items(),
                                       key=lambda pair: (-pair[1].get("limits", 0), pair[0])):
                ready = stats["ready_by_model"].get(name)
                ready_label = f" / いま {ready}/{stats['count']} 本が使用可" if ready is not None else ""
                limits = values.get("limits", 0)
                color = C.YELLOW if limits else C.DIM
                print(f"    {name:<20} 429 {limits} 回 / リクエスト {values.get('requests', 0)} 回"
                      f"{color}{ready_label}{C.RESET}")
        used = [item for item in stats["keys"] if item["attempts"]]
        if not used:
            print(f"{C.DIM}まだ統計がありません（1 回話すと貯まります）。{C.RESET}")
            return 0
        print(f"{C.BOLD}  429 でよく切れる順{C.RESET}")
        for item in used:
            rate = f"{item['limit_rate'] * 100:.0f}%"
            if not item["ready"]:
                state = f"{item['status'] or 429} 制限中（あと {item['wait']:.0f} 秒）"
                color = C.YELLOW
            elif item["active"]:
                state, color = "使用中", C.GREEN
            else:
                state, color = "待機", C.DIM
            detail = ""
            if item["limits_by_model"]:
                inner = " / ".join(f"{name} {count}" for name, count in item["limits_by_model"].items())
                detail = f" {C.DIM}[{inner}]{C.RESET}"
            print(f"  [{item['index']}] {item['masked']:<24} 429 {item['limits']} 回 / "
                  f"試行 {item['attempts']} 回（{rate}）"
                  f" {C.DIM}成功 {item['uses']} / 失敗 {item['errors']} / "
                  f"最終 {_ago(item['last_used'])}{C.RESET} {color}{state}{C.RESET}{detail}")
            print(f"          {C.DIM}{item['label']}{' ' + item['path'] if item['path'] else ''}{C.RESET}")
        worst = stats["worst"]
        if worst and worst["limits"]:
            print(f"{C.YELLOW}よく切れているキー: [{worst['index']}] {worst['masked']}"
                  f"（429 {worst['limits']} 回 / 試行 {worst['attempts']} 回）{C.RESET}")
            if stats["count"] > 1:
                print(f"{C.DIM}同じキーを他のツール（Gemini アプリ・他のプロジェクト）と共有していないか確認。"
                      f"{C.RESET}")
            else:
                print(f"{C.DIM}キーが 1 本だけです。`actor key add` で足すと、429 のとき他のキーで続けられます。"
                      f"{C.RESET}")
        hot_model = max(stats["by_model"].items(), key=lambda pair: pair[1].get("limits", 0),
                        default=None)
        if hot_model and hot_model[1].get("limits"):
            print(f"{C.YELLOW}いちばん切れているモデル: {hot_model[0]}"
                  f"（429 {hot_model[1]['limits']} 回）{C.RESET}")
            chain = [name for name in settings.model_fallbacks if name != hot_model[0]]
            if chain:
                print(f"{C.DIM}このモデルが切れたら自動で逃げます: {', '.join(chain)}"
                      f"（--fallback-model で変更 / --no-model-fallback で停止）{C.RESET}")
            else:
                print(f"{C.DIM}別モデルへ逃がすには `--fallback-model gemini-3.5-flash` のように指定します。"
                      f"{C.RESET}")
        if stats["stale_records"]:
            print(f"{C.DIM}消したキーの記録が {stats['stale_records']} 件残っています"
                  f"（`actor key stats --reset` で消せます）。{C.RESET}")
        return 0

    # ---- キーの入手先（無料で用意できる場所） ----
    if action == "sources":
        from .keys import KEY_SOURCES_CAUTION, key_sources

        items = key_sources()
        if args.as_json:
            print(json.dumps({"sources": items, "caution": KEY_SOURCES_CAUTION},
                             ensure_ascii=False, indent=2))
            return 0
        print(f"{C.BOLD}キーを用意できる場所{C.RESET}{C.DIM}（枠は「プロジェクト × モデル」単位です）{C.RESET}")
        for item in items:
            print(f"  {C.CYAN}{item['name']}{C.RESET} {C.DIM}[{item['kind']}]{C.RESET}")
            if item["url"]:
                print(f"    {C.BOLD}{item['url']}{C.RESET}")
            print(f"    {item['note']}")
        print(f"{C.YELLOW}{KEY_SOURCES_CAUTION}{C.RESET}")
        print(f"{C.DIM}いま使っているキーの状態: actor key list / 429 の統計: actor key stats / "
              f"追加: actor key add{C.RESET}")
        return 0

    return 2


def _ago(timestamp: float) -> str:
    """「3 分前」のような相対表記（統計の最終使用時刻用）。"""
    if not timestamp:
        return "—"
    delta = max(0.0, time.time() - float(timestamp))
    if delta < 60:
        return f"{delta:.0f} 秒前"
    if delta < 3600:
        return f"{delta / 60:.0f} 分前"
    if delta < 86400:
        return f"{delta / 3600:.1f} 時間前"
    return f"{delta / 86400:.1f} 日前"


def _print_priority_hint(settings: Settings, api_key: str) -> None:
    """入れたキーより優先されるキーがあるとき（環境変数など）は、そのことを伝える。"""
    pool = settings.key_pool()
    active = pool.current_entry
    if not active or active.value == api_key:
        return
    print(f"{C.DIM}いまは {active.masked}（{active.source_label}）が優先されます"
          f"（--api-key > 環境変数 > .env > 保存ファイル > keys ディレクトリ）。{C.RESET}")


def _print_no_key() -> None:
    print(f"{C.YELLOW}API キー未設定{C.RESET}{C.DIM}（シミュレータで動作中）{C.RESET}")
    print(f"{C.DIM}設定方法: actor key set / actor key add / {PROJECT_ROOT}/.env に "
          f"GEMINI_API_KEY=...（複数は GEMINI_API_KEYS=a,b,c）{C.RESET}")
    print(f"{C.DIM}無料で用意できる場所: actor key sources（Google AI Studio ほか）{C.RESET}")


def _print_check_result(settings: Settings, api_key: str, *, index: int, saved: bool = False) -> int:
    """``key set --check`` / ``key add --check`` の共通処理。"""
    label = "保存" if saved else "追加"
    if settings.is_demo:
        print(f"{C.YELLOW}キーは{label}しましたが、まだシミュレータのままです（--provider を確認してください）。{C.RESET}")
        return 0
    print(f"{C.DIM}接続を確認しています…{C.RESET}")
    ok, detail = _probe_key(settings, api_key)
    if ok:
        print(f"{C.GREEN}API 接続 OK{C.RESET} [{index}] {mask_key(api_key)} — {detail}")
        return 0
    print(f"{C.RED}API に接続できませんでした{C.RESET} [{index}] {mask_key(api_key)} — {detail}")
    return 1


def cmd_models(args: argparse.Namespace) -> int:
    settings = load_settings(model=getattr(args, "model", None) or None)
    provider = get_provider(settings)
    if provider.is_mock:
        print(f"{C.DIM}API キー未設定のため、内蔵の候補を表示します。{C.RESET}")
        for name in KNOWN_MODELS:
            print(f"  {name}")
        return 0
    models = provider.model_catalog()
    if not models:
        print(f"{C.YELLOW}モデル一覧を取得できませんでした。{C.RESET}")
        return 1
    for name in models:
        print(f"  {name}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = make_settings(args)
    library = SkillLibrary(settings.skills_dirs)
    engine = ChatEngine(settings, library=library)
    ok = True
    print(f"{C.BOLD}Actor doctor{C.RESET}")
    print(f"  python          : {sys.version.split()[0]}")
    print(f"  actor           : {__version__} ({Path(__file__).parent})")
    print(f"  依存パッケージ   : なし（標準ライブラリのみ）")
    print(f"  プロバイダ       : {settings.effective_provider}" + (f" {C.YELLOW}(シミュレータ){C.RESET}" if settings.is_demo else ""))
    print(f"  モデル           : {settings.model}")
    pool = settings.key_pool()
    if settings.api_key:
        where = {"env": "環境変数", ".env": f"{PROJECT_ROOT}/.env", "file": str(settings.api_key_file),
                 "keys": str(settings.keys_path), "cli": "--api-key"}.get(settings.api_key_source,
                                                                          settings.api_key_source)
        print(f"  API キー         : 設定済み {C.DIM}({where}){C.RESET}")
        if pool.size > 1:
            print(f"  API キーの内訳   : {pool.summary()}")
            for item in pool.status():
                mark = "*" if item["active"] and item["ready"] else " "
                state = "使用中" if item["active"] and item["ready"] else (
                    f"{item['status'] or 429} 制限中（あと {item['wait']:.0f} 秒）" if not item["ready"] else "待機")
                print(f"    {mark}[{item['index']}] {item['masked']} "
                      f"{C.DIM}{state} / {item['label']}{C.RESET}")
            print("  自動ローテーション: " + ("有効" if settings.key_rotate else "無効")
                  + f"{C.DIM}（429 のあとは {settings.key_cooldown:.0f} 秒そのキーを避けます）{C.RESET}")
        chain = settings.model_fallbacks or []
        print("  モデルの逃げ道   : " + (", ".join(chain) if chain else "なし（--no-model-fallback）")
              + f"{C.DIM}（429 のときだけ使います）{C.RESET}" if chain else
              "  モデルの逃げ道   : なし（ACTOR_MODEL_FALLBACKS= / --no-model-fallback）")
        if settings.api_version != "v1beta" or "aiplatform" in settings.base_url:
            print(f"  API の形         : {settings.api_version} / {settings.model_path}"
                  f"{C.DIM}（Vertex エクスプレスモード向け）{C.RESET}")
        totals = pool.totals
        if totals.get("limits") or totals.get("requests"):
            worst = pool.stats()["worst"]
            hint = ""
            if worst and worst["limits"]:
                hint = f" / よく切れる: [{worst['index']}] {worst['masked']}（429 {worst['limits']} 回）"
            print(f"  キーの統計       : リクエスト {totals.get('requests', 0):,} 回 / "
                  f"429 で切れた {totals.get('limits', 0):,} 回 / 切り替え {totals.get('rotations', 0):,} 回"
                  f"{C.DIM}{hint}（actor key stats）{C.RESET}")
            by_model = pool.stats()["by_model"]
            hot = [f"{name} {values.get('limits', 0)} 回" for name, values in by_model.items()
                   if values.get("limits")]
            if hot:
                print(f"  切れやすいモデル : {', '.join(hot)}")
    else:
        print(f"  API キー         : {C.YELLOW}未設定{C.RESET} {C.DIM}"
              f"（actor key set または {PROJECT_ROOT}/.env に GEMINI_API_KEY=...）{C.RESET}")
    for directory in settings.skills_dirs:
        count = sum(1 for s in library if s.source == str(directory))
        state = "✓" if directory.is_dir() else f"{C.YELLOW}未作成{C.RESET}"
        print(f"  スキルフォルダ   : {directory} {state} ({count} 件)")
    print(f"  データフォルダ   : {settings.data_dir}")
    print(f"  セッション数     : {len(engine.store.list(limit=10000))}")
    rooms = RoomStore(settings.data_dir / "rooms").list()
    print(f"  ルーム           : {len(rooms)} 件")
    try:
        from .genai_provider import GenAiProvider, available, sdk_version

        state = f"利用可 (v{sdk_version()})" if available() else "未導入（--provider gemini で動作）"
    except Exception:  # pragma: no cover
        state = "利用可" if False else "未導入"
    print(f"  google-genai SDK : {state}")
    tools = engine.tools
    print(f"  道具（ツール）   : {len(tools)} 件 ({', '.join(tools.names()) or 'なし'})"
          f" write={'on' if settings.tools_allow_write else 'off'}"
          f" network={'on' if settings.tools_allow_network else 'off'}")
    print(f"  添付の上限       : {settings.max_attachments} 件 / "
          f"{settings.max_attachment_bytes / 1048576:.0f}MB"
          f" / 保存={'する' if settings.store_attachments else 'しない'}")
    print(f"  使用プロバイダ   : {settings.effective_provider}")
    if library.errors:
        ok = False
        for error in library.errors:
            print(f"  {C.RED}! {error['path']}: {error['error']}{C.RESET}")
    if args.check_api:
        if settings.is_demo:
            print(f"  {C.YELLOW}--check-api には API キーが必要です。{C.RESET}")
        else:
            models = engine.provider.model_catalog()
            if models:
                print(f"  {C.GREEN}API 接続 OK{C.RESET} ({len(models)} models)")
                if settings.model not in models:
                    print(f"  {C.YELLOW}! '{settings.model}' は一覧にありません。候補: {', '.join(models[:5])}{C.RESET}")
            else:
                ok = False
                print(f"  {C.RED}API に接続できませんでした。API キーとネットワークを確認してください。{C.RESET}")
    return 0 if ok else 1


def cmd_tools(args: argparse.Namespace) -> int:
    settings = make_settings(args)
    library = SkillLibrary(settings.skills_dirs)
    engine = ChatEngine(settings, library=library)
    registry = engine.tools

    if args.run:
        pairs = {}
        for token in args.args or []:
            key, _, value = str(token).partition("=")
            pairs[key] = value
        result = registry.execute(args.run, pairs)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1

    if args.as_json:
        print(json.dumps({
            "tools": registry.describe(),
            "names": registry.names(),
            "root": str(settings.tools_root),
            "allow_write": settings.tools_allow_write,
            "allow_network": settings.tools_allow_network,
            "max_rounds": settings.max_tool_rounds,
        }, ensure_ascii=False, indent=2))
        return 0

    print_tools(registry)
    print(f"{C.DIM}root={settings.tools_root} / write={'on' if settings.tools_allow_write else 'off'}"
          f" / network={'on' if settings.tools_allow_network else 'off'}"
          f" / 最大ラウンド {settings.max_tool_rounds}{C.RESET}")
    print(f"{C.DIM}スキルが allowed-tools を宣言していると、その和集合だけが渡されます。{C.RESET}")
    declared = {s.name: list(s.allowed_tools) for s in library.all() if s.allowed_tools}
    if declared:
        print(f"{C.BOLD}スキルごとの指定{C.RESET}")
        for name, names in declared.items():
            print(f"  {C.CYAN}{name}{C.RESET}: {', '.join(names)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_split_command(raw))
    command = getattr(args, "command", None) or "chat"

    if command == "help":
        parser.print_help()
        return 0
    if command == "version":
        print(f"Actor {__version__}")
        return 0
    try:
        if command == "chat":
            return cmd_chat(args)
        if command == "serve":
            return cmd_serve(args)
        if command == "skills":
            return cmd_skills(args)
        if command == "rooms":
            return cmd_rooms(args)
        if command == "bot":
            return cmd_bot(args)
        if command == "tools":
            return cmd_tools(args)
        if command == "key":
            return cmd_key(args)
        if command == "models":
            return cmd_models(args)
        if command == "doctor":
            return cmd_doctor(args)
    except KeyboardInterrupt:
        print()
        return 130
    except ProviderError as exc:
        print(f"{C.RED}API エラー: {exc}{C.RESET}")
        return 1
    except Exception as exc:
        if getattr(args, "verbose", False):
            raise
        print(f"{C.RED}{type(exc).__name__}: {exc}{C.RESET}")
        print(f"{C.DIM}詳細は -v を付けて再実行してください。{C.RESET}")
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
