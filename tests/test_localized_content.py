import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def markdown_headings(path: Path) -> list[tuple[int, str]]:
    """Read Markdown headings outside fenced code blocks (no Markdown dependency)."""
    headings = []
    fence_char = ""
    fence_size = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        fence = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size and not stripped[len(marker):].strip():
                fence_char, fence_size = "", 0
            continue
        if fence_char:
            continue
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append((len(match.group(1)), match.group(2)))
    return headings


def markdown_links(path: Path) -> list[str]:
    """Return inline Markdown links outside fenced code blocks."""
    lines = []
    fence_char = ""
    fence_size = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        fence = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size and not stripped[len(marker):].strip():
                fence_char, fence_size = "", 0
            continue
        if not fence_char:
            lines.append(line)
    text = "\n".join(lines)
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)


class LocalizedContentTest(unittest.TestCase):
    def test_readme_chapter_structure_matches_japanese_edition(self):
        english = markdown_headings(ROOT / "README.md")
        japanese = markdown_headings(ROOT / "README.ja.md")
        self.assertEqual([level for level, _ in english], [level for level, _ in japanese])

    def test_readme_relative_links_resolve(self):
        for readme in (ROOT / "README.md", ROOT / "README.ja.md"):
            for link in markdown_links(readme):
                if link.startswith(("https://", "http://", "mailto:", "#")):
                    continue
                target = link.split("#", 1)[0].split("?", 1)[0]
                if not target:
                    continue
                with self.subTest(readme=readme.name, link=link):
                    self.assertTrue((readme.parent / target).exists(), f"Broken relative link: {link}")

    def test_default_documentation_is_english(self):
        files = [
            ROOT / ".env.example", ROOT / "README.md", ROOT / "HANDOFF.md", ROOT / "LISTING.md",
            ROOT / "CHANGELOG.md", ROOT / "FUNDING.md", ROOT / "SECURITY.md",
            *sorted((ROOT / "docs").glob("*.md")),
        ]
        for path in files:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIsNone(CJK_RE.search(path.read_text(encoding="utf-8")))

    def test_default_skills_and_references_are_english(self):
        skills_dir = ROOT / "skills"
        skill_dirs = sorted(path for path in skills_dir.iterdir() if path.is_dir() and path.name != "_house")
        self.assertEqual(len(skill_dirs), 5)
        for skill_dir in skill_dirs:
            files = [skill_dir / "SKILL.md"] + sorted((skill_dir / "references").glob("*.md"))
            for path in files:
                text = path.read_text(encoding="utf-8")
                with self.subTest(path=path.relative_to(ROOT)):
                    self.assertIsNone(CJK_RE.search(text), "English skill content contains Japanese text")
            frontmatter = (skill_dir / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
            description = re.search(r"^description:\s*(.+)$", frontmatter, re.M)
            self.assertIsNotNone(description)
            value = description.group(1)
            self.assertIn("Use when", value)
            self.assertIn("Do not use when", value)
            self.assertGreaterEqual(len(re.findall(r"[.!?](?:\s|$)", value)), 1)
            self.assertLessEqual(len(re.findall(r"[.!?](?:\s|$)", value)), 3)

    def test_japanese_skill_directory_is_separate_from_default(self):
        default_names = {path.name for path in (ROOT / "skills").iterdir() if path.is_dir() and path.name != "_house"}
        japanese_root = ROOT / "i18n" / "ja" / "skills"
        japanese_names = {path.name for path in japanese_root.iterdir() if path.is_dir() and path.name != "_house"}
        self.assertEqual(default_names, japanese_names)
        self.assertTrue((japanese_root / "_house" / "principles.md").is_file())


if __name__ == "__main__":
    unittest.main()
