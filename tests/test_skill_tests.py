import unittest
from pathlib import Path
from unittest.mock import patch

from actor.config import load_settings
from actor.skill_tests import _assertion_matches, run_cases
from actor.skills import SkillLibrary


class SkillOutputContractTest(unittest.TestCase):
    def setUp(self):
        self.library = SkillLibrary(load_settings({}).skills_dirs)

    def test_default_mode_is_explicitly_format_only(self):
        result = run_cases(self.library)
        self.assertTrue(result["passed"])
        self.assertEqual(result["mode"], "format-only")
        self.assertTrue(result["cases"])
        for case in result["cases"]:
            self.assertEqual(case["check_type"], "format-only")
            self.assertIsNone(case["output"])
            self.assertIn("形式の検査のみ", case["message"])

    def test_default_english_skill_directory_passes_format_checks(self):
        result = run_cases(self.library)
        self.assertTrue(result["passed"])
        minutes = next(case for case in result["cases"] if case["skill"] == "minutes-writer")
        self.assertIn("# <Meeting name> Meeting Minutes", minutes["spec_headings"])

    def test_japanese_skill_directory_passes_when_explicitly_selected(self):
        ja_dir = Path(__file__).resolve().parents[1] / "i18n" / "ja" / "skills"
        library = SkillLibrary([ja_dir])
        result = run_cases(library)
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["cases"]), 5)
        minutes = next(case for case in result["cases"] if case["skill"] == "minutes-writer")
        self.assertIn("# <会議名> 議事録", minutes["spec_headings"])

    def test_angle_bracket_heading_placeholders_match_concrete_output(self):
        self.assertTrue(_assertion_matches("# 週次会議 議事録", "# <会議名> 議事録"))
        self.assertTrue(_assertion_matches("# Sprint Review 議事録", "# <会議名> 議事録"))
        self.assertFalse(_assertion_matches("# 議事録", "# <会議名> 議事録"))
        self.assertFalse(_assertion_matches("# Sprint Review\\n議事録", "# <会議名> 議事録"))
        self.assertTrue(_assertion_matches("### [High] Null guard missing", "### [High] <1行で内容>"))

    def test_assertions_must_match_format_spec(self):
        with patch("actor.skill_tests._load_cases", return_value=[{
            "name": "intentionally-stale",
            "input": "example",
            "assert_contains": ["## stale heading"],
        }]):
            result = run_cases(self.library, names=["brand-voice"])
        case = result["cases"][0]
        self.assertFalse(case["passed"])
        self.assertTrue(case["missing_from_assertions"])
        self.assertEqual(case["stale_assertions"], ["## stale heading"])


if __name__ == "__main__":
    unittest.main()
