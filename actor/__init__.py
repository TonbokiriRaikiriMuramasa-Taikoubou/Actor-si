"""Actor — SKILL.md 駆動の Gemini 対話クライアント。

Google AI Studio の API キーで Gemini API を叩き、フォルダ内の SKILL.md を
system_instruction として読み込んで、CLI / Web API / Web UI から対話する。

依存パッケージはゼロ（Python 3.9+ の標準ライブラリのみ）。
"""

from __future__ import annotations

__all__ = [
    "__version__",
    "Skill",
    "SkillLibrary",
    "Settings",
    "Session",
    "SessionStore",
    "build_system_instruction",
    "get_provider",
]

__version__ = "0.2.0"
__app_name__ = "Actor"

from .config import Settings, load_settings  # noqa: E402
from .prompt import build_system_instruction  # noqa: E402
from .providers import get_provider  # noqa: E402
from .sessions import Session, SessionStore  # noqa: E402
from .skills import Skill, SkillLibrary  # noqa: E402


def load_skill_library(settings: Settings | None = None) -> SkillLibrary:
    """設定に基づいてスキルライブラリを読み込むショートハンド。"""
    settings = settings or load_settings()
    return SkillLibrary(settings.skills_dirs)
