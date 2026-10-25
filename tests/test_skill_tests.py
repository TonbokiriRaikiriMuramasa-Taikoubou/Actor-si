import unittest
from unittest.mock import patch

from actor.config import load_settings
from actor.skill_tests import run_cases
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
