"""Layout checks for installing the bundled skills with `npx skills add`.

The skills CLI installs each `skills/<name>/` folder as-is, so every file a
skill references must live inside its own folder.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _skill_dirs():
    return sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())


def _frontmatter(text):
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return match.group(1) if match else ""


class SkillsShLayoutTest(unittest.TestCase):
    def test_exactly_the_five_public_skills_are_discoverable(self):
        self.assertEqual([p.name for p in _skill_dirs()],
                         ["brand-voice", "code-review", "minutes-writer", "release-notes", "skill-author"])
        self.assertFalse((SKILLS / "_house" / "SKILL.md").exists(), "_house must not become a skill")

    def test_frontmatter_meets_agent_skills_spec(self):
        for root in _skill_dirs():
            with self.subTest(skill=root.name):
                fm = _frontmatter((root / "SKILL.md").read_text(encoding="utf-8"))
                name = re.search(r"^name:\s*(.+)$", fm, re.M).group(1).strip()
                description = re.search(r"^description:\s*(.+)$", fm, re.M).group(1).strip()
                self.assertEqual(name, root.name)
                self.assertRegex(name, NAME_RE)
                self.assertLessEqual(len(name), 64)
                self.assertTrue(0 < len(description) <= 1024)

    def test_shared_principles_and_license_are_vendored_in_each_skill(self):
        principles = (SKILLS / "_house" / "principles.md").read_bytes()
        license_text = (REPO / "LICENSE").read_bytes()
        for root in _skill_dirs():
            with self.subTest(skill=root.name):
                self.assertEqual((root / "references" / "principles.md").read_bytes(), principles,
                                 "run: cp skills/_house/principles.md skills/<name>/references/")
                self.assertEqual((root / "LICENSE").read_bytes(), license_text)

    def test_example_persona_is_hidden_from_skills_cli(self):
        fm = _frontmatter((REPO / "examples" / "mika" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertRegex(fm, r"(?m)^metadata:\n\s+internal:\s*true\s*$")


if __name__ == "__main__":
    unittest.main()
