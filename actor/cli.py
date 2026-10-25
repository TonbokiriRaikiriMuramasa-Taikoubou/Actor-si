"""Actor command-line interface.

Examples::

    python -m actor                           # Start an interactive chat
    python -m actor serve --port 8080         # Start only the Web UI and API server
    python -m actor "request text"             # Run one request and exit
    python -m actor skills list               # List skills
    python -m actor doctor                    # Check the environment
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

# ---- Terminal output -------------------------------------------------
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


BANNER = f"""{C.CYAN}{C.BOLD}Actor{C.RESET} {C.DIM}v{__version__} — Gemini chat powered by SKILL.md{C.RESET}
{C.DIM}Type /help for commands or /exit to quit.{C.RESET}"""

HELP_TEXT = f"""{C.BOLD}Chat{C.RESET}
  Type a request                 Send a message (skills are selected automatically)
  Start with /                   Show skill-name suggestions
  /use <name...>                 Pin skills (/use none clears pinned skills)
  /auto on|off                   Toggle automatic skill selection
  /think on|off                  Toggle the thought log (thinkingConfig)
  /stream on|off                 Toggle streaming
  /model <name>                  Change the model
  /system                        Show the current system instruction
  /show                          Show details for the most recently applied skills

{C.BOLD}Resident bots and rooms{C.RESET}
  actor bot --room <name> --skills minutes-writer   Monitor a room and reply to messages
  actor rooms list / show <name>          List rooms or show room contents
  actor rooms say "Hello" --reply         Post a message and request a server-side reply
  --token <value> / ACTOR_ROOM_TOKEN      Connect to a token-protected room

{C.BOLD}Tools and attachments{C.RESET}
  The model can call tools such as now, read_file, and list_files (shown with 🛠)
  actor tools                             List available tools (--json / --run NAME key=value)
  --attach ./diagram.png                 Attach images or PDFs (multiple files, up to 10 MB each)

{C.BOLD}Sessions{C.RESET}
  /new [title]                  Start a new chat
  /sessions                     List saved sessions
  /load <id>                    Load a session
  /title <text>                 Change the title

{C.BOLD}Skills{C.RESET}
  /skills                       List skills and scores
  /<skill-name>                 Pin a skill and send a request (e.g. /minutes meeting notes)
  /create <skill description>   Generate and save a SKILL.md from the conversation
  /export [name...]             Create a ZIP that can be uploaded to Gemini

{C.BOLD}API keys{C.RESET}
  actor key list                List registered keys and status (including cooldown; --json available)
  actor key add                 Add a key (.actor/keys/; use --env to append to .env)
  actor key rm <number>          Remove a key (including keys stored in .env)
  actor key check               Check each key's connectivity
  actor key stats               View rate-limited keys and models (--json / --reset available)
  actor key sources             Find free key options (AI Studio / Vertex Express, etc.)
  GEMINI_API_KEYS=key1,key2     Provide multiple keys through the environment or .env; rotate on 429
  --fallback-model <name>       Try another model after repeated 429s (default: gemini-3.5-flash,gemini-2.5-flash)

{C.BOLD}Other{C.RESET}
  /help                         Show this help
  /exit or Ctrl-D               Quit
"""


# ---- Arguments -------------------------------------------------------
def add_common_options(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("Common options")
    g.add_argument("--api-key", help="Gemini API key (default: GEMINI_API_KEY environment variable)")
    g.add_argument("--provider", choices=["auto", "gemini", "mock"], help="Provider (auto selects based on whether a key is configured)")
    g.add_argument("--model", help=f"Model name (default: gemini-3-flash / examples: {', '.join(KNOWN_MODELS[:4])})")
    g.add_argument("--fallback-model", action="append", dest="fallback_models", metavar="NAME",
                   help="Fallback model to try after a 429 (repeatable; defaults to gemini-3.5-flash,gemini-2.5-flash)")
    g.add_argument("--no-model-fallback", action="store_true",
                   help="Do not fall back to another model after a 429")
    g.add_argument("--base-url", help="Base URL for the Gemini API")
    g.add_argument("--skills-dir", action="append", dest="skills_dirs",
                   help="Skill directory (repeatable; default: ./skills)")
    g.add_argument("--data-dir", help="Directory for sessions and other data (default: ./.actor)")
    g.add_argument("--temperature", type=float)
    g.add_argument("--max-output-tokens", type=int)
    g.add_argument("--thinking-budget", type=int, help="Maximum thinking tokens (0 disables thinking)")
    g.add_argument("--timeout", type=float)
    g.add_argument("--no-auto", action="store_true", help="Disable automatic skill selection")
    g.add_argument("--env-file", help="Path to the .env file (default: ./.env)")
    g.add_argument("-v", "--verbose", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="actor",
        description="Chat with Gemini using SKILL.md as the system instruction",
    )
    parser.add_argument("--version", action="version", version=f"Actor {__version__}")
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="Interactive chat (default)")
    add_common_options(chat)
    chat.add_argument("prompt", nargs="*", help="Request to run once (omit to start interactive chat)")
    chat.add_argument("-p", "--print", action="store_true", dest="oneshot", help="Print the result and exit")
    chat.add_argument("--json", action="store_true", dest="as_json", help="Print the result as JSON")
    chat.add_argument("--session", help="Session ID to continue")
    chat.add_argument("--use", dest="use_skills", action="append", help="Skill to pin for this chat")
    chat.add_argument("--attach", action="append", default=[], metavar="PATH",
                      help="Attach images, PDFs, or other files (repeatable)")
    chat.add_argument("--no-banner", action="store_true")

    # Use positional arguments instead of nested subcommands to allow options in any order.
    skills = sub.add_parser("skills", help="Manage skills")
    add_common_options(skills)
    skills.add_argument("args", nargs="*", help="[list|show|new|rm|export|lint|test|path] [name...]")
    skills.add_argument("--description", default="", help="Description for new")
    skills.add_argument("--body", help="Read the new skill body from a file")
    skills.add_argument("--overwrite", action="store_true", help="Overwrite an existing skill with new")
    skills.add_argument("--yes", action="store_true", help="Skip confirmation for rm")
    skills.add_argument("--json", action="store_true", dest="as_json", help="Print show output as JSON")
    skills.add_argument("-o", "--output", help="Output path for export")
    skills.add_argument("--target", choices=("gemini", "portable", "agensi"), default="gemini", help="Format for export/lint")
    skills.add_argument("--keep-allowed-tools", action="store_true", help="Include allowed-tools in portable lint/export")
    skills.add_argument("--force", action="store_true", help="Export even if lint fails (review required)")
    skills.add_argument("--live", action="store_true", help="Use the live API for skills test (may incur charges)")

    # `actor rooms [list|show|new|rm|say] [name/message...]`
    rooms = sub.add_parser("rooms", help="Rooms (shared logs for resident bots)")
    add_common_options(rooms)
    rooms.add_argument("args", nargs="*", help="[list|show|new|rm|say] [name/message...]")
    rooms.add_argument("--room", default=DEFAULT_ROOM, help="Room to use with show / say")
    rooms.add_argument("--reply", action="store_true", help="Generate a server-side reply for say")
    rooms.add_argument("--author", default="user", help="Author name for say")
    rooms.add_argument("--after", type=int, default=0, help="Starting sequence number for show")
    rooms.add_argument("--api", default="http://127.0.0.1:8080", help="Server URL for say")
    rooms.add_argument("--attach", action="append", default=[], metavar="PATH",
                       help="Attach images or PDFs with say (repeatable)")
    rooms.add_argument("--token", default="", help="Room token (defaults to ACTOR_ROOM_TOKEN)")
    rooms.add_argument("--skills", action="append", dest="room_skills", default=[], help="Skills to pin for new")
    rooms.add_argument("--yes", action="store_true", help="Skip confirmation for rm")

    # Resident bot
    bot = sub.add_parser("bot", help="Resident bot that monitors a room and replies")
    add_common_options(bot)
    bot.add_argument("--room", default=DEFAULT_ROOM, help="Room to monitor")
    bot.add_argument("--api", default="http://127.0.0.1:8080", help="Server URL")
    bot.add_argument("--name", default="Actor", help="Bot name shown in the room")
    bot.add_argument("--skills", action="append", dest="use_skills", help="Skills to pin (repeatable)")
    bot.add_argument("--interval", type=float, default=1.5, help="Polling interval in seconds")
    bot.add_argument("--mention", default="", help="Reply only to messages containing this text")
    bot.add_argument("--stream", action="store_true", help="Stream generated text to the terminal")
    bot.add_argument("--once", action="store_true", help="Process once and exit (for cron, etc.)")
    bot.add_argument("--from-now", action="store_true", help="Ignore room history and respond only to new messages")

    tools = sub.add_parser("tools", help="List available tools (function calling)")
    add_common_options(tools)
    tools.add_argument("--json", action="store_true", dest="as_json", help="Print as JSON")
    tools.add_argument("--run", metavar="NAME", help="Run a tool directly (for diagnostics)")
    tools.add_argument("args", nargs="*", help="Arguments for --run (key=value)")

    serve = sub.add_parser("serve", help="Web UI and HTTP API server")
    add_common_options(serve)
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--open", action="store_true", help="Open the browser after starting")

    key = sub.add_parser("key", help="Manage Gemini API keys (multiple keys and rotation supported)")
    add_common_options(key)
    key.add_argument("args", nargs="*",
                     help="[show|list|stats|set|add|rm|clear|check|sources] [number / all]")
    key.add_argument("--value", help="Pass a key directly (not recommended; may be saved in shell history)")
    key.add_argument("--stdin", action="store_true", help="Read from standard input")
    key.add_argument("--env", action="store_true",
                     help="Append with add to GEMINI_API_KEYS in .env (default: .actor/keys/)")
    key.add_argument("--reset", action="store_true", help="Reset stats counters")
    key.add_argument("--sources", action="store_true", help="Show places to obtain a key for free")
    key.add_argument("--all", action="store_true", help="Also check fallback models")
    key.add_argument("--check", action="store_true", help="Check API connectivity after saving or adding a key")
    key.add_argument("--json", action="store_true", dest="as_json", help="Print list / check / stats as JSON")
    key.add_argument("--yes", action="store_true", help="Skip confirmation for rm all")

    sub.add_parser("models", help="List available models (requires API access)").add_argument("--all", action="store_true")

    doctor = sub.add_parser("doctor", help="Check the environment")
    add_common_options(doctor)
    doctor.add_argument("--check-api", action="store_true", help="Connect to the live API for verification")

    sub.add_parser("help", help="Show help")
    sub.add_parser("version", help="Show version")
    return parser


# Boolean flags do not consume the following token as a value.
BOOL_FLAGS = {
    "-h", "--help", "--version", "-v", "--verbose", "--no-auto", "-p", "--print", "--json",
    "--open", "--no-banner", "--all", "--check-api", "--overwrite", "--yes", "--full",
    "--reply", "--stream", "--once", "--from-now", "--json",
    "--env", "--reset", "--stdin", "--check", "--no-model-fallback", "--all", "--sources",
}


def _split_command(argv: list[str]) -> list[str]:
    """Insert chat when no subcommand is specified.

    Examples: ``actor doctor`` → ``["doctor"]`` / ``actor --model x serve`` → ``["serve", "--model", "x"]``
    / ``actor "hello"`` → ``["chat", "hello"]``
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
        break  # The first non-option token is a request, so treat it as chat
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
        env_file = None       # If .env does not exist, keep the default path; key add --env creates it
    return load_settings(env_file=env_file, **overrides)


# ---- Display helpers -------------------------------------------------
def print_skills(library: SkillLibrary, query: str = "", scores: dict[str, float] | None = None) -> None:
    skills = library.all()
    if not skills:
        print(f"{C.YELLOW}No skills found.{C.RESET} Directories: " + ", ".join(str(d) for d in library.dirs))
        print(f"{C.DIM}  Create one with actor skills new my-skill --description 'description'.{C.RESET}")
        return
    ordered = skills
    if scores:
        ordered = sorted(skills, key=lambda s: (-scores.get(s.name, 0.0), s.name))
    print(f"{C.BOLD}{len(skills)} skill(s){C.RESET} {C.DIM}({', '.join(str(d) for d in library.dirs)}){C.RESET}")
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
        print(f"{C.MAGENTA}▶ Applied skills:{C.RESET} " + "  ".join(parts))
    else:
        print(f"{C.DIM}▶ Applied skills: none (below the auto-selection threshold){C.RESET}")
    for missing in event.get("missing", []):
        print(f"{C.YELLOW}  ! Skill '{missing}' not found{C.RESET}")
    print(f"{C.DIM}  system_instruction: {event.get('system_chars', 0):,} characters ≈ {event.get('system_tokens', 0):,} tokens{C.RESET}")


# ---- Chat ------------------------------------------------------------
def load_attachments(paths: Sequence[str], settings: Settings) -> list[dict[str, Any]]:
    """Convert paths passed with ``--attach`` into base64-encoded attachments."""
    import base64
    import mimetypes

    files: list[dict[str, Any]] = []
    for raw in list(paths)[: settings.max_attachments]:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Attachment not found: {path}")
        blob = path.read_bytes()
        if len(blob) > settings.max_attachment_bytes:
            raise ValueError(
                f"Attachment '{path.name}' is too large"
                f"({len(blob) / 1048576:.1f} MB; limit {settings.max_attachment_bytes / 1048576:.0f} MB)"
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
    print(f"{C.BOLD}{len(registry)} tool(s){C.RESET}")
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

    # ---- Prompts ----
    def prompt_string(self) -> str:
        pinned = ",".join(self.session.skills) if self.session.skills else "-"
        flags = []
        if self.settings.auto_select:
            flags.append("auto")
        if self.show_thoughts:
            flags.append("think")
        suffix = f" {C.DIM}[{pinned}|{'|'.join(flags) or 'manual'}]{C.RESET}"
        return f"{C.GREEN}you{C.RESET}{suffix} {C.DIM}›{C.RESET} "

    # ---- Main loop ----
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
        """Return True to exit."""
        parts = line[1:].split()
        cmd, rest = (parts[0] if parts else ""), parts[1:]

        if cmd in {"exit", "quit", "q"}:
            print(f"{C.DIM}Goodbye.{C.RESET}")
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
                print(f"{C.DIM}Cleared pinned skills.{C.RESET}")
            else:
                found, missing = self.engine.library.resolve(rest)
                self.session.skills = [s.name for s in found]
                print(f"{C.DIM}Pinned: {', '.join(self.session.skills) or 'none'}{C.RESET}")
                for name in missing:
                    print(f"{C.YELLOW}  ! Skill '{name}' not found{C.RESET}")
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
            print(f"{C.DIM}Auto-select skills: {'ON' if self.settings.auto_select else 'OFF'}{C.RESET}")
            return False
        if cmd == "think":
            value = (rest[0] if rest else "toggle").lower()
            self.show_thoughts = not self.show_thoughts if value not in {"on", "off"} else value == "on"
            if self.show_thoughts and self.settings.thinking_budget <= 0:
                self.settings.thinking_budget = 1024
                print(f"{C.DIM}Set thinkingBudget to 1024.{C.RESET}")
            print(f"{C.DIM}Thought log: {'ON' if self.show_thoughts else 'OFF'}{C.RESET}")
            return False
        if cmd == "stream":
            value = (rest[0] if rest else "toggle").lower()
            self.stream = not self.stream if value not in {"on", "off"} else value == "on"
            print(f"{C.DIM}Streaming: {'ON' if self.stream else 'OFF'}{C.RESET}")
            return False
        if cmd == "model":
            if rest:
                self.settings.model = rest[0]
                self.engine.provider.settings = self.settings
                print(f"{C.DIM}Model: {rest[0]}{C.RESET}")
            else:
                print(f"{C.DIM}Current model: {self.settings.model}{C.RESET}")
            return False
        if cmd in {"system", "prompt"}:
            print(self.last_system or f"{C.DIM}(No message sent yet){C.RESET}")
            return False
        if cmd in {"show", "info"}:
            if not self.last_selection:
                print(f"{C.DIM}(No message sent yet){C.RESET}")
            else:
                print(json.dumps(self.last_selection, ensure_ascii=False, indent=2))
            return False
        if cmd == "new":
            self.session = self.engine.store.create(title=" ".join(rest) or "New chat", model=self.settings.model)
            print(f"{C.DIM}New chat: {self.session.id}{C.RESET}")
            return False
        if cmd == "sessions":
            for item in self.engine.store.list():
                mark = "*" if item["id"] == self.session.id else " "
                print(f"{mark} {C.CYAN}{item['id']}{C.RESET} {item['title']}  {C.DIM}{item['updated_at']} ({item['turn_count']}turn){C.RESET}")
            return False
        if cmd == "load":
            if not rest:
                print(f"{C.RED}Usage: /load <id>{C.RESET}")
                return False
            try:
                self.session = self.engine.store.require(rest[0])
                print(f"{C.DIM}Loaded: {self.session.title} ({self.session.turn_count} turn){C.RESET}")
            except KeyError as exc:
                print(f"{C.RED}{exc}{C.RESET}")
            return False
        if cmd == "title":
            self.session.title = " ".join(rest) or self.session.title
            self.engine.store.save(self.session)
            print(f"{C.DIM}Title: {self.session.title}{C.RESET}")
            return False
        if cmd == "create":
            if not rest:
                print(f"{C.RED}Usage: /create a skill that formats meeting minutes consistently{C.RESET}")
                return False
            self.create_skill(" ".join(rest))
            return False
        if cmd == "export":
            self.export_zip(rest)
            return False
        if not rest:
            # Shortcut for /<skill-name>
            if self.engine.library.get(slugify(cmd)):
                print(f"{C.DIM}Pinned skill '{slugify(cmd)}'. Enter a message to send.{C.RESET}")
                self.session.skills = [slugify(cmd)]
                self.engine.store.save(self.session)
                return False
            print(f"{C.YELLOW}Unknown command: /{cmd} (see /help){C.RESET}")
            return False
        # Run one request with /<skill-name> <request>
        skill = self.engine.library.get(slugify(cmd))
        if skill:
            self.session.skills = [skill.name]
            self.send(" ".join(rest), explicit=[skill.name])
            return False
        print(f"{C.YELLOW}Unknown command: /{cmd} (see /help){C.RESET}")
        return False

    # ---- Send ------------------------------------------------------------
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
                    state = "" if event.get("ok", True) else f"{C.RED}(failed) {C.RESET}"
                    print(f"{C.DIM}   ↳ {state}{snippet}{C.RESET}")
                elif etype == "tool_limit":
                    print(f"{C.YELLOW}Maximum tool-call rounds reached.{C.RESET}")
                elif etype == "error":
                    print(f"\n{C.RED}Error: {event['text']}{C.RESET}")
                elif etype == "done":
                    final = event
        except ProviderError as exc:
            print(f"{C.RED}Provider error: {exc}{C.RESET}")
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

    # ---- Skill generation / export ----
    def create_skill(self, request: str) -> None:
        print(f"{C.DIM}Designing skill…{C.RESET}")
        draft = self.engine.draft_skill(request)
        print(f"\n{C.BOLD}name{C.RESET}:        {C.CYAN}{draft.name}{C.RESET}")
        print(f"{C.BOLD}description{C.RESET}: {draft.description}")
        print(f"{C.DIM}{'-' * 60}{C.RESET}")
        print(draft.instructions)
        print(f"{C.DIM}{'-' * 60}{C.RESET}")
        try:
            answer = input(f"Save? [Y/n/e=edit] {C.DIM}›{C.RESET} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer in {"n", "no"}:
            print(f"{C.DIM}Discarded.{C.RESET}")
            return
        if answer in {"e", "edit"}:
            print(f"{C.DIM}Enter the body (two blank lines to finish):{C.RESET}")
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
            print(f"{C.RED}Could not save: {exc}{C.RESET}")
            return
        print(f"{C.GREEN}Saved:{C.RESET} {skill.path}")
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
        print(f"{C.GREEN}Exported:{C.RESET} {out} ({len(data):,} bytes)")
        print(f"{C.DIM}Upload this file in the Gemini app under Settings → Skills → Upload.{C.RESET}")


# ---- Commands -------------------------------------------------------
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
            print(f"{C.RED}No request provided.{C.RESET}")
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
        print(f"{C.DIM}Loaded attachments: {', '.join(Path(a).name for a in args.attach)}{C.RESET}")
    return repl.run()


def _print_mode(settings: Settings, engine: ChatEngine) -> None:
    provider = engine.provider
    if provider.is_mock:
        print(f"{C.YELLOW}Running in simulation mode{C.RESET}{C.DIM} — set GEMINI_API_KEY to switch to a live model.{C.RESET}")
    else:
        pool = settings.key_pool()
        extra = f" / {pool.size} keys ({pool.summary()})" if pool.size > 1 else ""
        print(f"{C.DIM}Gemini connection: {settings.model} / key {mask_key(settings.api_key)}{extra}{C.RESET}")
        if pool.size > 1:
            print(f"{C.DIM}Automatically switches to another key after a 429 (status: actor key list){C.RESET}")
    print(f"{C.DIM}{len(engine.library)} skill(s) / data directory {settings.data_dir}{C.RESET}")


def cmd_serve(args: argparse.Namespace) -> int:
    settings = make_settings(args)
    try:
        from .server import run_server
    except ImportError as exc:  # pragma: no cover
        print(f"{C.RED}Could not start server: {exc}{C.RESET}")
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
            exists = "✓" if directory.is_dir() else "(not created)"
            print(f"{directory} {exists}")
        return 0
    if sub == "show":
        if not names:
            print(f"{C.RED}Usage: actor skills show <name>{C.RESET}")
            return 2
        skill = library.get(slugify(names[0]))
        if not skill:
            print(f"{C.RED}'{names[0]}' not found.{C.RESET}")
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
            print(f"{C.YELLOW}Parameter overrides: {pairs}{C.RESET}")
        for warning in skill.warnings:
            print(f"{C.YELLOW}! {warning}{C.RESET}")
        print()
        print(skill.instructions)
        if skill.references:
            print(f"\n{C.BOLD}Reference files{C.RESET}")
            for ref in skill.references:
                note = f" {C.RED}{ref.error}{C.RESET}" if ref.error else ""
                print(f"  {ref.relpath} ({ref.kind}, {ref.size:,} bytes){note}")
        return 0
    if sub == "new":
        if not names:
            print(f"{C.RED}Usage: actor skills new <name> --description 'description'{C.RESET}")
            return 2
        body = Path(args.body).read_text(encoding="utf-8") if args.body else None
        if body is None:
            body = "# Purpose\n(What this task should accomplish)\n\n# Input\n- The source material and any requirements or constraints.\n\n# Procedure\n1. Confirm the goal and required deliverable.\n2. Review the supplied material.\n3. Ask about missing or conflicting information instead of guessing.\n4. Produce the result in the format below.\n5. Review the result against the request.\n\n# Output format\n```markdown\n## Result\n\n## Details\n\n## Next steps\n```\n\n# Checklist\n- Do not invent information.\n"
        try:
            skill = library.create(names[0], args.description, body, overwrite=args.overwrite)
        except (FileExistsError, ValueError) as exc:
            print(f"{C.RED}{exc}{C.RESET}")
            return 1
        print(f"{C.GREEN}Created:{C.RESET} {skill.path}")
        return 0
    if sub == "rm":
        if not names:
            print(f"{C.RED}Usage: actor skills rm <name>{C.RESET}")
            return 2
        skill = library.get(slugify(names[0]))
        if not skill:
            print(f"{C.RED}'{names[0]}' not found.{C.RESET}")
            return 1
        if not args.yes:
            try:
                answer = input(f"Delete '{skill.name}'? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return 1
            if answer not in {"y", "yes"}:
                print(f"{C.DIM}Cancelled.{C.RESET}")
                return 1
        library.delete(skill.name)
        print(f"{C.GREEN}Deleted:{C.RESET} {skill.name}")
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
        print(f"{C.GREEN}Exported:{C.RESET} {out} ({len(data):,} bytes)")
        if args.target in {"portable", "agensi"}:
            print("ZIP layout: <name>/SKILL.md, references/, LICENSE. Submit to Agensi manually if desired.")
        return 0
    print(f"{C.RED}Unknown subcommand: {sub}{C.RESET}{C.DIM} (list / show / new / rm / export / lint / test / path){C.RESET}")
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
            print(f"{C.YELLOW}No rooms found.{C.RESET}{C.DIM}Create one with actor rooms new my-room.{C.RESET}")
            return 0
        print(f"{C.BOLD}{len(rooms)} room(s){C.RESET} {C.DIM}({settings.data_dir / 'rooms'}){C.RESET}")
        for room in rooms:
            skills = f" {C.DIM}[{', '.join(room['skills'])}]{C.RESET}" if room["skills"] else ""
            print(f"  {C.CYAN}{room['name']}{C.RESET}  {room['count']} message(s){skills}  {C.DIM}{room['updated_at']}{C.RESET}")
            if room["preview"]:
                print(f"    {C.DIM}{room['preview']}{C.RESET}")
        return 0

    if sub == "show":
        name = rest[0] if rest else args.room
        room = store.get(name)
        if room is None:
            print(f"{C.RED}Room '{name}' not found.{C.RESET}")
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
            print(f"{C.RED}Room names must be at most 32 characters and contain only letters, numbers, hyphens, or underscores.{C.RESET}")
            return 1
        room = store.create(name, skills=list(args.room_skills or []))
        print(f"{C.GREEN}Created room:{C.RESET} {room.name}")
        return 0

    if sub == "rm":
        name = rest[0] if rest else args.room
        if not args.yes:
            try:
                answer = input(f"Delete room '{name}'? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return 1
            if answer not in {"y", "yes"}:
                print(f"{C.DIM}Cancelled.{C.RESET}")
                return 1
        if store.delete(name):
            print(f"{C.GREEN}Deleted:{C.RESET} {name}")
            return 0
        print(f"{C.RED}Room '{name}' not found.{C.RESET}")
        return 1

    if sub == "say":
        from .bot import RoomClient

        text = " ".join(rest)
        if not text.strip():
            print(f"{C.RED}Usage: actor rooms say 'Hello' --reply{C.RESET}")
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
            # If the server is unavailable, write directly to the local room store.
            room = store.require(args.room)
            room.append("user", text, author=args.author)
            store.save(room)
            print(f"{C.YELLOW}Could not connect to the server; wrote to the local room store.{C.RESET} {C.DIM}({exc}){C.RESET}")
            print(f"{C.CYAN}{args.author}{C.RESET}: {text}")
            print(f"{C.DIM}To get a reply, start actor serve and add --reply.{C.RESET}")
            return 0
        print(f"{C.CYAN}{args.author}{C.RESET}: {text}")
        if result.get("reply"):
            print(f"{C.GREEN}Actor{C.RESET}: {result['reply']['text']}")
        return 0

    print(f"{C.RED}Unknown subcommand: {sub}{C.RESET}{C.DIM} (list / show / new / rm / say){C.RESET}")
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
    """Read one key from --value, --stdin, a pipe, or interactive input, in that order."""
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
    """Validate the key format (return None when valid, or an exit code on failure)."""
    if not api_key:
        print(f"{C.RED}API key is empty.{C.RESET}")
        return 2
    if any(ch.isspace() for ch in api_key):
        print(f"{C.RED}API key contains whitespace. Copy it again without spaces.{C.RESET}")
        return 2
    if len(api_key) < 20:
        print(f"{C.RED}API key is too short. Paste a key issued by Google AI Studio.{C.RESET}")
        return 2
    return None


def _probe_key(settings: Settings, api_key: str, *, model: str | None = None) -> tuple[bool, str]:
    """Fetch the model catalog with a single key to verify connectivity without rotation."""
    from dataclasses import replace

    probe = replace(settings, api_key=api_key, api_keys=[api_key], key_entries=[], key_rotate=False,
                    model_fallbacks=[], model=model or settings.model)
    provider = get_provider(probe)
    if provider.is_mock:
        return False, "Provider is mock (check --provider / ACTOR_GEMINI_IMPL)."
    try:
        models = provider.model_catalog(strict=True)     # Preserve status details such as 429 responses.
    except ProviderError as exc:
        detail = str(exc).splitlines()[0]
        if exc.status and not detail.startswith(str(exc.status)):
            detail = f"{exc.status} {detail}"
        return False, detail
    if models:
        return True, f"OK ({len(models)} models)"
    return False, "Could not connect (check the key and network)."


def cmd_key(args: argparse.Namespace) -> int:
    """Manage API keys (``show`` / ``list`` / ``set`` / ``add`` / ``rm`` / ``clear`` / ``check``).

    Multiple keys can be stored under ``.actor/keys/`` (``actor key add``) or in ``GEMINI_API_KEYS``.
    After a 429, Actor tries the next key automatically. Use ``actor key list`` to inspect key status.
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
        print(f"{C.RED}Unknown action: {action}{C.RESET}")
        print(f"{C.DIM}Available actions: show / list / set / add / rm / clear / check.{C.RESET}")
        return 2
    pool = settings.key_pool()

    # ---- List ------------------------------------------------------------
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
        chain_label = f" / fallback models: {', '.join(chain)}" if chain else " / fallback models: none (--no-model-fallback)"
        print(f"{C.BOLD}API key(s): {pool.size}{C.RESET} {C.DIM}"
              f"({pool.summary()} / model: {settings.model}{chain_label}){C.RESET}")
        if len(pool.models) > 1:
            parts = " / ".join(f"{name} {label}" for name, label in pool.model_summary())
            print(f"{C.DIM}  Available keys by model: {parts}{C.RESET}")
        for item in pool.status():
            mark = "*" if item["active"] and item["ready"] else " "
            if not item["ready"]:
                state = f"{item['status'] or 429} limited (retry in {item['wait']:.0f}s)"
                color = C.YELLOW
            elif item["active"]:
                state, color = "Active", C.GREEN
            else:
                state, color = "Ready", C.DIM
            extra = ""
            if item["limited_models"]:
                extra = f" {C.DIM}(limited for {'/'.join(item['limited_models'])}){C.RESET}"
            print(f"  {mark}[{item['index']}] {item['masked']:<24} {color}{state}{C.RESET} "
                  f"{C.DIM}{item['label']}{' ' + item['path'] if item['path'] else ''}{C.RESET}{extra}")
        print(f"{C.DIM}After a 429, skip that key for {settings.key_cooldown:.0f}s and try the next one"
              f"(configure ACTOR_KEY_COOLDOWN; set ACTOR_KEY_ROTATE=false to disable).{C.RESET}")
        if pool.totals.get("limits"):
            print(f"{C.DIM}Total rate limits: {pool.totals['limits']:,}"
                  f"(details: actor key stats){C.RESET}")
        print(f"{C.DIM}Add: actor key add   /   Remove: actor key rm <number>   /   "
              f"Check: actor key check   /   Stats: actor key stats   /   Sources: actor key sources{C.RESET}")
        return 0

    # ---- Status ----------------------------------------------------------
    if action == "show":
        if not pool.size:
            _print_no_key()
            return 0
        entry = pool.current_entry
        print(f"{C.BOLD}{entry.masked}{C.RESET} {C.DIM}({entry.source_label}){C.RESET}")
        extra = f" / {pool.size} keys ({pool.summary()})" if pool.size > 1 else ""
        print(f"{C.DIM}Provider: {settings.effective_provider} / model: {settings.model}{extra}{C.RESET}")
        if settings.model_fallbacks:
            print(f"{C.DIM}Fallback models for 429s: {', '.join(settings.model_fallbacks)}"
                  f"(disable with --no-model-fallback){C.RESET}")
        if pool.size > 1:
            print(f"{C.DIM}Use `actor key list` to check all key statuses.{C.RESET}")
        return 0

    # ---- Add (keys directory or .env) ------------------------------------
    if action == "add":
        api_key = _read_key_value(args, "Gemini API key to add (input hidden): ")
        if api_key is None:
            return 1
        code = _validate_key(api_key)
        if code:
            return code
        if any(entry.value == api_key for entry in pool.entries):
            print(f"{C.YELLOW}This key is already registered:{C.RESET} {mask_key(api_key)}")
            return 0
        if args.env:
            path = append_key_to_env_file(settings, api_key)
            where = f"{MULTI_KEY_ENV} in {path}"
        else:
            [path] = write_keys_dir(settings, [api_key])
            where = str(path)
        refresh_keys(settings)
        print(f"{C.GREEN}Added:{C.RESET} {where} {C.DIM}(chmod 600){C.RESET}")
        print(f"{C.DIM}{settings.api_key_count} key(s) registered ({settings.key_pool().summary()})"
              f" / use `actor key list` to see all keys.{C.RESET}")
        _print_priority_hint(settings, api_key)
        if args.check:
            return _print_check_result(settings, api_key, index=settings.key_pool().current_index)
        return 0

    # ---- Save (legacy set action; lowest precedence, but usable as the first key) ----
    if action == "set":
        api_key = _read_key_value(args, "Google AI Studio API key (input hidden): ")
        if api_key is None:
            return 1
        code = _validate_key(api_key)
        if code:
            return code
        saved = write_api_key(settings, api_key)
        refresh_keys(settings)
        print(f"{C.GREEN}Saved:{C.RESET} {saved} {C.DIM}(chmod 600){C.RESET}")
        print(f"{C.DIM}This location is gitignored and will not be committed.{C.RESET}")
        _print_priority_hint(settings, api_key)
        if args.check:
            return _print_check_result(settings, api_key, index=0, saved=True)
        print(f"{C.DIM}Use `actor key show` to inspect it and `actor key set --check` to verify connectivity.{C.RESET}")
        return 0

    # ---- Remove ----------------------------------------------------------
    if action in {"clear"} or (action in {"rm", "remove"} and (rest[:1] == ["all"] or not rest)):
        if action in {"rm", "remove"} and not args.yes and sys.stdin.isatty():
            answer = input(f"Remove all {pool.size} registered keys? [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                print(f"{C.DIM}Cancelled.{C.RESET}")
                return 0
        removed = []
        if clear_api_key(settings):
            removed.append(str(settings.api_key_file or (settings.data_dir / "api_key")))
        for path in list_generated_key_files(settings):
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:  # pragma: no cover - permissions, etc.
                pass
        refresh_keys(settings)
        if removed:
            print(f"{C.GREEN}Removed{C.RESET}{C.DIM}: {', '.join(removed)}{C.RESET}")
            print(f"{C.DIM}.env and environment variables were not changed and remain active if configured.{C.RESET}")
        else:
            print(f"{C.DIM}No saved keys.{C.RESET}")
        return 0

    if action in {"rm", "remove"}:
        target = rest[0]
        try:
            index = int(target)
        except ValueError:
            print(f"{C.RED}Specify a key number from `actor key list` ([n]).{C.RESET}")
            return 2
        if not pool.size:
            _print_no_key()
            return 0
        if not 0 <= index < pool.size:
            print(f"{C.RED}Key number out of range (0–{pool.size - 1}).{C.RESET}")
            return 2
        entry = pool.entries[index]
        if entry.source == "keys" and entry.path:
            try:
                Path(entry.path).unlink()
            except OSError as exc:
                print(f"{C.RED}Could not remove key: {exc}{C.RESET}")
                return 1
            refresh_keys(settings)
            print(f"{C.GREEN}Deleted:{C.RESET} {entry.masked} {C.DIM}({entry.path}){C.RESET}")
            print(f"{C.DIM}{settings.api_key_count} key(s) remain ({settings.key_pool().summary()}){C.RESET}")
            return 0
        if entry.source == "file":
            clear_api_key(settings)
            refresh_keys(settings)
            print(f"{C.GREEN}Deleted:{C.RESET} {entry.masked} "
                  f"{C.DIM}({entry.path or entry.source_label}){C.RESET}")
            return 0
        if entry.source == ".env":
            env_path = settings.env_file or (PROJECT_ROOT / ".env")
            if remove_key_from_env_file(settings, entry.value, var=entry.label or MULTI_KEY_ENV):
                refresh_keys(settings)
                print(f"{C.GREEN}Deleted:{C.RESET} {entry.masked} "
                      f"{C.DIM}({entry.label or MULTI_KEY_ENV} in {env_path}){C.RESET}")
                print(f"{C.DIM}{settings.api_key_count} key(s) remain ({settings.key_pool().summary()}){C.RESET}")
                return 0
            print(f"{C.RED}Could not update {env_path} (check permissions).{C.RESET}")
            return 1
        where = {
            "env": f"environment variable {entry.label}",
            "cli": "--api-key (passed at startup)",
        }.get(entry.source, entry.source_label)
        print(f"{C.YELLOW}This key came from {where} and cannot be removed here.{C.RESET}")
        print(f"{C.DIM}Edit the source configuration file or shell environment (key: {entry.masked}).{C.RESET}")
        return 1

    # ---- Connectivity checks (one or all keys × models) ------------------
    if action == "check":
        if not pool.size:
            _print_no_key()
            return 1
        candidates = pool.status()
        if rest:
            try:
                index = int(rest[0])
            except ValueError:
                print(f"{C.RED}Specify a key number from `actor key list` ([n]).{C.RESET}")
                return 2
            if not 0 <= index < pool.size:
                print(f"{C.RED}Key number out of range (0–{pool.size - 1}).{C.RESET}")
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
            print(f"{C.BOLD}API key connectivity check{C.RESET} {C.DIM}"
                  f"({len(candidates)} key(s) × {len(models)} models / fetching model catalog){C.RESET}")
            for item in results:
                mark = f"{C.GREEN}✓{C.RESET}" if item["ok"] else f"{C.RED}✗{C.RESET}"
                print(f"  {mark} [{item['index']}] {item['masked']:<24} "
                      f"{item['checked_model']:<18} {item['detail']}")
            ok_count = sum(1 for item in results if item["ok"])
            if len(models) == 1:
                print(f"{C.DIM}{ok_count}/{len(results)} key(s) responded."
                      f"(use --all to check fallback models too){C.RESET}")
            else:
                print(f"{C.DIM}{ok_count}/{len(results)} key/model combinations responded.{C.RESET}")
            if ok_count and any(not item["ok"] for item in results):
                print(f"{C.DIM}Keys/models marked ✗ may be rate-limited, invalid, or unavailable"
                      f"(use `actor key stats` to inspect rate limits by model).{C.RESET}")
        return 0 if any(item["ok"] for item in results) else 1

    # ---- Statistics (identify frequently rate-limited keys) ---------------
    if action == "stats":
        if args.reset:
            pool.reset_stats()
            if args.as_json:
                print(json.dumps({"reset": True, **pool.stats()}, ensure_ascii=False, indent=2))
            else:
                print(f"{C.GREEN}Reset statistics{C.RESET}"
                      f"{C.DIM}(cleared counters and rate-limit status){C.RESET}")
            return 0
        stats = pool.stats()
        if args.as_json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
            return 0
        if not pool.size:
            _print_no_key()
            return 0
        totals = stats["totals"]
        print(f"{C.BOLD}API key usage statistics{C.RESET} {C.DIM}"
              f"({stats['state_path'] or 'no records yet'}){C.RESET}")
        print(f"  Totals: {totals['requests']:,} requests / "
              f"{totals['limits']:,} rate limits / {totals['rotations']:,} rotations")
        by_model = stats["by_model"]
        if by_model:
            print(f"{C.BOLD}  By model{C.RESET}")
            for name, values in sorted(by_model.items(),
                                       key=lambda pair: (-pair[1].get("limits", 0), pair[0])):
                ready = stats["ready_by_model"].get(name)
                ready_label = f" / {ready}/{stats['count']} keys ready" if ready is not None else ""
                limits = values.get("limits", 0)
                color = C.YELLOW if limits else C.DIM
                print(f"    {name:<20} {limits} rate limits / {values.get('requests', 0)} requests"
                      f"{color}{ready_label}{C.RESET}")
        used = [item for item in stats["keys"] if item["attempts"]]
        if not used:
            print(f"{C.DIM}No statistics yet (make a request to start collecting data).{C.RESET}")
            return 0
        print(f"{C.BOLD}  Most rate-limited keys{C.RESET}")
        for item in used:
            rate = f"{item['limit_rate'] * 100:.0f}%"
            if not item["ready"]:
                state = f"{item['status'] or 429} limited (retry in {item['wait']:.0f}s)"
                color = C.YELLOW
            elif item["active"]:
                state, color = "Active", C.GREEN
            else:
                state, color = "Ready", C.DIM
            detail = ""
            if item["limits_by_model"]:
                inner = " / ".join(f"{name} {count}" for name, count in item["limits_by_model"].items())
                detail = f" {C.DIM}[{inner}]{C.RESET}"
            print(f"  [{item['index']}] {item['masked']:<24} 429: {item['limits']} time(s) / "
                  f"{item['attempts']} attempt(s) ({rate})"
                  f" {C.DIM}successes {item['uses']} / errors {item['errors']} / "
                  f"last used {_ago(item['last_used'])}{C.RESET} {color}{state}{C.RESET}{detail}")
            print(f"          {C.DIM}{item['label']}{' ' + item['path'] if item['path'] else ''}{C.RESET}")
        worst = stats["worst"]
        if worst and worst["limits"]:
            print(f"{C.YELLOW}Most rate-limited key: [{worst['index']}] {worst['masked']}"
                  f" ({worst['limits']} rate limits / {worst['attempts']} attempts){C.RESET}")
            if stats["count"] > 1:
                print(f"{C.DIM}Check whether this key is shared with other tools (Gemini app or other projects)."
                      f"{C.RESET}")
            else:
                print(f"{C.DIM}Only one key is configured. Add another with `actor key add` to continue with a backup after a 429."
                      f"{C.RESET}")
        hot_model = max(stats["by_model"].items(), key=lambda pair: pair[1].get("limits", 0),
                        default=None)
        if hot_model and hot_model[1].get("limits"):
            print(f"{C.YELLOW}Most rate-limited model: {hot_model[0]}"
                  f" ({hot_model[1]['limits']} rate limits){C.RESET}")
            chain = [name for name in settings.model_fallbacks if name != hot_model[0]]
            if chain:
                print(f"{C.DIM}Fallbacks for this model: {', '.join(chain)}"
                      f" (change with --fallback-model / disable with --no-model-fallback){C.RESET}")
            else:
                print(f"{C.DIM}Set a fallback model with `--fallback-model gemini-3.5-flash`."
                      f"{C.RESET}")
        if stats["stale_records"]:
            print(f"{C.DIM}{stats['stale_records']} stale key record(s) remain"
                  f"(remove them with `actor key stats --reset`).{C.RESET}")
        return 0

    # ---- Key sources ------------------------------------------------------
    if action == "sources":
        from .keys import KEY_SOURCES_CAUTION, key_sources

        items = key_sources()
        if args.as_json:
            print(json.dumps({"sources": items, "caution": KEY_SOURCES_CAUTION},
                             ensure_ascii=False, indent=2))
            return 0
        print(f"{C.BOLD}Where to get API keys{C.RESET}{C.DIM} (quotas are per project and model){C.RESET}")
        for item in items:
            print(f"  {C.CYAN}{item['name']}{C.RESET} {C.DIM}[{item['kind']}]{C.RESET}")
            if item["url"]:
                print(f"    {C.BOLD}{item['url']}{C.RESET}")
            print(f"    {item['note']}")
        print(f"{C.YELLOW}{KEY_SOURCES_CAUTION}{C.RESET}")
        print(f"{C.DIM}Current key status: actor key list / rate-limit statistics: actor key stats / "
              f"Add a key: actor key add{C.RESET}")
        return 0

    return 2


def _ago(timestamp: float) -> str:
    """Format a relative timestamp such as "3m ago" for key statistics."""
    if not timestamp:
        return "—"
    delta = max(0.0, time.time() - float(timestamp))
    if delta < 60:
        return f"{delta:.0f}s ago"
    if delta < 3600:
        return f"{delta / 60:.0f}m ago"
    if delta < 86400:
        return f"{delta / 3600:.1f}h ago"
    return f"{delta / 86400:.1f}d ago"


def _print_priority_hint(settings: Settings, api_key: str) -> None:
    """Notify the user when a higher-precedence key (such as an environment variable) overrides the supplied key."""
    pool = settings.key_pool()
    active = pool.current_entry
    if not active or active.value == api_key:
        return
    print(f"{C.DIM}Currently, {active.masked} ({active.source_label}) takes precedence"
          f"(--api-key > environment > .env > saved file > keys directory).{C.RESET}")


def _print_no_key() -> None:
    print(f"{C.YELLOW}API key not configured{C.RESET}{C.DIM} (running in simulation mode){C.RESET}")
    print(f"{C.DIM}Configure with actor key set / actor key add / "
          f"GEMINI_API_KEY=... (multiple keys: GEMINI_API_KEYS=a,b,c){C.RESET}")
    print(f"{C.DIM}Free key options: actor key sources (Google AI Studio, etc.){C.RESET}")


def _print_check_result(settings: Settings, api_key: str, *, index: int, saved: bool = False) -> int:
    """Shared logic for ``key set --check`` and ``key add --check``."""
    label = "saved" if saved else "added"
    if settings.is_demo:
        print(f"{C.YELLOW}Key {label}, but still in simulation mode (check --provider).{C.RESET}")
        return 0
    print(f"{C.DIM}Checking connectivity…{C.RESET}")
    ok, detail = _probe_key(settings, api_key)
    if ok:
        print(f"{C.GREEN}API connection OK{C.RESET} [{index}] {mask_key(api_key)} — {detail}")
        return 0
    print(f"{C.RED}Could not connect to the API{C.RESET} [{index}] {mask_key(api_key)} — {detail}")
    return 1


def cmd_models(args: argparse.Namespace) -> int:
    settings = load_settings(model=getattr(args, "model", None) or None)
    provider = get_provider(settings)
    if provider.is_mock:
        print(f"{C.DIM}No API key configured; showing built-in model options.{C.RESET}")
        for name in KNOWN_MODELS:
            print(f"  {name}")
        return 0
    models = provider.model_catalog()
    if not models:
        print(f"{C.YELLOW}Could not fetch the model list.{C.RESET}")
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
    print(f"  Dependencies      : None (Python standard library only)")
    print(f"  Provider          : {settings.effective_provider}" + (f" {C.YELLOW}(simulator){C.RESET}" if settings.is_demo else ""))
    print(f"  Model             : {settings.model}")
    pool = settings.key_pool()
    if settings.api_key:
        where = {"env": "environment variable", ".env": f"{PROJECT_ROOT}/.env", "file": str(settings.api_key_file),
                 "keys": str(settings.keys_path), "cli": "--api-key"}.get(settings.api_key_source,
                                                                          settings.api_key_source)
        print(f"  API key           : configured {C.DIM}({where}){C.RESET}")
        if pool.size > 1:
            print(f"  API key pool      : {pool.summary()}")
            for item in pool.status():
                mark = "*" if item["active"] and item["ready"] else " "
                state = "Active" if item["active"] and item["ready"] else (
                    f"{item['status'] or 429} limited (retry in {item['wait']:.0f}s)" if not item["ready"] else "Ready")
                print(f"    {mark}[{item['index']}] {item['masked']} "
                      f"{C.DIM}{state} / {item['label']}{C.RESET}")
            print("  Automatic rotation: " + ("enabled" if settings.key_rotate else "disabled")
                  + f"{C.DIM}(skip that key for {settings.key_cooldown:.0f}s after a 429){C.RESET}")
        chain = settings.model_fallbacks or []
        print("  Model fallbacks   : " + (", ".join(chain) if chain else "none (--no-model-fallback)")
              + f"{C.DIM}(used only after a 429){C.RESET}" if chain else
              "  Model fallbacks   : none (ACTOR_MODEL_FALLBACKS= / --no-model-fallback)")
        if settings.api_version != "v1beta" or "aiplatform" in settings.base_url:
            print(f"  API shape         : {settings.api_version} / {settings.model_path}"
                  f"{C.DIM}(for Vertex Express Mode){C.RESET}")
        totals = pool.totals
        if totals.get("limits") or totals.get("requests"):
            worst = pool.stats()["worst"]
            hint = ""
            if worst and worst["limits"]:
                hint = f" / most limited: [{worst['index']}] {worst['masked']} ({worst['limits']} rate limits)"
            print(f"  Key statistics   : {totals.get('requests', 0):,} requests / "
                  f"{totals.get('limits', 0):,} rate limits / {totals.get('rotations', 0):,} rotations"
                  f"{C.DIM}{hint} (actor key stats){C.RESET}")
            by_model = pool.stats()["by_model"]
            hot = [f"{name}: {values.get('limits', 0)} rate limits" for name, values in by_model.items()
                   if values.get("limits")]
            if hot:
                print(f"  Most rate-limited models: {', '.join(hot)}")
    else:
        print(f"  API key           : {C.YELLOW}not configured{C.RESET} {C.DIM}"
              f"(use actor key set or set GEMINI_API_KEY in {PROJECT_ROOT}/.env){C.RESET}")
    for directory in settings.skills_dirs:
        count = sum(1 for s in library if s.source == str(directory))
        state = "✓" if directory.is_dir() else f"{C.YELLOW}not created{C.RESET}"
        print(f"  Skill directories: {directory} {state} ({count} skill(s))")
    print(f"  Data directory    : {settings.data_dir}")
    print(f"  Sessions          : {len(engine.store.list(limit=10000))}")
    rooms = RoomStore(settings.data_dir / "rooms").list()
    print(f"  Rooms             : {len(rooms)} item(s)")
    try:
        from .genai_provider import GenAiProvider, available, sdk_version

        state = f"available (v{sdk_version()})" if available() else "not installed (use --provider gemini)"
    except Exception:  # pragma: no cover
        state = "available" if False else "not installed"
    print(f"  google-genai SDK : {state}")
    tools = engine.tools
    print(f"  Tools             : {len(tools)} tool(s) ({', '.join(tools.names()) or 'none'})"
          f" write={'on' if settings.tools_allow_write else 'off'}"
          f" network={'on' if settings.tools_allow_network else 'off'}")
    print(f"  Attachment limit  : {settings.max_attachments} file(s) / "
          f"{settings.max_attachment_bytes / 1048576:.0f}MB"
          f" / saved={'yes' if settings.store_attachments else 'no'}")
    print(f"  Active provider   : {settings.effective_provider}")
    if library.errors:
        ok = False
        for error in library.errors:
            print(f"  {C.RED}! {error['path']}: {error['error']}{C.RESET}")
    if args.check_api:
        if settings.is_demo:
            print(f"  {C.YELLOW}--check-api requires an API key.{C.RESET}")
        else:
            models = engine.provider.model_catalog()
            if models:
                print(f"  {C.GREEN}API connection OK{C.RESET} ({len(models)} models)")
                if settings.model not in models:
                    print(f"  {C.YELLOW}! '{settings.model}' is not in the catalog. Suggestions: {', '.join(models[:5])}{C.RESET}")
            else:
                ok = False
                print(f"  {C.RED}Could not connect to the API. Check the API key and network.{C.RESET}")
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
          f" / max rounds {settings.max_tool_rounds}{C.RESET}")
    print(f"{C.DIM}If skills declare allowed-tools, only the union of those tools is available.{C.RESET}")
    declared = {s.name: list(s.allowed_tools) for s in library.all() if s.allowed_tools}
    if declared:
        print(f"{C.BOLD}Per-skill settings{C.RESET}")
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
        print(f"{C.RED}API Error: {exc}{C.RESET}")
        return 1
    except Exception as exc:
        if getattr(args, "verbose", False):
            raise
        print(f"{C.RED}{type(exc).__name__}: {exc}{C.RESET}")
        print(f"{C.DIM}Rerun with -v for details.{C.RESET}")
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
