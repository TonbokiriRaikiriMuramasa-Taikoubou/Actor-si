"""Actor のテスト（unittest のみ / 追加依存なし）。

実行::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HTTPServerProc = ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actor.chat import ChatEngine, parse_draft  # noqa: E402
from actor.config import Settings, load_settings, parse_env_file  # noqa: E402
from actor.prompt import build_system_instruction, compose_system_text, estimate_tokens, system_text_of  # noqa: E402
from actor.providers import MockProvider, ProviderError, get_provider, to_gemini_contents  # noqa: E402
from actor.bot import RoomBot, RoomClient  # noqa: E402
from actor.rooms import Room, RoomStore  # noqa: E402
from actor.sessions import Session, SessionStore  # noqa: E402
from actor.skills import (  # noqa: E402
    SkillLibrary,
    code_fence,
    dump_frontmatter,
    fenced_blocks,
    parse_frontmatter,
    slugify,
)

SAMPLE_SKILL = """---
name: {name}
description: {description}
version: 1.0.0
tags: [テスト, {tag}]
---

# 目的
{description}

# 手順
1. 入力を受け取る
2. 処理する
3. 出力する

# 出力フォーマット
```markdown
## 結果
```

# 確認事項
- 捏造しない

# 仕様メモ（ネストしたフェンスのテスト）
```markdown
```python
print("nested")
```
```
"""


class FrontmatterTest(unittest.TestCase):
    def test_parse_basic(self):
        meta, body = parse_frontmatter("---\nname: a-b\ndescription: 説明\nversion: 2.1.0\n---\n\n# 本文\n")
        self.assertEqual(meta["name"], "a-b")
        self.assertEqual(meta["description"], "説明")
        self.assertEqual(meta["version"], "2.1.0")
        self.assertTrue(body.startswith("# 本文"))

    def test_parse_inline_list(self):
        meta, _ = parse_frontmatter("---\ntags: [議事録, 会議]\n---\nx")
        self.assertEqual(meta["tags"], ["議事録", "会議"])

    def test_parse_block_list(self):
        meta, _ = parse_frontmatter("---\ntags:\n  - alpha\n  - beta\n---\nx")
        self.assertEqual(meta["tags"], ["alpha", "beta"])

    def test_no_frontmatter(self):
        meta, body = parse_frontmatter("# 見出しだけ\n本文")
        self.assertEqual(meta, {})
        self.assertIn("見出し", body)

    def test_dump_roundtrip(self):
        text = dump_frontmatter({"name": "x-y", "description": "説明", "tags": ["a", "b"], "flag": True})
        meta, _ = parse_frontmatter(text + "\nbody")
        self.assertEqual(meta["name"], "x-y")
        self.assertEqual(meta["tags"], ["a", "b"])
        self.assertIs(meta["flag"], True)

    def test_slugify(self):
        self.assertEqual(slugify("Weekly Report!"), "weekly-report")
        self.assertEqual(slugify("My___Skill"), "my-skill")
        self.assertEqual(slugify("日本語"), "skill")


class FenceTest(unittest.TestCase):
    def test_code_fence_grows(self):
        self.assertEqual(code_fence("no fences"), "```")
        self.assertEqual(code_fence("```inner```"), "````")

    def test_fenced_blocks(self):
        blocks = fenced_blocks("text\n```python\nx = 1\n```\nmore\n````\nnested\n````\n")
        self.assertEqual(blocks[0], ("python", "x = 1"))
        self.assertEqual(blocks[1][1], "nested")


class SkillLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "skills"
        (self.dir / "minutes").mkdir(parents=True)
        (self.dir / "minutes" / "SKILL.md").write_text(
            SAMPLE_SKILL.format(name="minutes", description="会議の議事録を作る", tag="会議"), encoding="utf-8"
        )
        (self.dir / "minutes" / "references").mkdir()
        (self.dir / "minutes" / "references" / "format.md").write_text(
            "# テンプレート\n\n```markdown\n# <会議名> 議事録\n```\n", encoding="utf-8"
        )
        (self.dir / "review.md").write_text(
            "---\nname: code-review\ndescription: 差分をレビューする\n---\n# 手順\n1. 見る\n", encoding="utf-8"
        )
        self.lib = SkillLibrary([self.dir])

    def tearDown(self):
        self.tmp.cleanup()

    def test_loads_folder_and_single_file(self):
        self.assertEqual(sorted(self.lib.names()), ["code-review", "minutes"])
        self.assertEqual(self.lib.get("minutes").description, "会議の議事録を作る")

    def test_references_collected(self):
        skill = self.lib.get("minutes")
        self.assertEqual([r.relpath for r in skill.references], ["references/format.md"])
        self.assertEqual(skill.references[0].kind, "text")

    def test_instruction_block_includes_reference_with_nested_fence(self):
        block = self.lib.get("minutes").instruction_block()
        self.assertIn("## 参照資料（スキルに同梱）", block)
        self.assertIn("references/format.md", block)
        # ネストしたフェンスより長いフェンスで囲まれている
        self.assertIn("````", block)

    def test_search_and_select(self):
        hits = self.lib.search("会議の議事録を作って", limit=3, threshold=2.0)
        self.assertEqual(hits[0][0].name, "minutes")
        self.assertEqual(self.lib.search("今日の天気", limit=3, threshold=2.0), [])
        skills, missing, _ = self.lib.select("レビューして", explicit=["nope"], auto=True)
        self.assertIn("code-review", [s.name for s in skills])
        self.assertEqual(missing, ["nope"])

    def test_explicit_slash_reference_wins(self):
        hits = self.lib.search("/minutes を作って", limit=3, threshold=2.0)
        self.assertGreater(hits[0][1], 50)

    def test_create_update_delete(self):
        created = self.lib.create("new-skill", "説明", "# 手順\n1. する")
        self.assertTrue(created.path.is_file())
        self.assertIn("new-skill", self.lib.names())
        with self.assertRaises(FileExistsError):
            self.lib.create("new-skill", "説明", "body")
        updated = self.lib.update("new-skill", description="更新後", instructions="# 手順\n1. やる")
        self.assertEqual(updated.description, "更新後")
        self.assertIn("1. やる", updated.instructions)
        self.assertTrue(self.lib.delete("new-skill"))
        self.assertNotIn("new-skill", self.lib.names())

    def test_export_zip_has_skill_md_at_root(self):
        import io
        import zipfile

        data = self.lib.export_zip(["minutes"])
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        self.assertIn("SKILL.md", names)
        self.assertIn("references/format.md", names)

    def test_broken_skill_is_reported_not_fatal(self):
        (self.dir / "broken").mkdir()
        (self.dir / "broken" / "SKILL.md").write_bytes(b"\xff\xfe\x00bad")
        lib = SkillLibrary([self.dir])
        self.assertIn("minutes", lib.names())
        self.assertTrue(all("error" in e for e in lib.errors) or lib.get("broken") is not None)

    def test_kebab_case_warning(self):
        (self.dir / "UPPER").mkdir()
        (self.dir / "UPPER" / "SKILL.md").write_text("---\nname: My Skill\n---\n# 手順\n1. x\n", encoding="utf-8")
        lib = SkillLibrary([self.dir])
        skill = lib.get("my-skill")
        self.assertIsNotNone(skill)
        self.assertTrue(any("kebab" in w for w in skill.warnings))


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "a").mkdir()
        (self.dir / "a" / "SKILL.md").write_text(
            "---\nname: a\ndescription: Aスキル\n---\n# 手順\n1. 一つ目\n", encoding="utf-8"
        )
        (self.dir / "b").mkdir()
        (self.dir / "b" / "SKILL.md").write_text(
            "---\nname: b\ndescription: Bスキル\n---\n# 手順\n1. 二つ目\n", encoding="utf-8"
        )
        self.lib = SkillLibrary([self.dir])

    def tearDown(self):
        self.tmp.cleanup()

    def test_compose_includes_all_skills_in_order(self):
        text = compose_system_text(self.lib.all(), preamble="前置き")
        self.assertIn("前置き", text)
        self.assertIn('適用中のスキル: a, b', text)
        self.assertLess(text.index("<skill name=\"a\">"), text.index("<skill name=\"b\">"))

    def test_catalog_lists_others(self):
        text = compose_system_text([self.lib.get("a")], preamble="p", all_skills=self.lib.all(), with_catalog=True)
        self.assertIn("## 利用できるスキル（カタログ）", text)
        self.assertIn("`b`", text)
        self.assertNotIn("- `a`:", text.split("カタログ")[1])

    def test_build_returns_parts(self):
        obj = build_system_instruction([self.lib.get("a")], preamble="p")
        self.assertIn("parts", obj)
        self.assertIn("<skill name=\"a\">", obj["parts"][0]["text"])
        self.assertEqual(system_text_of(obj), obj["parts"][0]["text"])

    def test_no_skills(self):
        text = compose_system_text([], preamble="p")
        self.assertIn("適用中のスキル: なし", text)

    def test_estimate_tokens_monotonic(self):
        self.assertLess(estimate_tokens("短い"), estimate_tokens("ずっと長いテキストです" * 10))


class ConfigTest(unittest.TestCase):
    def test_env_file_and_precedence(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / ".env"
        path.write_text('GEMINI_API_KEY=from_file\nACTOR_MODEL="gemini-3-pro"\n# comment\nexport ACTOR_AUTO_SELECT=0\n',
                        encoding="utf-8")
        self.assertEqual(parse_env_file(path)["GEMINI_API_KEY"], "from_file")
        settings = load_settings(env={"GEMINI_API_KEY": "from_env"}, env_file=path)
        self.assertEqual(settings.api_key, "from_env")  # 環境変数が .env より優先
        self.assertEqual(settings.model, "gemini-3-pro")
        self.assertFalse(settings.auto_select)
        self.assertEqual(settings.effective_provider, "gemini")
        tmp.cleanup()

    def test_auto_provider_without_key_is_mock(self):
        settings = load_settings(env={}, env_file=Path("/nonexistent"))
        self.assertEqual(settings.effective_provider, "mock")
        self.assertTrue(settings.is_demo)

    def test_api_key_never_leaks(self):
        settings = Settings(api_key="fixture-SyABCDEFGHIJKLMNOP")
        dump = json.dumps(settings.to_dict())
        self.assertNotIn("fixture-SyABCDEFGHIJKLMNOP", dump)

    def test_thinking_config(self):
        settings = Settings(thinking_budget=0)
        self.assertNotIn("thinkingConfig", settings.generation_config())
        settings = Settings(thinking_budget=2048)
        config = settings.generation_config()
        self.assertEqual(config["thinkingConfig"]["thinkingBudget"], 2048)


class ProviderTest(unittest.TestCase):
    def test_contents_mapping_and_merge(self):
        contents = to_gemini_contents([
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
            {"role": "user", "content": "  "},
        ])
        self.assertEqual([c["role"] for c in contents], ["user", "model"])
        self.assertEqual([p["text"] for p in contents[0]["parts"]], ["a", "b"])

    def test_get_provider(self):
        self.assertIsInstance(get_provider(Settings(api_key="")), MockProvider)
        self.assertEqual(get_provider(Settings(api_key="k")).name, "gemini")
        self.assertEqual(get_provider(Settings(), name="mock").name, "mock")
        with self.assertRaises(ValueError):
            get_provider(Settings(), name="nope")

    def test_mock_streams_thoughts_and_text(self):
        provider = MockProvider(Settings())
        provider.delay = 0
        lib = SkillLibrary([])
        system = build_system_instruction([], preamble="テスト")
        events = list(provider.stream([{"role": "user", "content": "こんにちは"}], system))
        kinds = [e["type"] for e in events]
        self.assertIn("thought", kinds)
        self.assertIn("text", kinds)
        self.assertEqual(kinds[-1], "done")
        self.assertTrue(events[-1]["usage"]["simulated"])

    def test_provider_error_retryable(self):
        self.assertTrue(ProviderError("x", status=429).retryable)
        self.assertFalse(ProviderError("x", status=400).retryable)


class SessionTest(unittest.TestCase):
    def test_store_roundtrip(self):
        tmp = tempfile.TemporaryDirectory()
        store = SessionStore(Path(tmp.name) / "sessions")
        session = store.create("タイトル", skills=["a"], model="m")
        session.add_user("こんにちは")
        session.add_assistant("はい", usage={"totalTokenCount": 5})
        store.save(session)

        loaded = SessionStore(Path(tmp.name) / "sessions").get(session.id)
        self.assertEqual(loaded.title, "タイトル")
        self.assertEqual(len(loaded.messages), 2)
        self.assertEqual(loaded.messages[1].usage["totalTokenCount"], 5)
        self.assertEqual(store.list()[0]["id"], session.id)
        self.assertTrue(store.delete(session.id))
        self.assertIsNone(store.get(session.id))
        tmp.cleanup()

    def test_invalid_session_id(self):
        tmp = tempfile.TemporaryDirectory()
        store = SessionStore(Path(tmp.name))
        with self.assertRaises(ValueError):
            store.path_of("../../etc/passwd")
        tmp.cleanup()

    def test_auto_title(self):
        session = Session(id="x")
        session.add_user("会議の文字起こしから議事録を作る件について相談したい")
        self.assertTrue(session.title != "" or session.auto_title().startswith("会議の"))


class ChatEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "skills" / "minutes").mkdir(parents=True)
        (root / "skills" / "minutes" / "SKILL.md").write_text(
            SAMPLE_SKILL.format(name="minutes", description="議事録を作る", tag="会議"), encoding="utf-8"
        )
        self.settings = Settings(
            api_key="", provider="mock", skills_dirs=[root / "skills"], data_dir=root / ".actor"
        )
        self.engine = ChatEngine(self.settings)

    def tearDown(self):
        self.tmp.cleanup()

    def test_turn_events_and_persistence(self):
        session = self.engine.store.create()
        events = list(self.engine.turn(session, "会議の議事録を作って"))
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[0], "selection")
        self.assertEqual(kinds[1], "prompt")
        self.assertEqual(kinds[-1], "done")
        selection = events[0]
        self.assertEqual(selection["names"], ["minutes"])
        self.assertIn("<skill name=\"minutes\">", events[1]["system_instruction"])
        done = events[-1]
        self.assertTrue(done["simulated"])
        self.assertEqual(done["session"]["turn_count"], 1)
        # 永続化されている
        self.assertIsNotNone(self.engine.store.get(session.id))
        self.assertEqual(self.engine.store.get(session.id).messages[0].role, "user")

    def test_pinned_skills_persist_across_turns(self):
        session = self.engine.store.create()
        list(self.engine.turn(session, "こんにちは", explicit_skills=["minutes"]))
        events = list(self.engine.turn(session, "続き"))
        self.assertEqual(events[0]["names"], ["minutes"])

    def test_unknown_skill_reports_missing(self):
        session = self.engine.store.create()
        events = list(self.engine.turn(session, "test", explicit_skills=["does-not-exist"], auto=False))
        self.assertEqual(events[0]["missing"], ["does-not-exist"])

    def test_select_preview_has_scores(self):
        selection = self.engine.select("議事録を作って")
        self.assertEqual(selection.names, ["minutes"])
        self.assertTrue(selection.auto_scores)

    def test_draft_skill_from_request(self):
        draft = self.engine.draft_skill("週次レポートを作る weekly report")
        self.assertTrue(draft.name)
        self.assertIn("# 手順", draft.instructions)

    def test_parse_draft_handles_code_fence_and_garbage(self):
        good = parse_draft('```json\n{"name":"My Skill","description":"d","instructions":"# 手順\\n1. x"}\n```')
        self.assertEqual(good.name, "my-skill")
        self.assertIn("1. x", good.instructions)
        messy = parse_draft("説明文\n{\"name\":\"x-skill\",\"instructions\":\"手順\"}\n追記")
        self.assertEqual(messy.name, "x-skill")
        raw = parse_draft("JSONではないが手順書っぽいテキスト")
        self.assertTrue(raw.instructions)


class SkillOverrideTest(unittest.TestCase):
    """スキル（frontmatter）による生成パラメータ上書き。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "skills"
        (self.dir / "mika").mkdir(parents=True)
        (self.dir / "mika" / "SKILL.md").write_text(
            "---\nname: mika\ndescription: ペルソナ\ntemperature: 0.85\nmax_output_tokens: 512\n"
            "thinking_budget: 2048\ngreeting: おはよー\n---\n# 返し方\n1. 短く返す\n",
            encoding="utf-8",
        )
        (self.dir / "plain").mkdir()
        (self.dir / "plain" / "SKILL.md").write_text(
            "---\nname: plain\ndescription: 普通のスキル\n---\n# 手順\n1. する\n", encoding="utf-8"
        )
        self.lib = SkillLibrary([self.dir])

    def tearDown(self):
        self.tmp.cleanup()

    def test_skill_exposes_overrides(self):
        mika = self.lib.get("mika")
        self.assertEqual(mika.temperature, 0.85)
        self.assertEqual(mika.max_output_tokens, 512)
        self.assertEqual(mika.thinking_budget, 2048)
        self.assertEqual(mika.greeting, "おはよー")
        self.assertEqual(mika.overrides, {"temperature": 0.85, "max_output_tokens": 512, "thinking_budget": 2048})
        self.assertEqual(self.lib.get("plain").overrides, {})

    def test_temperature_flows_into_generation_config(self):
        root = Path(self.tmp.name)
        settings = Settings(api_key="", provider="mock", skills_dirs=[self.dir], data_dir=root / ".actor",
                            temperature=0.7)
        engine = ChatEngine(settings)
        session = engine.store.create()
        events = list(engine.turn(session, "こんにちは", explicit_skills=["mika"], auto=False))
        prompt = next(e for e in events if e["type"] == "prompt")
        self.assertAlmostEqual(prompt["generation_config"]["temperature"], 0.85)
        self.assertEqual(prompt["generation_config"]["maxOutputTokens"], 512)
        self.assertEqual(prompt["generation_config"]["thinkingConfig"]["thinkingBudget"], 2048)
        self.assertEqual(prompt["overrides"]["temperature"], 0.85)
        self.assertEqual(prompt["override_sources"]["temperature"], "mika")

        selection = events[0]
        self.assertEqual(selection["overrides"]["temperature"], 0.85)
        self.assertAlmostEqual(selection["effective_temperature"], 0.85)

    def test_first_skill_wins_on_conflict(self):
        from actor.skills import resolve_overrides

        values, sources = resolve_overrides([self.lib.get("mika"), self.lib.get("plain")])
        self.assertEqual(values["temperature"], 0.85)
        self.assertEqual(sources["temperature"], "mika")


class RoomsTest(unittest.TestCase):
    """ルーム（共有ログ）の基本動作。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RoomStore(Path(self.tmp.name) / "rooms")

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_diff_polling(self):
        room = self.store.create("actor-x", skills=["mika"])
        first = room.append("user", "こんにちは", author="TRMTKB")
        room.append("assistant", "やあ", author="ミカ")
        self.store.save(room)

        room = self.store.require("actor-x")
        self.assertEqual(room.cursor, 2)
        self.assertEqual(first.seq, 1)
        self.assertEqual([m.seq for m in room.since(1)], [2])
        self.assertEqual(room.since(0)[0].author, "TRMTKB")
        self.assertEqual(room.skills, ["mika"])
        self.assertEqual(room.to_dict(after=1)["count"], 2)
        self.assertEqual(len(room.to_dict(after=1)["messages"]), 1)

    def test_store_survives_deleted_data_dir(self):
        """実行中にデータフォルダごと消されても、書き込み時に作り直す。"""
        import shutil

        room = self.store.create("gone")
        shutil.rmtree(self.store.directory)          # `.actor/` を丸ごと削除
        room.append("user", "まだ居る？")
        self.store.save(room)                        # ここで FileNotFoundError にしない
        self.assertTrue(self.store.path_of("gone").is_file())
        self.assertEqual(self.store.require("gone").messages[0].text, "まだ居る？")

    def test_room_name_validation(self):
        for bad in ["../etc", "a b", "x" * 33, ""]:
            with self.subTest(name=bad), self.assertRaises(ValueError):
                self.store.path_of(bad)
        self.assertTrue(RoomStore.valid_name("actor-x_01"))
        self.assertFalse(RoomStore.valid_name("日本語"))

    def test_list_and_delete(self):
        self.store.create("a")
        self.store.create("b")
        names = [r["name"] for r in self.store.list()]
        self.assertEqual(sorted(names), ["a", "b"])
        self.assertTrue(self.store.delete("a"))
        self.assertEqual([r["name"] for r in self.store.list()], ["b"])

    def test_get_or_create_and_roundtrip(self):
        room = self.store.get_or_create("c", skills=["x"])
        self.assertEqual(self.store.get_or_create("c").name, "c")
        room.append("user", "テスト")
        self.store.save(room)
        restored = Room.from_dict(self.store.get("c").to_dict())
        self.assertEqual(restored.messages[0].text, "テスト")
        self.assertEqual(restored.skills, ["x"])


class RoomApiTest(unittest.TestCase):
    """HTTP 経由のルーム API。"""

    @classmethod
    def setUpClass(cls):
        from actor.server import ActorServer

        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "skills" / "minutes").mkdir(parents=True)
        (root / "skills" / "minutes" / "SKILL.md").write_text(
            SAMPLE_SKILL.format(name="minutes", description="議事録を作る", tag="会議"), encoding="utf-8"
        )
        settings = Settings(api_key="", provider="mock", skills_dirs=[root / "skills"],
                            data_dir=root / ".actor", host="127.0.0.1", port=0)
        cls.server = ActorServer(("127.0.0.1", 0), ChatEngine(settings), quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = RoomClient(f"http://127.0.0.1:{cls.port}", timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_create_post_and_diff(self):
        created = self.client.ensure_room("t-room", ["minutes"])
        self.assertEqual(created["room"]["name"], "t-room")
        self.assertTrue(created["created"])
        again = self.client.ensure_room("t-room")
        self.assertFalse(again["created"])  # 既存なら作成しない

        posted = self.client.post("t-room", "議事録を作って", author="TRMTKB")
        self.assertEqual(posted["message"]["seq"], 1)
        self.assertEqual(posted["room"]["cursor"], 1)

        payload = self.client.messages("t-room", after=0)
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(payload["messages"][0]["author"], "TRMTKB")
        self.assertEqual(self.client.messages("t-room", after=1)["messages"], [])

    def test_reply_true_generates_and_appends(self):
        self.client.ensure_room("r-room", ["minutes"])
        result = self.client.post("r-room", "会議のメモから議事録を作って", reply=True)
        self.assertIsNotNone(result["reply"])
        self.assertEqual(result["reply"]["role"], "assistant")
        self.assertIn("minutes", result["reply"]["skills"])
        self.assertIn("議事録", result["reply"]["text"])
        self.assertEqual(result["room"]["count"], 2)
        self.assertEqual(result["missing_skills"], [])

    def test_missing_skills_are_reported(self):
        self.client.ensure_room("m-room", ["does-not-exist"])
        result = self.client.post("m-room", "テスト", reply=True)
        self.assertEqual(result["missing_skills"], ["does-not-exist"])
        self.assertIsNotNone(result["reply"])

    def test_room_validation_and_404(self):
        with self.assertRaises(RuntimeError):
            self.client.messages("no-such-room", after=0)
        with self.assertRaises(RuntimeError):
            self.client._request("POST", "/api/rooms", {"name": "../evil"})


class BotTest(unittest.TestCase):
    """常駐ボット（tick 単位で検証）。"""

    @classmethod
    def setUpClass(cls):
        from actor.server import ActorServer

        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "skills" / "minutes").mkdir(parents=True)
        (root / "skills" / "minutes" / "SKILL.md").write_text(
            SAMPLE_SKILL.format(name="minutes", description="議事録を作る", tag="会議"), encoding="utf-8"
        )
        cls.root = root
        cls.settings = Settings(api_key="", provider="mock", skills_dirs=[root / "skills"],
                                data_dir=root / ".actor", host="127.0.0.1", port=0)
        cls.server = ActorServer(("127.0.0.1", 0), ChatEngine(cls.settings), quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = RoomClient(f"http://127.0.0.1:{cls.port}", timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def make_bot(self, room: str, **kw) -> RoomBot:
        bot = RoomBot(
            settings=Settings(api_key="", provider="mock", skills_dirs=[self.root / "skills"],
                              data_dir=self.root / f".bot-{room}", host="127.0.0.1", port=0),
            room=room, api_base=f"http://127.0.0.1:{self.port}", interval=0.05, bot_name="ミカ", **kw
        )
        bot.setup()
        return bot

    def test_bot_answers_user_messages(self):
        bot = self.make_bot("b-room", skills=["minutes"])
        self.client.ensure_room("b-room", ["minutes"])
        self.client.post("b-room", "会議メモから議事録を作って", author="TRMTKB")

        handled = bot.tick()
        self.assertEqual(handled, 1)

        room = self.client.messages("b-room", after=0)
        self.assertEqual(len(room["messages"]), 2)
        reply = room["messages"][1]
        self.assertEqual(reply["role"], "assistant")
        self.assertEqual(reply["author"], "ミカ")
        self.assertIn("minutes", reply["skills"])
        self.assertIn("議事録", reply["text"])
        self.assertTrue(reply["simulated"])

        # 同じメッセージに二度返事をしない
        self.assertEqual(bot.tick(), 0)

    def test_bot_ignores_own_messages_and_mention_filter(self):
        bot = self.make_bot("c-room", mention="@actor")
        self.client.ensure_room("c-room")
        self.client.post("c-room", "反応しないはず", author="TRMTKB")
        self.assertEqual(bot.tick(), 0)

        self.client.post("c-room", "@actor これは反応する", author="TRMTKB")
        self.assertEqual(bot.tick(), 1)
        # 自分の投稿（assistant）には反応しない
        self.assertEqual(bot.tick(), 0)

    def test_bot_from_now_skips_history(self):
        self.client.ensure_room("d-room")
        self.client.post("d-room", "過去のメッセージ", author="TRMTKB")
        bot = self.make_bot("d-room", from_now=True)
        self.assertEqual(bot.tick(), 0)
        self.client.post("d-room", "新しいメッセージ", author="TRMTKB")
        self.assertEqual(bot.tick(), 1)
        bot.stop()

    def test_bot_recreates_deleted_room(self):
        """ルームが消えてもボットが復帰できる（データ削除・サーバー再起動など）。"""
        bot = self.make_bot("f-room", skills=["minutes"])
        self.client.ensure_room("f-room")
        self.client.post("f-room", "一回目", author="TRMTKB")
        self.assertEqual(bot.tick(), 1)

        # ルームを削除 → ボットは作り直して待機に戻る
        self.client._request("DELETE", "/api/rooms/f-room")
        self.assertEqual(bot.tick(), 0)
        self.assertEqual(bot.state.cursor, 0)

        # 復帰後も普通に返事ができる
        self.client.post("f-room", "復帰テスト", author="TRMTKB")
        self.assertEqual(bot.tick(), 1)
        messages = self.client.messages("f-room", after=0)["messages"]
        self.assertEqual(messages[-1]["role"], "assistant")

    def test_bot_survives_tool_using_replies(self):
        """道具を使う返事でもボットが落ちない（ログ整形の回帰テスト）。"""
        self.client.ensure_room("tool-room")
        bot = self.make_bot("tool-room", skills=["minutes"])
        self.client.post("tool-room", "今日は何日？ あと README.md も読んで", author="TRMTKB")
        self.assertEqual(bot.tick(), 1)

        payload = self.client.messages("tool-room", after=0)
        reply = [m for m in payload["messages"] if m["role"] == "assistant"][-1]
        self.assertIn("ツールの実行結果", reply["text"])
        self.assertIn("now", reply["tools"])

    def test_bot_persists_cursor(self):
        bot = self.make_bot("e-room")
        self.client.ensure_room("e-room")
        self.client.post("e-room", "ひとつ目", author="TRMTKB")
        self.assertEqual(bot.tick(), 1)
        cursor = bot.state.cursor
        self.assertGreater(cursor, 0)
        self.assertTrue(bot.state_path.is_file())

        # 再起動してもカーソルが維持され、過去分を再処理しない
        bot2 = self.make_bot("e-room")
        self.assertEqual(bot2.state.cursor, cursor)
        self.assertEqual(bot2.tick(), 0)


class ServerTest(unittest.TestCase):
    """実 HTTP 経由で API を検証する。"""

    @classmethod
    def setUpClass(cls):
        from actor.server import ActorServer

        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "skills" / "minutes").mkdir(parents=True)
        (root / "skills" / "minutes" / "SKILL.md").write_text(
            SAMPLE_SKILL.format(name="minutes", description="議事録を作る", tag="会議"), encoding="utf-8"
        )
        settings = Settings(api_key="", provider="mock", skills_dirs=[root / "skills"],
                            data_dir=root / ".actor", host="127.0.0.1", port=0)
        cls.engine = ChatEngine(settings)
        cls.server = ActorServer(("127.0.0.1", 0), cls.engine, quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.url(path), timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def post(self, path: str, payload: dict, raw: bool = False):
        request = urllib.request.Request(
            self.url(path), data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                return response.status, (body if raw else json.loads(body.decode("utf-8")))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def test_health_and_config(self):
        status, data = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["simulated"])
        self.assertEqual(data["skills"], 1)
        status, config = self.get("/api/config")
        self.assertFalse(config["has_api_key"])
        self.assertNotIn("GEMINI", json.dumps(config))

    def test_static_ui_is_served(self):
        with urllib.request.urlopen(self.url("/"), timeout=10) as response:
            html = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("Actor", html)
        with urllib.request.urlopen(self.url("/app.js"), timeout=10) as response:
            self.assertIn("sendMessage", response.read().decode("utf-8"))

    def test_skills_endpoints(self):
        status, data = self.get("/api/skills")
        self.assertEqual([s["name"] for s in data["skills"]], ["minutes"])
        status, detail = self.get("/api/skills/minutes")
        self.assertIn("<skill name", detail["system_block"])
        status, missing = self.get("/api/skills/nope")
        self.assertEqual(status, 404)

        status, created = self.post("/api/skills", {"name": "Temp Skill", "description": "d", "instructions": "# 手順\n1. a"})
        self.assertEqual(status, 201)
        self.assertEqual(created["skill"]["name"], "temp-skill")
        status, updated = self.post("/api/select", {"message": "temp-skill の手順で"})
        self.assertIn("temp-skill", [s["name"] for s in updated["skills"]])

        request = urllib.request.Request(self.url("/api/skills/temp-skill"), headers={"Content-Type": "application/json"}, method="DELETE")
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)

    def test_select_endpoint(self):
        status, data = self.post("/api/select", {"message": "会議の議事録を作って"})
        self.assertEqual(data["names"], ["minutes"])
        self.assertTrue(data["auto_scores"])
        status, _ = self.post("/api/select", {})
        self.assertEqual(status, 200)

    def test_chat_and_sse_stream(self):
        status, data = self.post("/api/chat", {"message": "議事録を作って"})
        self.assertEqual(status, 200)
        self.assertIn("シミュレーションモード", data["text"])
        self.assertEqual(data["skills"], ["minutes"])
        self.assertTrue(data["session_id"])

        # SSE: 最初の数イベントを読む
        request = urllib.request.Request(
            self.url("/api/chat/stream"),
            data=json.dumps({"message": "続けて", "session_id": data["session_id"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            self.assertEqual(response.headers["Content-Type"], "text/event-stream; charset=utf-8")
            seen = []
            buffer = ""
            while len(seen) < 3:
                buffer += response.read(512).decode("utf-8", errors="replace")
                while "\n\n" in buffer:
                    chunk, buffer = buffer.split("\n\n", 1)
                    for line in chunk.splitlines():
                        if line.startswith("data:"):
                            seen.append(json.loads(line[5:].strip()))
                if not buffer and len(seen) < 3:
                    break
        self.assertEqual(seen[0]["type"], "open")
        self.assertEqual(seen[1]["type"], "selection")
        self.assertIn(seen[2]["type"], {"prompt", "thought", "text"})

    def test_bad_json_returns_400(self):
        request = urllib.request.Request(
            self.url("/api/chat"), data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_path_traversal_blocked(self):
        status, _ = self.get("/../actor/config.py")
        self.assertIn(status, (400, 403, 404))

    def test_rooms_endpoints(self):
        status, created = self.post("/api/rooms", {"name": "api-room", "skills": ["minutes"]})
        self.assertIn(status, (200, 201))
        self.assertEqual(created["room"]["name"], "api-room")

        status, posted = self.post("/api/rooms/api-room/messages", {"text": "議事録を作って", "author": "TRMTKB"})
        self.assertEqual(status, 201)
        self.assertEqual(posted["message"]["seq"], 1)

        status, fetched = self.get("/api/rooms/api-room?after=0")
        self.assertEqual(len(fetched["messages"]), 1)
        status, listing = self.get("/api/rooms")
        self.assertTrue(any(r["name"] == "api-room" for r in listing["rooms"]))

        status, replied = self.post("/api/rooms/api-room/messages", {"text": "会議メモから議事録を", "reply": True})
        self.assertIsNotNone(replied["reply"])
        self.assertIn("minutes", replied["reply"]["skills"])

        status, bad = self.post("/api/rooms", {"name": "../evil"})
        self.assertEqual(status, 400)
        status, missing = self.get("/api/rooms/nope")
        self.assertEqual(status, 404)

        request = urllib.request.Request(self.url("/api/rooms/api-room"), headers={"Content-Type": "application/json"}, method="DELETE")
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)

    def test_sessions_endpoints(self):
        status, data = self.post("/api/sessions", {"title": "T", "skills": ["minutes"]})
        session_id = data["session"]["id"]
        status, fetched = self.get(f"/api/sessions/{session_id}")
        self.assertEqual(fetched["session"]["title"], "T")
        status, listing = self.get("/api/sessions")
        self.assertTrue(any(s["id"] == session_id for s in listing["sessions"]))
        request = urllib.request.Request(self.url(f"/api/sessions/{session_id}"), headers={"Content-Type": "application/json"}, method="DELETE")
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)


class ReplTest(unittest.TestCase):
    """REPL のコマンド処理を直接検証する。"""

    def setUp(self):
        from actor.cli import Repl

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "skills" / "minutes").mkdir(parents=True)
        (root / "skills" / "minutes" / "SKILL.md").write_text(
            SAMPLE_SKILL.format(name="minutes", description="議事録を作る", tag="会議"), encoding="utf-8"
        )
        self.settings = Settings(api_key="", provider="mock", skills_dirs=[root / "skills"], data_dir=root / ".actor")
        self.engine = ChatEngine(self.settings)
        self.repl = Repl(self.settings, self.engine)

    def tearDown(self):
        self.tmp.cleanup()

    def test_use_and_clear(self):
        self.repl.handle_command("/use minutes")
        self.assertEqual(self.repl.session.skills, ["minutes"])
        self.repl.handle_command("/use none")
        self.assertEqual(self.repl.session.skills, [])

    def test_toggles(self):
        self.repl.handle_command("/auto off")
        self.assertFalse(self.settings.auto_select)
        self.repl.handle_command("/think on")
        self.assertTrue(self.repl.show_thoughts)
        self.assertGreater(self.settings.thinking_budget, 0)

    def test_new_session(self):
        old = self.repl.session.id
        self.repl.handle_command("/new テスト会話")
        self.assertNotEqual(self.repl.session.id, old)
        self.assertEqual(self.repl.session.title, "テスト会話")

    def test_skill_shortcut_pins(self):
        self.repl.handle_command("/minutes")
        self.assertEqual(self.repl.session.skills, ["minutes"])
        self.repl.handle_command("/unknown-skill")
        self.assertEqual(self.repl.session.skills, ["minutes"])

    def test_send_records_history(self):
        self.repl.send("議事録を作って")
        self.assertEqual(len(self.repl.session.messages), 2)
        self.assertEqual(self.repl.session.messages[0].role, "user")
        self.assertIn("minutes", self.repl.session.messages[0].skills)
        self.assertTrue(self.repl.last_system)

    def test_system_command_output(self):
        self.repl.send("議事録を作って")
        self.repl.handle_command("/system")
        self.assertIn("<skill name=\"minutes\">", self.repl.last_system)


def _genai_available() -> bool:
    try:
        from actor.genai_provider import available

        return available()
    except Exception:
        return False


@unittest.skipUnless(_genai_available(), "google-genai 未導入（pip install google-genai で有効化）")
class GenAiSdkTest(unittest.TestCase):
    """google-genai SDK との橋渡し（通信はしない）。"""

    def setUp(self):
        from actor.genai_provider import GenAiProvider

        self.settings = Settings(api_key="dummy-key", model="gemini-3-flash")
        self.provider = GenAiProvider(self.settings)
        self.provider._load()  # クライアント生成まで（ネットワークは呼ばない）

    def test_config_mapping(self):
        system = build_system_instruction([], preamble="テスト")
        config = self.provider._to_config(
            {"temperature": 0.85, "topP": 0.9, "maxOutputTokens": 512,
             "thinkingConfig": {"thinkingBudget": 2048, "includeThoughts": True}},
            self.provider._to_system(system),
        )
        self.assertAlmostEqual(config.temperature, 0.85)
        self.assertAlmostEqual(config.top_p, 0.9)
        self.assertEqual(config.max_output_tokens, 512)
        self.assertEqual(config.thinking_config.thinking_budget, 2048)
        self.assertIn("テスト", config.system_instruction.parts[0].text)

    def test_contents_conversion(self):
        contents = self.provider._to_contents([
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ])
        self.assertEqual([c.role for c in contents], ["user", "model"])
        self.assertEqual(contents[0].parts[0].text, "a")

    def test_provider_selection(self):
        settings = Settings(api_key="k", provider="genai")
        self.assertEqual(settings.effective_provider, "genai")
        self.assertEqual(get_provider(settings).name, "genai")
        self.assertEqual(get_provider(Settings(api_key="k", provider="auto", gemini_impl="genai")).name, "genai")
        self.assertEqual(get_provider(Settings(api_key="k", provider="auto")).name, "gemini")


class _FakeGemini(BaseHTTPRequestHandler):
    """google-genai SDK が送るリクエストを受け取る偽サーバー（外部通信なし）。"""

    protocol_version = "HTTP/1.1"
    captured: list[dict] = []

    def log_message(self, *args):  # noqa: A003
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        _FakeGemini.captured.append({"path": self.path, "payload": payload,
                                     "headers": {k.lower() for k in self.headers}})
        # tools を渡されていて、まだ functionResponse を返していなければ「道具を使いたい」と応答する
        answered = any(
            "functionResponse" in part
            for content in payload.get("contents", [])
            for part in (content.get("parts") or [])
        )
        if payload.get("tools") and not answered:
            name = payload["tools"][0]["functionDeclarations"][0]["name"]
            call = {"functionCall": {"name": name, "args": {}}}
            if "streamGenerateContent" in self.path:
                chunks = [
                    {"candidates": [{"content": {"role": "model", "parts": [call]}}]},
                    {"candidates": [{"content": {"role": "model", "parts": [call]}}],
                     "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1, "totalTokenCount": 6}},
                ]
                body = b"".join(
                    f"data: {json.dumps(c, ensure_ascii=False)}\r\n\r\n".encode("utf-8") for c in chunks
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
            else:
                body = json.dumps({
                    "candidates": [{"content": {"role": "model", "parts": [call]}}],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1, "totalTokenCount": 6},
                    "modelVersion": "gemini-3-flash",
                }, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if "streamGenerateContent" in self.path:
            chunks = [
                {"candidates": [{"content": {"role": "model", "parts": [{"thought": True, "text": "考え中…"}]}}]},
                {"candidates": [{"content": {"role": "model", "parts": [{"text": "やあ、"}]}}]},
                {"candidates": [{"content": {"role": "model", "parts": [{"text": "きみ。"}]}}],
                 "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "totalTokenCount": 8}},
            ]
            body = b"".join(
                f"data: {json.dumps(c, ensure_ascii=False)}\r\n\r\n".encode("utf-8") for c in chunks
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            body = json.dumps({
                "candidates": [{"content": {"role": "model", "parts": [{"text": "やあ、きみ。"}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "totalTokenCount": 8},
                "modelVersion": "gemini-3-flash",
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(_genai_available(), "google-genai 未導入（pip install google-genai で有効化）")
class GenAiWireTest(unittest.TestCase):
    """SDK 経由で「正しい形のリクエスト」が出ているかを偽サーバーで検証する。"""

    @classmethod
    def setUpClass(cls):
        from actor.genai_provider import GenAiProvider

        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "skills" / "mika").mkdir(parents=True)
        (root / "skills" / "mika" / "SKILL.md").write_text(
            "---\nname: mika\ndescription: ペルソナ\ntemperature: 0.85\n---\n# 返し方\n1. 短く返す\n",
            encoding="utf-8",
        )
        _FakeGemini.captured = []
        cls.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeGemini)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        port = cls.httpd.server_address[1]

        cls.settings = Settings(api_key="dummy", model="gemini-3-flash",
                                base_url=f"http://127.0.0.1:{port}", skills_dirs=[root / "skills"])
        cls.provider = GenAiProvider(cls.settings)
        cls.library = SkillLibrary(cls.settings.skills_dirs)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def _system_and_config(self):
        mika = self.library.get("mika")
        system = build_system_instruction([mika], preamble="あなたは「ミカ」です。")
        config = self.settings.generation_config(**mika.overrides)
        return system, config

    def test_request_shape_and_override(self):
        system, config = self._system_and_config()
        result = self.provider.generate([{"role": "user", "content": "おはよう"}], system, generation_config=config)
        self.assertEqual(result["text"], "やあ、きみ。")

        sent = _FakeGemini.captured[-1]
        self.assertIn(":generateContent", sent["path"])
        self.assertIn("x-goog-api-key", sent["headers"])
        payload = sent["payload"]
        self.assertEqual([c["role"] for c in payload["contents"]], ["user"])
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "おはよう")
        self.assertIn('<skill name="mika">', payload["systemInstruction"]["parts"][0]["text"])
        self.assertAlmostEqual(payload["generationConfig"]["temperature"], 0.85)

    def test_stream_separates_thought_and_text(self):
        system, config = self._system_and_config()
        events = list(self.provider.stream([{"role": "user", "content": "おはよう"}], system, generation_config=config))
        self.assertEqual([e["type"] for e in events], ["thought", "text", "text", "done"])
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "text"), "やあ、きみ。")
        self.assertEqual(events[-1]["usage"]["totalTokenCount"], 8)


@unittest.skipUnless(_genai_available(), "google-genai 未導入（pip install google-genai で有効化）")
class GenAiToolTest(unittest.TestCase):
    """SDK 経由でも function calling が回るか（偽サーバー / 2往復）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "skills" / "mika").mkdir(parents=True)
        (root / "skills" / "mika" / "SKILL.md").write_text(
            "---\nname: mika\ndescription: ペルソナ\n---\n# 返し方\n1. 短く返す\n", encoding="utf-8"
        )
        _FakeGemini.captured = []
        cls.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeGemini)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        port = cls.httpd.server_address[1]
        cls.settings = Settings(api_key="dummy", model="gemini-3-flash",
                                base_url=f"http://127.0.0.1:{port}", skills_dirs=[root / "skills"],
                                data_dir=root / ".actor", tools_root=root)
        from actor.genai_provider import GenAiProvider

        cls.engine = ChatEngine(cls.settings, provider=GenAiProvider(cls.settings))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def test_tool_loop_through_sdk(self):
        _FakeGemini.captured = []
        session = self.engine.store.create()
        events = list(self.engine.turn(session, "いま何時？", auto=False, stream=True))
        kinds = [e["type"] for e in events]
        self.assertIn("function_call", kinds)
        self.assertIn("tool_call", kinds)
        self.assertIn("tool_result", kinds)
        self.assertEqual(next(e for e in events if e["type"] == "done")["tool_calls"][0]["name"], "now")

        first, second = _FakeGemini.captured[0], _FakeGemini.captured[-1]
        # 1往復目: functionDeclarations が渡っている
        declarations = first["payload"]["tools"][0]["functionDeclarations"]
        self.assertIn("now", [d["name"] for d in declarations])
        # SDK は JSON Schema をそのまま渡す（parameters_json_schema → ワイヤでは snake_case）
        self.assertEqual(declarations[0]["parameters_json_schema"]["type"], "object")
        # 2往復目: functionResponse が contents に載っている
        parts = [p for c in second["payload"]["contents"] for p in (c.get("parts") or [])]
        self.assertTrue(any("functionCall" in p for p in parts))
        responses = [p["functionResponse"] for p in parts if "functionResponse" in p]
        self.assertEqual(responses[0]["name"], "now")
        self.assertTrue(responses[0]["response"]["result"])

    def test_attachment_reaches_sdk_as_inline_data(self):
        import base64

        _FakeGemini.captured = []
        session = self.engine.store.create()
        blob = base64.b64encode(b"\x89PNG dummy").decode("ascii")
        list(self.engine.turn(session, "この画像を見て", auto=False,
                              attachments=[{"name": "a.png", "mime_type": "image/png", "data": blob}],
                              stream=False))
        parts = [p for c in _FakeGemini.captured[-1]["payload"]["contents"]
                 for p in (c.get("parts") or [])]
        inline = [p["inlineData"] for p in parts if "inlineData" in p]
        mime = inline[0].get("mimeType") or inline[0].get("mime_type")
        self.assertEqual(mime, "image/png")
        self.assertEqual(inline[0]["data"], blob)


class CliArgTest(unittest.TestCase):
    def test_split_command(self):
        from actor.cli import _split_command

        self.assertEqual(_split_command([]), ["chat"])
        self.assertEqual(_split_command(["serve", "--port", "8080"]), ["serve", "--port", "8080"])
        self.assertEqual(_split_command(["こんにちは"]), ["chat", "こんにちは"])
        self.assertEqual(_split_command(["--model", "x", "doctor"]), ["doctor", "--model", "x"])
        self.assertEqual(_split_command(["--version"]), ["--version"])
        self.assertEqual(_split_command(["--json", "serve"]), ["serve", "--json"])
        self.assertEqual(_split_command(["--skills-dir", "./s", "skills", "list"]),
                         ["skills", "--skills-dir", "./s", "list"])

    def test_parser_bot_and_rooms(self):
        from actor.cli import build_parser, _split_command

        args = build_parser().parse_args(_split_command(
            ["bot", "--room", "actor-x", "--skills", "mika", "--interval", "2", "--once", "--from-now"]))
        self.assertEqual(args.command, "bot")
        self.assertEqual(args.room, "actor-x")
        self.assertEqual(args.use_skills, ["mika"])
        self.assertTrue(args.once)
        self.assertTrue(args.from_now)

        args = build_parser().parse_args(_split_command(["rooms", "show", "actor-x"]))
        self.assertEqual(args.command, "rooms")
        self.assertEqual(args.args, ["show", "actor-x"])

        args = build_parser().parse_args(_split_command(["rooms", "say", "こんにちは", "--reply"]))
        self.assertTrue(args.reply)

    def test_parser_accepts_each_command(self):
        from actor.cli import build_parser, _split_command

        for argv in (["skills", "list"], ["models"], ["serve", "--port", "9999"], ["doctor"], ["chat", "hello"]):
            with self.subTest(argv=argv):
                args = build_parser().parse_args(_split_command(argv))
                self.assertTrue(hasattr(args, "command"))
        # --help / --version は argparse が SystemExit で終了する（正常動作）
        for argv in (["--help"], ["--version"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                build_parser().parse_args(_split_command(argv))


class CliTest(unittest.TestCase):
    def test_one_shot_json(self):
        import subprocess

        tmp = tempfile.TemporaryDirectory()
        env = dict(os.environ, ACTOR_SKILLS_DIR=str(Path(__file__).resolve().parent.parent / "skills"),
                   ACTOR_DATA_DIR=str(Path(tmp.name) / ".actor"), ACTOR_MOCK_DELAY="0")
        env.pop("GEMINI_API_KEY", None)
        result = subprocess.run(
            [sys.executable, "-m", "actor", "--json", "Create minutes from meeting notes"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent), env=env, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("minutes-writer", payload["skills"])
        self.assertTrue(payload["simulated"])
        tmp.cleanup()

    def test_skills_export_command(self):
        import subprocess

        tmp = tempfile.TemporaryDirectory()
        env = dict(os.environ, ACTOR_SKILLS_DIR=str(Path(__file__).resolve().parent.parent / "skills"),
                   ACTOR_DATA_DIR=str(Path(tmp.name) / ".actor"))
        out = Path(tmp.name) / "out.zip"
        result = subprocess.run(
            [sys.executable, "-m", "actor", "skills", "export", "minutes-writer", "-o", str(out)],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent), env=env, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out.is_file())
        import zipfile

        with zipfile.ZipFile(out) as zf:
            self.assertIn("SKILL.md", zf.namelist())
        tmp.cleanup()



# ----------------------------------------------------------------------
# ツール（function calling）
# ----------------------------------------------------------------------
class ToolRegistryTest(unittest.TestCase):
    """ツール層の単体テスト（サンドボックス / allowed-tools / 危険な道具）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "note.md").write_text("メモの内容", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_and_subset(self):
        from actor.tools import DEFAULT_ENABLED, ToolRegistry, build_registry

        settings = Settings(api_key="", provider="mock", skills_dirs=[self.root],
                            tools_root=self.root)
        registry = build_registry(settings, skill_provider=SkillLibrary(settings.skills_dirs).all)
        self.assertEqual(registry.names(), list(DEFAULT_ENABLED))
        self.assertIn("now", registry)
        self.assertNotIn("write_file", registry)  # 既定では書き込み不可

        subset = ToolRegistry.build(["now"], root=self.root).subset(["read_file"])
        self.assertEqual(subset.names(), [])  # 持っていない道具は絞ると空になる
        self.assertEqual(ToolRegistry.build(["now"]).subset(None).names(), ["now"])

    def test_dangerous_tools_need_opt_in(self):
        from actor.tools import ToolRegistry

        without = ToolRegistry.build(["write_file", "http_get"], root=self.root)
        self.assertEqual(without.names(), [])
        with_opt = ToolRegistry.build(["write_file", "http_get"], root=self.root,
                                      allow_write=True, allow_network=True)
        self.assertEqual(sorted(with_opt.names()), ["http_get", "write_file"])
        self.assertTrue(all(t.dangerous for t in with_opt.all()))

    def test_sandbox_blocks_outside_paths(self):
        from actor.tools import ToolRegistry

        registry = ToolRegistry.build(["read_file", "list_files"], root=self.root)
        ok = registry.execute("read_file", {"path": "note.md"})
        self.assertTrue(ok.ok)
        self.assertIn("メモの内容", ok.content)

        for bad in ["../secret.txt", "/etc/passwd"]:
            with self.subTest(path=bad):
                result = registry.execute("read_file", {"path": bad})
                self.assertFalse(result.ok)
        listing = registry.execute("list_files", {"path": "."})
        self.assertIn("note.md", listing.content)

    def test_unknown_tool_reports_error(self):
        from actor.tools import ToolRegistry

        result = ToolRegistry.build(["now"]).execute("nope", {})
        self.assertFalse(result.ok)
        self.assertIn("使えません", result.error)

    def test_declarations_shape(self):
        from actor.tools import ToolRegistry

        registry = ToolRegistry.build(["now", "read_file"], root=self.root)
        declarations = registry.declarations()
        self.assertEqual([d["name"] for d in declarations], ["now", "read_file"])
        for declaration in declarations:
            self.assertIn("description", declaration)
            self.assertEqual(declaration["parameters"]["type"], "object")
        rest = registry.to_rest_tools()
        self.assertEqual(len(rest), 1)
        self.assertIn("functionDeclarations", rest[0])


class ToolLoopTest(unittest.TestCase):
    """モデルが functionCall を返したときに、ループが道具を実行して履歴に戻すか。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "skills" / "minutes").mkdir(parents=True)
        (root / "skills" / "minutes" / "SKILL.md").write_text(
            SAMPLE_SKILL.format(name="minutes", description="議事録を作る", tag="会議"), encoding="utf-8"
        )
        # allowed-tools を宣言したスキル（道具が絞られる）
        (root / "skills" / "limited").mkdir(parents=True)
        (root / "skills" / "limited" / "SKILL.md").write_text(
            "---\nname: limited\ndescription: 絞られた道具\nallowed-tools: [now]\n---\n# 手順\n1. 実行\n",
            encoding="utf-8",
        )
        self.root = root
        self.settings = Settings(api_key="", provider="mock", skills_dirs=[root / "skills"],
                                 data_dir=root / ".actor", tools_root=root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mock_provider_runs_a_tool(self):
        engine = ChatEngine(self.settings)
        session = engine.store.create()
        events = list(engine.turn(session, "今日は何日？", auto=False))
        kinds = [e["type"] for e in events]
        self.assertIn("tool_call", kinds)
        names = [e["name"] for e in events if e["type"] == "tool_call"]
        self.assertEqual(names, ["now"])
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual([c["name"] for c in done["tool_calls"]], ["now"])
        self.assertEqual(session.messages[-1].tools, ["now"])

    def test_loop_returns_function_response_to_model(self):
        """REST の function_call イベント経路でも、結果がモデルへ返る。"""
        from actor.tools import ToolRegistry

        captured = []

        class ScriptedProvider(MockProvider):
            is_mock = False  # シミュレータ表記を出さない

            def stream(self, messages, system_instruction=None, *, generation_config=None,
                       tools=None, model=None, **kwargs):
                captured.append(json.loads(json.dumps(messages, default=str)))
                if len(captured) == 1:
                    self.declared = tools
                    yield {"type": "function_call", "call": {"name": "read_file", "args": {"path": "note.md"}}}
                    yield {"type": "done", "text": "", "usage": {}, "model": "test-model"}
                    return
                yield {"type": "text", "text": "読みました。"}
                yield {"type": "done", "text": "", "usage": {"totalTokenCount": 3}, "model": "test-model"}

        (self.root / "note.md").write_text("秘密のメモ", encoding="utf-8")
        provider = ScriptedProvider(self.settings)
        engine = ChatEngine(self.settings, provider=provider)
        session = engine.store.create()
        events = list(engine.turn(session, "note.md を読んで", auto=False))

        self.assertEqual([e["type"] for e in events if e["type"].startswith("tool")],
                         ["tool_call", "tool_result"])
        result = next(e for e in events if e["type"] == "tool_result")
        self.assertTrue(result["ok"])
        self.assertIn("秘密のメモ", result["result"])
        self.assertEqual("".join(e.get("text", "") for e in events if e["type"] == "text"), "読みました。")

        # 2 ラウンド目の履歴に functionCall / functionResponse が入っているか
        second = captured[1]
        model_parts = [p for m in second if m["role"] == "model" for p in m.get("parts", [])]
        response_parts = [p for m in second if m["role"] == "user" for p in m.get("parts", [])]
        self.assertTrue(any("functionCall" in p for p in model_parts))
        self.assertEqual(response_parts[-1]["functionResponse"]["name"], "read_file")
        self.assertIn("秘密のメモ", response_parts[-1]["functionResponse"]["response"]["result"])

    def test_non_stream_path_also_runs_tools(self):
        """非ストリーム経路（常駐ボットの既定）でも道具が動く。"""
        engine = ChatEngine(self.settings)
        session = engine.store.create()
        events = list(engine.turn(session, "今日は何日？", auto=False, stream=False))
        self.assertEqual([e["name"] for e in events if e["type"] == "tool_call"], ["now"])
        self.assertEqual([e["name"] for e in events if e["type"] == "tool_result"], ["now"])
        self.assertIn("2026", session.messages[-1].content)   # 実行結果が本文へ反映される
        self.assertEqual(session.messages[-1].tools, ["now"])
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["tool_calls"][0]["name"], "now")

    def test_allowed_tools_limits_what_the_model_sees(self):
        engine = ChatEngine(self.settings)
        limited = engine.library.get("limited")
        self.assertEqual(engine.tools_for([limited]).names(), ["now"])
        self.assertEqual(sorted(engine.tools_for([]).names()),
                         ["list_files", "list_skills", "now", "read_file", "read_skill"])

    def test_tool_limit_stops_runaway_loops(self):
        class AlwaysCalls(MockProvider):
            is_mock = False

            def stream(self, messages, system_instruction=None, *, generation_config=None,
                       tools=None, model=None, **kwargs):
                yield {"type": "function_call", "call": {"name": "now", "args": {}}}
                yield {"type": "done", "text": "", "usage": {}, "model": "test-model"}

        settings = Settings(api_key="", provider="mock", skills_dirs=[self.root / "skills"],
                            data_dir=self.root / ".actor", tools_root=self.root, max_tool_rounds=3)
        engine = ChatEngine(settings, provider=AlwaysCalls(settings))
        session = engine.store.create()
        events = list(engine.turn(session, "延々と道具を呼ぶ", auto=False))
        kinds = [e["type"] for e in events]
        # 最後の 1 ラウンドは「答えを書く余地」として残すので、実行は max_tool_rounds - 1 回で打ち切る
        self.assertEqual(len([e for e in events if e["type"] == "tool_call"]), 2)
        self.assertIn("tool_limit", kinds)
        self.assertEqual(kinds[-1], "done")


# ----------------------------------------------------------------------
# 添付（画像 / PDF）
# ----------------------------------------------------------------------
class AttachmentTest(unittest.TestCase):
    """添付つきのターン（プロバイダへ inlineData で渡るか / 保存されるか）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "skills" / "minutes").mkdir(parents=True)
        (root / "skills" / "minutes" / "SKILL.md").write_text(
            SAMPLE_SKILL.format(name="minutes", description="議事録を作る", tag="会議"), encoding="utf-8"
        )
        self.root = root
        self.settings = Settings(api_key="", provider="mock", skills_dirs=[root / "skills"],
                                 data_dir=root / ".actor")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _png_b64() -> str:
        import base64

        # 1x1 の透過 PNG
        blob = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
        return base64.b64encode(blob).decode("ascii")

    def test_attachment_reaches_provider_and_is_stored(self):
        captured = []

        class Capturing(MockProvider):
            def generate(self, messages, system_instruction=None, *, generation_config=None,
                         tools=None, model=None, **kwargs):
                captured.append(json.loads(json.dumps(messages, default=str)))
                return super().generate(messages, system_instruction,
                                        generation_config=generation_config, tools=tools, model=model, **kwargs)

        engine = ChatEngine(self.settings, provider=Capturing(self.settings))
        session = engine.store.create()
        attachments = [{"name": "pic.png", "mime_type": "image/png", "data": self._png_b64()}]
        list(engine.turn(session, "この画像を見て", auto=False, attachments=attachments))

        images = captured[-1][0]["images"]
        self.assertEqual(images[0]["mime_type"], "image/png")
        stored = session.messages[0].attachments
        self.assertEqual(stored[0]["name"], "pic.png")
        self.assertTrue(stored[0]["data"])

    def test_size_limit_is_enforced(self):
        engine = ChatEngine(self.settings)
        session = engine.store.create()
        huge = [{"name": "big.pdf", "mime_type": "application/pdf", "size": 99 * 1024 * 1024, "data": "AA=="}]
        with self.assertRaises(ValueError):
            list(engine.turn(session, "見て", auto=False, attachments=huge))

    def test_rooms_keep_attachment_metadata(self):
        store = RoomStore(self.root / "rooms")
        room = store.create("att")
        room.append("user", "図を見て", attachments=[
            {"name": "a.png", "mime_type": "image/png", "size": 3, "data": "AAA"},
        ])
        store.save(room)
        restored = store.require("att")
        self.assertEqual(restored.messages[0].attachments[0]["name"], "a.png")
        # 一覧・ポーリングでは base64 を返さない（明示したときだけ返す）
        public = restored.to_dict(after=0)["messages"][0]["attachments"][0]
        self.assertNotIn("data", public)
        private = restored.to_dict(after=0, include_attachment_data=True)["messages"][0]["attachments"][0]
        self.assertEqual(private["data"], "AAA")


class RoomAuthTest(unittest.TestCase):
    """ルームのトークン認証。"""

    @classmethod
    def setUpClass(cls):
        from actor.server import ActorServer

        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "skills" / "minutes").mkdir(parents=True)
        (root / "skills" / "minutes" / "SKILL.md").write_text(
            SAMPLE_SKILL.format(name="minutes", description="議事録を作る", tag="会議"), encoding="utf-8"
        )
        cls.settings = Settings(api_key="", provider="mock", skills_dirs=[root / "skills"],
                                data_dir=root / ".actor", host="127.0.0.1", port=0,
                                room_token="s3cret", room_protect_reads=True)
        cls.server = ActorServer(("127.0.0.1", 0), ChatEngine(cls.settings), quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _call(self, path: str, payload: dict | None = None, token: str = "", method: str = ""):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Actor-Token"] = token
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base + path, data=data, headers=headers,
                                         method=method or ("POST" if data else "GET"))
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def test_write_requires_token(self):
        status, body = self._call("/api/rooms", {"name": "locked-room"})
        self.assertEqual(status, 403)
        self.assertIn("トークン", body["error"])

        status, _ = self._call("/api/rooms", {"name": "locked-room"}, token="s3cret")
        self.assertEqual(status, 201)
        # 削除も同様に保護される
        status, _ = self._call("/api/rooms/unlocked-room", method="DELETE")
        self.assertEqual(status, 403)

    def test_reads_are_protected_when_configured(self):
        self._call("/api/rooms", {"name": "auth-room"}, token="s3cret")
        self._call("/api/rooms/auth-room/messages", {"text": "やあ"}, token="s3cret")

        status, _ = self._call("/api/rooms/auth-room?after=0")
        self.assertEqual(status, 403)
        status, _ = self._call("/api/rooms/auth-room?after=0&token=s3cret")
        self.assertEqual(status, 403)
        status, payload = self._call("/api/rooms/auth-room?after=0", token="s3cret")
        self.assertEqual(status, 200)
        self.assertEqual(payload["messages"][0]["text"], "やあ")

    def test_bearer_header_is_accepted(self):
        request = urllib.request.Request(
            self.base + "/api/rooms", data=json.dumps({"name": "bearer-room"}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer s3cret"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 201)

    def test_room_client_sends_token(self):
        client = RoomClient(self.base, token="s3cret")
        client.ensure_room("client-room")
        client.post("client-room", "こんにちは")
        self.assertEqual(client.messages("client-room", after=0)["messages"][0]["author"], "user")


class WebhookTest(unittest.TestCase):
    """Webhook（署名つきの POST が飛ぶか）。"""

    received: list = []
    secret = "hook-secret"

    @classmethod
    def setUpClass(cls):
        outer = cls

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                outer.received.append({
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": body,
                    "json": json.loads(body.decode("utf-8")),
                })
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):  # 静かに
                return

        cls.httpd = HTTPServerProc(("127.0.0.1", 0), Receiver)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/hook"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        WebhookTest.received = []

    def test_dispatcher_signs_and_retries(self):
        from actor.webhooks import WebhookDispatcher, sign_payload

        dispatcher = WebhookDispatcher(url=self.url, secret=self.secret, events=["message"])
        try:
            self.assertTrue(dispatcher.emit("message", {"room": "r", "text": "やあ"}))
            self.assertFalse(dispatcher.emit("other", {"room": "r"}))  # 対象外イベント
            deadline = time.time() + 5
            while time.time() < deadline and not WebhookTest.received:
                time.sleep(0.05)
        finally:
            dispatcher.shutdown()

        self.assertEqual(len(WebhookTest.received), 1)
        delivery = WebhookTest.received[0]
        self.assertEqual(delivery["json"]["event"], "message")
        self.assertEqual(delivery["json"]["room"], "r")
        self.assertEqual(delivery["headers"]["X-Actor-Event"], "message")
        self.assertEqual(delivery["headers"]["X-Actor-Signature"],
                         sign_payload(self.secret, delivery["body"]))

    def test_room_message_triggers_webhook(self):
        from actor.server import ActorServer

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            settings = Settings(api_key="", provider="mock", skills_dirs=[root / "skills"],
                                data_dir=root / ".actor", host="127.0.0.1", port=0,
                                webhook_url=self.url, webhook_secret=self.secret,
                                webhook_events=["message"])
            server = ActorServer(("127.0.0.1", 0), ChatEngine(settings), quiet=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = RoomClient(f"http://127.0.0.1:{server.server_address[1]}")
                client.ensure_room("hook-room")
                client.post("hook-room", "通知して")
                deadline = time.time() + 5
                while time.time() < deadline and not WebhookTest.received:
                    time.sleep(0.05)
            finally:
                server.shutdown()
                server.server_close()
        finally:
            tmp.cleanup()

        self.assertTrue(WebhookTest.received, "Webhook が届きませんでした")
        payload = WebhookTest.received[0]["json"]
        self.assertEqual(payload["room"], "hook-room")
        self.assertEqual(payload["message"]["text"], "通知して")



# ----------------------------------------------------------------------
# API キーの置き場所（.env / 保存ファイル / CLI / UI）
# ----------------------------------------------------------------------
class ApiKeyConfigTest(unittest.TestCase):
    """キーの優先順位と保存（``.actor/api_key``）の挙動。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / ".actor"

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **env):
        return load_settings(env={"ACTOR_DATA_DIR": str(self.data), **env},
                             env_file=Path(self.tmp.name) / "no-such.env")

    def test_saved_key_is_used_and_not_leaked(self):
        from actor.config import clear_api_key, mask_key, write_api_key

        settings = self.make()
        self.assertFalse(settings.api_key)
        self.assertEqual(settings.api_key_source, "")

        path = write_api_key(settings, "fixture-TESTKEY1234567890")
        self.assertEqual(path, self.data / "api_key")
        self.assertEqual(path.read_text(encoding="utf-8").strip(), "fixture-TESTKEY1234567890")
        if os.name != "nt":
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")

        loaded = self.make()
        self.assertEqual(loaded.api_key, "fixture-TESTKEY1234567890")
        self.assertEqual(loaded.api_key_source, "file")
        self.assertEqual(loaded.effective_provider, "gemini")   # キーがあるので mock ではない
        self.assertEqual(mask_key(loaded.api_key), "fixtur…7890")
        # /api/config 相当の辞書には生のキーを載せない
        payload = loaded.to_dict()
        self.assertEqual(payload["api_key"], True)
        self.assertNotIn("fixture-TESTKEY1234567890", json.dumps(payload))

        self.assertTrue(clear_api_key(loaded))
        self.assertFalse(self.make().api_key)

    def test_env_wins_over_saved_file(self):
        from actor.config import write_api_key

        write_api_key(self.make(), "fixture-FROMFILE1234567890")
        settings = load_settings(
            env={"ACTOR_DATA_DIR": str(self.data), "GEMINI_API_KEY": "fixture-FROMENV1234567890"},
            env_file=Path(self.tmp.name) / "no-such.env",
        )
        self.assertEqual(settings.api_key, "fixture-FROMENV1234567890")
        self.assertEqual(settings.api_key_source, "env")

    def test_dotenv_is_read_from_project_root(self):
        env_file = Path(self.tmp.name) / "custom.env"
        env_file.write_text("GEMINI_API_KEY=fixture-FROMDOTENV123456789\n", encoding="utf-8")
        settings = load_settings(env={"ACTOR_DATA_DIR": str(self.data)}, env_file=env_file)
        self.assertEqual(settings.api_key, "fixture-FROMDOTENV123456789")
        self.assertEqual(settings.api_key_source, ".env")


class ApiKeyApiTest(unittest.TestCase):
    """UI / HTTP からキーを入れて、その場で実モデルへ切り替わるか（偽 Gemini で検証）。"""

    @classmethod
    def setUpClass(cls):
        from actor.server import ActorServer

        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "skills" / "minutes").mkdir(parents=True)
        (root / "skills" / "minutes" / "SKILL.md").write_text(
            SAMPLE_SKILL.format(name="minutes", description="議事録を作る", tag="会議"), encoding="utf-8"
        )
        _FakeGemini.captured = []
        cls.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeGemini)
        cls.gemini_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.gemini_thread.start()
        cls.fake_base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

        cls.data = root / ".actor"
        cls.settings = Settings(api_key="", provider="auto", gemini_impl="gemini",
                                base_url=cls.fake_base, model="gemini-3-flash",
                                skills_dirs=[root / "skills"], data_dir=cls.data,
                                host="127.0.0.1", port=0)
        cls.server = ActorServer(("127.0.0.1", 0), ChatEngine(cls.settings), quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        # テストごとに「キー未設定の状態」から始める（クラス内でサーバーを共有しているため）
        (self.data / "api_key").unlink(missing_ok=True)
        engine = self.server.engine
        engine.settings.api_key = ""
        engine.settings.api_key_source = ""
        engine.provider = get_provider(engine.settings)

    def call(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def test_status_starts_without_key(self):
        status, info = self.call("GET", "/api/key")
        self.assertEqual(status, 200)
        self.assertFalse(info["has_key"])
        self.assertTrue(info["simulated"])

    def test_set_switches_to_real_provider_and_chats(self):
        status, info = self.call("POST", "/api/key", {"api_key": "fixture-UIKEY1234567890abcd"})
        self.assertEqual(status, 200, info)
        self.assertEqual(info["provider"], "gemini")
        self.assertFalse(info["simulated"])
        self.assertEqual((self.data / "api_key").read_text(encoding="utf-8").strip(),
                         "fixture-UIKEY1234567890abcd")

        # 切り替え後は実 API（偽サーバー）へ投げられる
        _FakeGemini.captured = []
        status, payload = self.call("POST", "/api/chat", {"message": "こんにちは", "auto": False})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["text"], "やあ、きみ。")
        self.assertFalse(payload["simulated"])
        self.assertIn(":generateContent", _FakeGemini.captured[-1]["path"])

        # /api/config は生のキーを返さない
        status, config = self.call("GET", "/api/config")
        self.assertTrue(config["has_api_key"])
        self.assertNotIn("fixture-UIKEY1234567890abcd", json.dumps(config))

    def test_invalid_keys_are_rejected(self):
        for bad, hint in [("", "必須"), ("fixture- with space 1234567890", "空白"),
                          ("short", "短すぎ"), ("x" * 19, "短すぎ")]:
            with self.subTest(key=bad[:12]):
                status, body = self.call("POST", "/api/key", {"api_key": bad})
                self.assertEqual(status, 400)
                self.assertIn(hint, body["error"])

    def test_check_failure_keeps_simulator(self):
        """繋がらないキーでも、会話を壊さずシミュレータのまま続けられる。"""
        settings = self.server.engine.settings
        original = settings.base_url
        settings.base_url = "http://127.0.0.1:9"      # 到達できない URL
        try:
            status, info = self.call("POST", "/api/key",
                                     {"api_key": "fixture-NOPE1234567890abcd", "check": True})
            self.assertEqual(status, 200, info)
            self.assertFalse(info["check"]["ok"])
            self.assertEqual(info["provider"], "mock")   # 切り替えずに戻す
            self.assertTrue(info["simulated"])
            self.assertTrue((self.data / "api_key").is_file())   # 保存自体はされている

            status, payload = self.call("POST", "/api/chat", {"message": "やあ", "auto": False})
            self.assertEqual(status, 200)
            self.assertTrue(payload["simulated"])        # 会話は続けられる
        finally:
            settings.base_url = original

    def test_clear_returns_to_simulator(self):
        self.call("POST", "/api/key", {"api_key": "fixture-CLEARME1234567890"})
        status, info = self.call("DELETE", "/api/key")
        self.assertEqual(status, 200)
        self.assertTrue(info["simulated"])
        self.assertFalse((self.data / "api_key").exists())
        status, payload = self.call("POST", "/api/chat", {"message": "やあ", "auto": False})
        self.assertTrue(payload["simulated"])

    def test_check_reports_api_health(self):
        status, info = self.call("POST", "/api/key", {"api_key": "fixture-CHECKME1234567890", "check": True})
        self.assertEqual(status, 200, info)
        self.assertIsNotNone(info["check"])


class ApiKeyCliTest(unittest.TestCase):
    """``actor key set / show / clear``（CLI からの保存）。"""

    def run_cli(self, args, data_dir, stdin: str = ""):
        import subprocess

        env = dict(os.environ, ACTOR_DATA_DIR=str(data_dir))
        env.pop("GEMINI_API_KEY", None)
        return subprocess.run([sys.executable, "-m", "actor", *args], input=stdin,
                              capture_output=True, text=True, env=env, timeout=60,
                              cwd=str(Path(__file__).resolve().parent.parent))

    def test_set_show_clear(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            data_dir = Path(tmp.name) / ".actor"
            shown = self.run_cli(["key", "show"], data_dir)
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertIn("未設定", shown.stdout)

            saved = self.run_cli(["key", "set", "--stdin"], data_dir, stdin="fixture-CLIKEY1234567890\n")
            self.assertEqual(saved.returncode, 0, saved.stderr)
            self.assertIn(".actor/api_key", saved.stdout)

            shown = self.run_cli(["key", "show"], data_dir)
            self.assertIn("fixtur…7890", shown.stdout)
            self.assertNotIn("fixture-CLIKEY1234567890", shown.stdout)   # 生の値は出さない

            cleared = self.run_cli(["key", "clear"], data_dir)
            self.assertEqual(cleared.returncode, 0, cleared.stderr)
            self.assertIn("削除しました", cleared.stdout)
            self.assertFalse((data_dir / "api_key").exists())

            bad = self.run_cli(["key", "set", "--stdin"], data_dir, stdin="has space in it 1234567890\n")
            self.assertEqual(bad.returncode, 2)
        finally:
            tmp.cleanup()



# ----------------------------------------------------------------------
# 複数キー（プール・自動ローテーション・429 フェイルオーバー）
# ----------------------------------------------------------------------
class _FakeRotatingGemini(BaseHTTPRequestHandler):
    """キーごとに 429 / 200 を返す偽 Gemini（レート制限の再現・外部通信なし）。"""

    protocol_version = "HTTP/1.1"
    limited: set = set()      # 429 を返すキー
    seen: list = []           # 受け取ったキーの順番（ローテーションの検証用）

    def log_message(self, *args):  # noqa: A003
        pass

    def do_GET(self):  # noqa: N802
        self._respond()

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._respond()

    def _respond(self):
        key = (self.headers.get("x-goog-api-key") or "").strip()
        _FakeRotatingGemini.seen.append(key)
        if key in _FakeRotatingGemini.limited:
            body = json.dumps({
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    # わざとキー本体を混ぜて、伏せ字（redact）が効くことも確かめる
                    "message": f"Quota exceeded for {key} (generateRequestsPerDayPerProject)",
                    "details": [{
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "31s",
                    }],
                }
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.endswith("/models"):
            body = json.dumps({"models": [
                {"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]},
            ]}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        elif "streamGenerateContent" in self.path:
            chunks = [
                {"candidates": [{"content": {"role": "model", "parts": [{"text": "やあ、"}]}}]},
                {"candidates": [{"content": {"role": "model", "parts": [{"text": "きみ。"}]}}],
                 "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "totalTokenCount": 8}},
            ]
            body = b"".join(
                f"data: {json.dumps(c, ensure_ascii=False)}\r\n\r\n".encode("utf-8") for c in chunks
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            body = json.dumps({
                "candidates": [{"content": {"role": "model", "parts": [{"text": "やあ、きみ。"}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "totalTokenCount": 8},
                "modelVersion": "gemini-3-flash",
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class KeyPoolTest(unittest.TestCase):
    """キーの集め方・ローテーション・429 の待ち時間（ネットワーク不要）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / ".actor"

    def tearDown(self):
        self.tmp.cleanup()

    # ---- 集め方（後方互換と優先順位） ----
    def test_single_key_still_works(self):
        settings = load_settings(env={"ACTOR_DATA_DIR": str(self.data), "GEMINI_API_KEY": "fixture-ONE1234567890abcd"},
                                 env_file=self.root / "no-such.env")
        self.assertEqual(settings.api_key, "fixture-ONE1234567890abcd")
        self.assertEqual(settings.api_key_source, "env")
        self.assertEqual(settings.api_key_count, 1)

    def test_multiple_keys_from_env_and_dotenv(self):
        env_file = self.root / ".env"
        env_file.write_text("GEMINI_API_KEYS=fixture-ENV1_1234567890, fixture-ENV2_1234567890\n", encoding="utf-8")
        settings = load_settings(
            env={"ACTOR_DATA_DIR": str(self.data), "GEMINI_API_KEYS": "fixture-PROC1_1234567890;fixture-PROC2_1234567890"},
            env_file=env_file,
        )
        # 環境変数が .env より優先。区切りは , / ; / 空白のどれでも
        self.assertEqual(settings.api_keys, ["fixture-PROC1_1234567890", "fixture-PROC2_1234567890",
                                             "fixture-ENV1_1234567890", "fixture-ENV2_1234567890"])
        self.assertEqual(settings.api_key, "fixture-PROC1_1234567890")
        self.assertEqual(settings.api_key_count, 4)

    def test_cli_key_is_first_and_deduped(self):
        settings = load_settings(
            env={"ACTOR_DATA_DIR": str(self.data), "GEMINI_API_KEY": "fixture-ENVKEY_1234567890"},
            env_file=self.root / "no-such.env",
            api_key="fixture-CLIKEY_1234567890",
        )
        self.assertEqual(settings.api_key, "fixture-CLIKEY_1234567890")
        self.assertEqual(settings.api_key_source, "cli")
        self.assertEqual(settings.api_keys, ["fixture-CLIKEY_1234567890", "fixture-ENVKEY_1234567890"])
        self.assertEqual(settings.key_pool().current, "fixture-CLIKEY_1234567890")

    def test_saved_file_and_keys_dir(self):
        from actor.config import write_api_key, write_keys_dir

        settings = load_settings(env={"ACTOR_DATA_DIR": str(self.data)}, env_file=self.root / "no-such.env")
        write_api_key(settings, "fixture-FILEKEY_1234567890")
        paths = write_keys_dir(settings, ["fixture-DIRKEY1_1234567890", "fixture-DIRKEY2_1234567890"])
        self.assertEqual([p.name for p in paths], ["key-1.key", "key-2.key"])
        self.assertEqual(paths[0].read_text(encoding="utf-8").strip(), "fixture-DIRKEY1_1234567890")
        if os.name != "nt":
            self.assertEqual(oct(paths[0].stat().st_mode & 0o777), "0o600")

        reloaded = load_settings(env={"ACTOR_DATA_DIR": str(self.data)}, env_file=self.root / "no-such.env")
        self.assertEqual(reloaded.api_keys, ["fixture-FILEKEY_1234567890", "fixture-DIRKEY1_1234567890",
                                             "fixture-DIRKEY2_1234567890"])
        self.assertEqual(reloaded.api_key_source, "file")
        self.assertEqual(reloaded.key_pool().size, 3)

    def test_duplicate_keys_are_merged(self):
        from actor.keys import collect_entries

        entries = collect_entries(env={"GEMINI_API_KEYS": "fixture-SAME_1234567890,fixture-OTHER_1234567890"},
                                  dotenv={"GEMINI_API_KEY": "fixture-SAME_1234567890"})
        self.assertEqual([e.value for e in entries], ["fixture-SAME_1234567890", "fixture-OTHER_1234567890"])
        self.assertEqual(entries[0].source, "env")

    # ---- ローテーション ----
    def test_rotate_marks_limited_and_moves_on(self):
        from actor.keys import KeyEntry, KeyPool

        now = [1_000.0]
        pool = KeyPool([KeyEntry("fixture-AAAA_1234567890"), KeyEntry("fixture-BBBB_1234567890")],
                       cooldown=60.0, clock=lambda: now[0])
        self.assertEqual(pool.current, "fixture-AAAA_1234567890")
        self.assertTrue(pool.rotate(status=429, reason="429 Quota exceeded", retry_after=31))
        self.assertEqual(pool.current, "fixture-BBBB_1234567890")
        self.assertEqual(pool.ready_count, 1)
        self.assertGreater(pool.limited_for("fixture-AAAA_1234567890"), 0)
        # retryDelay（31s）より cooldown（60s）が長いのでそちらを採る
        self.assertGreater(pool.limited_for("fixture-AAAA_1234567890"), 55)
        # 2 本目も制限になったら全滅
        self.assertFalse(pool.rotate(status=429, reason="429"))
        self.assertEqual(pool.next_available_in() > 0, True)
        # 時間が経てば復帰する
        now[0] += 61
        self.assertTrue(pool.is_ready("fixture-AAAA_1234567890"))
        self.assertEqual(pool.ready_count, 2)

    def test_retry_after_longer_than_cooldown_is_honoured(self):
        from actor.keys import KeyEntry, KeyPool

        pool = KeyPool([KeyEntry("fixture-AAAA_1234567890"), KeyEntry("fixture-BBBB_1234567890")], cooldown=10.0)
        pool.rotate(key="fixture-AAAA_1234567890", status=429, retry_after=120)
        self.assertGreater(pool.limited_for("fixture-AAAA_1234567890"), 100)

    def test_mark_ok_clears_limit_and_counts(self):
        from actor.keys import KeyEntry, KeyPool

        pool = KeyPool([KeyEntry("fixture-AAAA_1234567890")], cooldown=60.0)
        pool.mark_limited("fixture-AAAA_1234567890", status=429, reason="429")
        self.assertFalse(pool.is_ready("fixture-AAAA_1234567890"))
        pool.mark_ok("fixture-AAAA_1234567890")
        self.assertTrue(pool.is_ready("fixture-AAAA_1234567890"))
        self.assertEqual(pool.status()[0]["uses"], 1)

    def test_state_file_records_only_fingerprints(self):
        from actor.keys import KeyEntry, KeyPool, fingerprint

        state = self.data / "key_state.json"
        pool = KeyPool([KeyEntry("fixture-SECRET_1234567890"), KeyEntry("fixture-OTHER_1234567890")],
                       cooldown=60.0, state_path=state)
        pool.rotate(key="fixture-SECRET_1234567890", status=429, reason="429 Quota exceeded for fixture-SECRET_1234567890")
        raw = state.read_text(encoding="utf-8")
        self.assertNotIn("fixture-SECRET_1234567890", raw)          # 生のキーは書かない
        self.assertIn(fingerprint("fixture-SECRET_1234567890"), raw)  # 指紋だけ
        # 別のプールでも「制限中」が引き継がれる
        again = KeyPool([KeyEntry("fixture-SECRET_1234567890"), KeyEntry("fixture-OTHER_1234567890")],
                        cooldown=60.0, state_path=state)
        self.assertEqual(again.current, "fixture-OTHER_1234567890")

    def test_exhaustion_message_is_helpful_and_masked(self):
        from actor.keys import KeyEntry, KeyPool

        keys = ["fixture-AAAA_1234567890", "fixture-BBBB_1234567890"]
        pool = KeyPool([KeyEntry(k, "env", "GEMINI_API_KEYS") for k in keys], cooldown=60.0)
        for key in keys:
            pool.mark_limited(key, status=429, reason=f"429 Quota exceeded for {key}")
        message = pool.exhaustion_message(status=429, last_error=ProviderError("429 Quota exceeded"))
        self.assertIn("すべての Gemini API キーが使えません", message)
        self.assertIn("fixtur…7890", message)
        self.assertIn("actor key add", message)
        for key in keys:
            self.assertNotIn(key, message)     # 生のキーは出さない

    def test_concurrent_rotation_is_consistent(self):
        """サーバーは複数スレッドから同じプールを使う（壊れないこと）。"""
        from actor.keys import KeyEntry, KeyPool

        keys = [f"fixture-THREAD{i}_1234567890" for i in range(3)]
        pool = KeyPool([KeyEntry(k) for k in keys], cooldown=60.0, state_path=self.data / "key_state.json")
        errors: list = []

        def worker(index: int):
            try:
                for _ in range(20):
                    key = pool.current or keys[0]
                    if index % 2:
                        pool.rotate(key=key, status=429, reason="429")
                    else:
                        pool.mark_ok(key)
            except Exception as exc:  # pragma: no cover - 失敗したら下で報告
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertIn(pool.current_index, range(3))
        self.assertEqual(len(pool.status()), 3)

    # ---- マスク / 伏せ字 ----
    def test_mask_and_redact(self):
        from actor.keys import mask_key, redact, split_keys

        self.assertEqual(mask_key("fixture-SyABCDEFGHIJ"), "fixtur…GHIJ")
        self.assertEqual(mask_key("short"), "＊＊＊＊＊")
        text = redact("Quota exceeded for fixture-SyABCDEFGHIJ please retry", ["fixture-SyABCDEFGHIJ"])
        self.assertNotIn("fixture-SyABCDEFGHIJ", text)
        self.assertIn("fixtur…GHIJ", text)
        self.assertEqual(split_keys("a,b  c;d"), ["a", "b", "c", "d"])


class KeyRotationTest(unittest.TestCase):
    """429 のときに自動で次のキーへ切り替わるか（偽 Gemini / 依存なし）。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeRotatingGemini)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        from actor.keys import KeyEntry

        self._key_entry = KeyEntry
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / ".actor"
        _FakeRotatingGemini.seen = []
        _FakeRotatingGemini.limited = set()

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, keys, **kw):
        settings = Settings(api_key=keys[0], api_keys=list(keys), base_url=self.base,
                            model="gemini-3-flash", data_dir=self.data,
                            skills_dirs=[self.root / "skills"], **kw)
        settings.key_entries = [self._key_entry(k, "env", f"K{i}") for i, k in enumerate(keys)]
        return settings

    def call(self, settings, text="やはり"):
        provider = get_provider(settings)
        self.assertEqual(provider.name, "gemini")
        return provider.generate([{"role": "user", "content": text}])

    def test_429_switches_to_next_key(self):
        keys = ["fixture-AAAA_1234567890", "fixture-BBBB_1234567890"]
        _FakeRotatingGemini.limited = {keys[0]}
        settings = self.make(keys)
        result = self.call(settings)
        self.assertEqual(result["text"], "やあ、きみ。")
        self.assertEqual(_FakeRotatingGemini.seen, keys)      # 1 本目 429 → 2 本目で成功
        pool = settings.key_pool()
        self.assertEqual(pool.current, keys[1])
        self.assertFalse(pool.is_ready(keys[0]))
        self.assertEqual(pool.rotations, 1)

    def test_ready_key_is_reused(self):
        keys = ["fixture-AAAA_1234567890", "fixture-BBBB_1234567890"]
        _FakeRotatingGemini.limited = {keys[0]}
        settings = self.make(keys)
        self.call(settings)
        self.call(settings, "もう一度")
        self.assertEqual(_FakeRotatingGemini.seen, [keys[0], keys[1], keys[1]])

    def test_all_keys_limited_raises_clear_error(self):
        keys = ["fixture-AAAA_1234567890", "fixture-BBBB_1234567890"]
        _FakeRotatingGemini.limited = set(keys)
        settings = self.make(keys)
        with self.assertRaises(ProviderError) as ctx:
            self.call(settings)
        message = str(ctx.exception)
        self.assertEqual(ctx.exception.status, 429)
        self.assertIn("すべての Gemini API キーが使えません", message)
        self.assertIn("actor key list", message)
        for key in keys:
            self.assertNotIn(key, message)      # API 本文に混ざったキーも伏せる

    def test_rotation_can_be_disabled(self):
        keys = ["fixture-AAAA_1234567890", "fixture-BBBB_1234567890"]
        _FakeRotatingGemini.limited = {keys[0]}
        settings = self.make(keys, key_rotate=False)
        with self.assertRaises(ProviderError) as ctx:
            self.call(settings)
        self.assertEqual(ctx.exception.status, 429)
        self.assertNotIn(keys[1], _FakeRotatingGemini.seen)   # 切り替えない（従来どおり）

    def test_stream_rotates_before_first_chunk(self):
        keys = ["fixture-AAAA_1234567890", "fixture-BBBB_1234567890"]
        _FakeRotatingGemini.limited = {keys[0]}
        settings = self.make(keys)
        events = list(get_provider(settings).stream([{"role": "user", "content": "やあ"}]))
        self.assertEqual("".join(e.get("text", "") for e in events if e["type"] == "text"), "やあ、きみ。")
        self.assertEqual(_FakeRotatingGemini.seen, keys)

    def test_stream_reports_exhaustion(self):
        keys = ["fixture-AAAA_1234567890", "fixture-BBBB_1234567890"]
        _FakeRotatingGemini.limited = set(keys)
        settings = self.make(keys)
        events = list(get_provider(settings).stream([{"role": "user", "content": "やあ"}]))
        errors = [e for e in events if e["type"] == "error"]
        self.assertTrue(errors)
        self.assertIn("すべての Gemini API キーが使えません", errors[0]["text"])

    def test_network_error_does_not_burn_keys(self):
        keys = ["fixture-AAAA_1234567890", "fixture-BBBB_1234567890"]
        settings = self.make(keys)
        settings.base_url = "http://127.0.0.1:9"
        with self.assertRaises(ProviderError):
            self.call(settings)
        pool = settings.key_pool()
        self.assertTrue(pool.is_ready(keys[0]))     # ネットワーク断でキーを潰さない
        self.assertEqual(pool.rotations, 0)

    def test_single_key_429_is_reported_once(self):
        keys = ["fixture-ONLY_1234567890"]
        settings = self.make(keys)
        _FakeRotatingGemini.limited = set(keys)
        with self.assertRaises(ProviderError) as ctx:
            self.call(settings)
        self.assertIn("すべての Gemini API キーが使えません（1 本すべて", str(ctx.exception))
        self.assertEqual(_FakeRotatingGemini.seen, [keys[0]])   # 無駄な再試行をしない

    def test_health_reports_key_pool(self):
        keys = ["fixture-AAAA_1234567890", "fixture-BBBB_1234567890"]
        settings = self.make(keys)
        health = get_provider(settings).health()
        self.assertEqual(health["keys"]["count"], 2)
        self.assertEqual(health["api_key_masked"], "fixtur…7890")
        self.assertNotIn(keys[0], json.dumps(health))


@unittest.skipUnless(_genai_available(), "google-genai 未導入（pip install google-genai で有効化）")
class GenAiKeyRotationTest(unittest.TestCase):
    """SDK 経由でも 429 でキーが切り替わるか。"""

    @classmethod
    def setUpClass(cls):
        _FakeRotatingGemini.seen = []
        _FakeRotatingGemini.limited = set()
        cls.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeRotatingGemini)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        # ローテーション状態（key_state.json）がテスト間で混ざらないように毎回作り直す
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / ".actor"

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, keys, **kw):
        from actor.keys import KeyEntry

        settings = Settings(api_key=keys[0], api_keys=list(keys), provider="genai",
                            base_url=self.base, model="gemini-3-flash", data_dir=self.data, **kw)
        settings.key_entries = [KeyEntry(k, "env", f"K{i}") for i, k in enumerate(keys)]
        return settings

    def test_generate_rotates_on_429(self):
        from actor.genai_provider import GenAiProvider

        keys = ["fixture-SDK1_1234567890", "fixture-SDK2_1234567890"]
        _FakeRotatingGemini.limited = {keys[0]}
        _FakeRotatingGemini.seen = []
        settings = self.make(keys)
        provider = GenAiProvider(settings)
        result = provider.generate([{"role": "user", "content": "やあ"}])
        self.assertEqual(result["text"], "やあ、きみ。")
        self.assertIn(keys[1], _FakeRotatingGemini.seen)
        self.assertEqual(settings.key_pool().current, keys[1])

    def test_generate_reports_exhaustion(self):
        from actor.genai_provider import GenAiProvider

        keys = ["fixture-SDK1_1234567890", "fixture-SDK2_1234567890"]
        _FakeRotatingGemini.limited = set(keys)
        _FakeRotatingGemini.seen = []
        settings = self.make(keys)
        provider = GenAiProvider(settings)
        with self.assertRaises(ProviderError) as ctx:
            provider.generate([{"role": "user", "content": "やあ"}])
        self.assertIn("すべての Gemini API キーが使えません", str(ctx.exception))
        for key in keys:
            self.assertNotIn(key, str(ctx.exception))


class KeyCliTest(unittest.TestCase):
    """``actor key add / list / rm / clear / check``（CLI）。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeRotatingGemini)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / ".actor"
        _FakeRotatingGemini.seen = []
        _FakeRotatingGemini.limited = set()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, args, stdin: str = "", extra_env: dict | None = None):
        import subprocess

        env = dict(os.environ, ACTOR_DATA_DIR=str(self.data))
        for name in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"):
            env.pop(name, None)
        env.update(extra_env or {})
        return subprocess.run([sys.executable, "-m", "actor", *args], input=stdin,
                              capture_output=True, text=True, env=env, timeout=60,
                              cwd=str(Path(__file__).resolve().parent.parent))

    def test_add_list_rm_clear(self):
        first = "fixture-CLIADD1_1234567890"
        second = "fixture-CLIADD2_1234567890"

        added = self.run_cli(["key", "add", "--stdin"], stdin=f"{first}\n")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("追加しました", added.stdout)
        self.assertIn(".actor/keys/key-1.key", added.stdout.replace(str(self.data), ".actor"))
        self.assertNotIn(first, added.stdout)          # 生のキーは出さない

        added = self.run_cli(["key", "add", "--stdin"], stdin=f"{second}\n")
        self.assertEqual(added.returncode, 0, added.stderr)

        listing = self.run_cli(["key", "list"])
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("API キー 2 本", listing.stdout)
        self.assertIn("fixtur…7890", listing.stdout)
        self.assertNotIn(first, listing.stdout)

        payload = json.loads(self.run_cli(["key", "list", "--json"]).stdout)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["ready"], 2)
        self.assertEqual([item["label"] for item in payload["keys"]], ["key-1.key", "key-2.key"])
        self.assertNotIn(first, json.dumps(payload))

        # 同じキーは増やさない
        duplicate = self.run_cli(["key", "add", "--stdin"], stdin=f"{first}\n")
        self.assertEqual(duplicate.returncode, 0, duplicate.stderr)
        self.assertIn("既に登録されています", duplicate.stdout)
        self.assertEqual(json.loads(self.run_cli(["key", "list", "--json"]).stdout)["count"], 2)

        # 短すぎるキーは拒否
        too_short = self.run_cli(["key", "add", "--stdin"], stdin="shorty\n")
        self.assertEqual(too_short.returncode, 2)
        self.assertIn("短すぎ", too_short.stdout)

        # 1 本だけ消す
        removed = self.run_cli(["key", "rm", "0"])
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn("削除しました", removed.stdout)
        self.assertFalse((self.data / "keys" / "key-1.key").exists())
        self.assertEqual(json.loads(self.run_cli(["key", "list", "--json"]).stdout)["count"], 1)

        # 範囲外の番号はエラー
        self.assertEqual(self.run_cli(["key", "rm", "5"]).returncode, 2)

        # clear で全部（保存ファイルと keys ディレクトリ）
        self.run_cli(["key", "set", "--stdin"], stdin="fixture-CLISET1_1234567890\n")
        cleared = self.run_cli(["key", "clear"])
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertIn("削除しました", cleared.stdout)
        self.assertFalse((self.data / "api_key").exists())
        self.assertFalse((self.data / "keys" / "key-2.key").exists())
        self.assertIn("未設定", self.run_cli(["key", "show"]).stdout)

    def test_env_keys_are_listed_and_not_deletable(self):
        env = {"GEMINI_API_KEYS": "fixture-ENVONE_1234567890,fixture-ENVTWO_1234567890"}
        listing = self.run_cli(["key", "list", "--json"], extra_env=env)
        payload = json.loads(listing.stdout)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["keys"][0]["label"], "GEMINI_API_KEYS")
        self.assertEqual(payload["keys"][0]["source"], "env")
        self.assertTrue(payload["keys"][0]["active"])

        shown = self.run_cli(["key", "show"], extra_env=env)
        self.assertIn("fixtur…7890", shown.stdout)
        self.assertIn("キー 2 本", shown.stdout)

        # 環境変数のキーは削除できない（どこを直すか案内する）
        refused = self.run_cli(["key", "rm", "0"], extra_env=env)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("環境変数", refused.stdout)

    def test_check_reports_each_key(self):
        keys = ["fixture-CHK1_1234567890", "fixture-CHK2_1234567890"]
        checked = self.run_cli(
            ["key", "check", "--base-url", self.base],
            extra_env={"GEMINI_API_KEYS": ",".join(keys)},
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("API キーの接続確認", checked.stdout)
        self.assertIn("2/2 本が応答しました", checked.stdout)
        self.assertNotIn(keys[0], checked.stdout)

        # 1 本だけ 429 のときは、そのキーだけ✗になる
        _FakeRotatingGemini.limited = {keys[0]}
        checked = self.run_cli(
            ["key", "check", "--base-url", self.base, "--json"],
            extra_env={"GEMINI_API_KEYS": ",".join(keys)},
        )
        payload = json.loads(checked.stdout)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual([item["ok"] for item in payload["results"]], [False, True])

        # どこにも繋がらないときは終了コード 1
        unreachable = self.run_cli(
            ["key", "check", "--base-url", "http://127.0.0.1:9"],
            extra_env={"GEMINI_API_KEYS": keys[0]},
        )
        self.assertEqual(unreachable.returncode, 1)

    def test_unknown_action_is_reported(self):
        result = self.run_cli(["key", "lst"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("不明な操作", result.stdout)


class KeyRotationChatTest(unittest.TestCase):
    """会話まで通して 429 を跨げるか（CLI / Web と同じ経路）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _FakeRotatingGemini.seen = []
        _FakeRotatingGemini.limited = set()
        self.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeRotatingGemini)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        keys = ["fixture-CHAT1_1234567890", "fixture-CHAT2_1234567890"]
        from actor.keys import KeyEntry

        self.keys = keys
        self.settings = Settings(api_key=keys[0], api_keys=list(keys), base_url=base,
                                 model="gemini-3-flash", skills_dirs=[self.root / "skills"],
                                 data_dir=self.root / ".actor", tools=[])
        self.settings.key_entries = [KeyEntry(k, "env", f"K{i}") for i, k in enumerate(keys)]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_chat_falls_over_to_second_key(self):
        from actor.chat import ChatEngine

        _FakeRotatingGemini.limited = {self.keys[0]}
        engine = ChatEngine(self.settings)
        session = engine.store.create()
        events = list(engine.turn(session, "やあ", auto=False, stream=True))
        text = "".join(e.get("text", "") for e in events if e["type"] == "text")
        self.assertEqual(text, "やあ、きみ。")
        self.assertIsNone(next(e for e in events if e["type"] == "done")["error"])
        self.assertEqual(self.settings.key_pool().current, self.keys[1])

    def test_chat_reports_exhaustion_without_crashing(self):
        from actor.chat import ChatEngine

        _FakeRotatingGemini.limited = set(self.keys)
        engine = ChatEngine(self.settings)
        session = engine.store.create()
        events = list(engine.turn(session, "やあ", auto=False, stream=False))
        done = next(e for e in events if e["type"] == "done")
        self.assertIn("すべての Gemini API キーが使えません", done["error"])
        self.assertNotIn(self.keys[0], done["error"])



# ----------------------------------------------------------------------
# .env への追記 / 削除（``actor key add --env`` / ``key rm``）
# ----------------------------------------------------------------------
class EnvFileKeyTest(unittest.TestCase):
    """``.env`` を壊さずにキーを足す・消す（直接 API を呼ぶ）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / ".actor"
        self.env_path = self.root / ".env"
        self.env_path.write_text(
            "# Actor の設定\nexport GEMINI_API_KEYS=fixture-OLD1_1234567890\n"
            "GEMINI_API_KEY=fixture-SINGLE_1234567890\nACTOR_MODEL=gemini-3-flash\n",
            encoding="utf-8",
        )
        self.settings = load_settings(env={"ACTOR_DATA_DIR": str(self.data)}, env_file=self.env_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_keeps_comments_and_order(self):
        from actor.config import append_key_to_env_file

        path = append_key_to_env_file(self.settings, "fixture-NEW_1234567890")
        self.assertEqual(path, self.env_path)
        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("export GEMINI_API_KEYS=fixture-OLD1_1234567890,fixture-NEW_1234567890", text)
        self.assertIn("# Actor の設定", text)                 # コメントは残る
        self.assertIn("ACTOR_MODEL=gemini-3-flash", text)     # 他の変数も残る
        self.assertNotIn("fixture-NEW_1234567890\nfixture-NEW", text)  # 追記は 1 回だけ
        if os.name != "nt":
            self.assertEqual(oct(self.env_path.stat().st_mode & 0o777), "0o600")

        # 同じキーをもう一度足しても増えない
        append_key_to_env_file(self.settings, "fixture-NEW_1234567890")
        self.assertEqual(self.env_path.read_text(encoding="utf-8").count("fixture-NEW_1234567890"), 1)

        # 読み直すと 3 本になる（1 行目が最優先）
        refreshed = load_settings(env={"ACTOR_DATA_DIR": str(self.data)}, env_file=self.env_path)
        self.assertEqual(refreshed.api_keys,
                         ["fixture-OLD1_1234567890", "fixture-NEW_1234567890", "fixture-SINGLE_1234567890"])

    def test_append_creates_env_file_when_missing(self):
        from actor.config import append_key_to_env_file

        missing = self.root / "sub" / ".env"
        settings = load_settings(env={"ACTOR_DATA_DIR": str(self.data)}, env_file=missing)
        path = append_key_to_env_file(settings, "fixture-FRESH_1234567890")
        self.assertEqual(path, missing)
        self.assertEqual(missing.read_text(encoding="utf-8").strip(),
                         "GEMINI_API_KEYS=fixture-FRESH_1234567890")

    def test_append_prefers_existing_single_key_variable(self):
        from actor.config import append_key_to_env_file

        self.env_path.write_text("GEMINI_API_KEY=fixture-SINGLE_1234567890\n", encoding="utf-8")
        settings = load_settings(env={"ACTOR_DATA_DIR": str(self.data)}, env_file=self.env_path)
        append_key_to_env_file(settings, "fixture-EXTRA_1234567890", var="GEMINI_API_KEY")
        text = self.env_path.read_text(encoding="utf-8")
        self.assertEqual(text.strip(), "GEMINI_API_KEY=fixture-SINGLE_1234567890,fixture-EXTRA_1234567890")
        self.assertEqual(len(load_settings(env={"ACTOR_DATA_DIR": str(self.data)},
                                           env_file=self.env_path).api_keys), 2)

    def test_remove_key_from_env_file(self):
        from actor.config import remove_key_from_env_file

        self.assertTrue(remove_key_from_env_file(self.settings, "fixture-OLD1_1234567890"))
        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("export GEMINI_API_KEYS=", text)          # 行は残す（空にする）
        self.assertNotIn("fixture-OLD1_1234567890", text)
        self.assertIn("GEMINI_API_KEY=fixture-SINGLE_1234567890", text)
        # 残りは 1 本だけ
        refreshed = load_settings(env={"ACTOR_DATA_DIR": str(self.data)}, env_file=self.env_path)
        self.assertEqual(refreshed.api_keys, ["fixture-SINGLE_1234567890"])

        # 単数キーの行からも消せる
        self.assertTrue(remove_key_from_env_file(self.settings, "fixture-SINGLE_1234567890"))
        self.assertNotIn("fixture-SINGLE_1234567890", self.env_path.read_text(encoding="utf-8"))
        # 無いキーなら False
        self.assertFalse(remove_key_from_env_file(self.settings, "fixture-NOPE_1234567890"))

    def test_removed_env_key_is_reported_with_path(self):
        """``key list`` の items に「どこに書いてあるか」が出る（rm の案内用）。"""
        items = self.settings.key_pool().status()
        dotenv = [item for item in items if item["source"] == ".env"]
        self.assertEqual([item["path"] for item in dotenv], [str(self.env_path)] * len(dotenv))


class KeyStatsTest(unittest.TestCase):
    """429 の統計（どのキーがよく切れるか）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / ".actor"

    def tearDown(self):
        self.tmp.cleanup()

    def make_pool(self, keys=("fixture-AAAA_1234567890", "fixture-BBBB_1234567890"), **kw):
        from actor.keys import KeyEntry, KeyPool

        return KeyPool([KeyEntry(k, "env", "GEMINI_API_KEYS") for k in keys],
                       state_path=self.data / "key_state.json", **kw)

    def test_counts_survive_reload(self):
        pool = self.make_pool(cooldown=0.0)
        pool.mark_ok("fixture-AAAA_1234567890")
        pool.rotate(key="fixture-AAAA_1234567890", status=429, reason="429 Quota exceeded")
        pool.rotate(key="fixture-BBBB_1234567890", status=429, reason="429 Quota exceeded")

        again = self.make_pool(cooldown=0.0)
        stats = again.stats()
        self.assertEqual(stats["totals"], {"requests": 1, "limits": 2, "rotations": 2})
        self.assertEqual([item["limits"] for item in stats["keys"]], [1, 1])
        self.assertGreater(stats["updated"], 0)

    def test_worst_key_is_the_most_limited(self):
        pool = self.make_pool(keys=("fixture-AAAA_1234567890", "fixture-BBBB_1234567890"), cooldown=0.0)
        for _ in range(3):
            pool.mark_ok("fixture-BBBB_1234567890")
        pool.mark_limited("fixture-AAAA_1234567890", status=429, reason="429")
        stats = pool.stats()
        self.assertEqual(stats["worst"]["masked"], "fixtur…7890")
        self.assertEqual(stats["worst"]["limits"], 1)
        self.assertEqual(stats["worst"]["attempts"], 1)
        other = next(item for item in stats["keys"] if item["index"] == 1)
        self.assertEqual((other["limits"], other["uses"], other["attempts"]), (0, 3, 3))
        self.assertEqual(other["limit_rate"], 0.0)

    def test_stats_never_leak_keys(self):
        keys = ["fixture-SECRET1_1234567890", "fixture-SECRET2_1234567890"]
        pool = self.make_pool(keys=keys, cooldown=0.0)
        for key in keys:
            pool.mark_limited(key, status=429, reason=f"429 Quota exceeded for {key}")
        dump = json.dumps(pool.stats(), ensure_ascii=False)
        for key in keys:
            self.assertNotIn(key, dump)
        self.assertIn("fixtur…7890", dump)
        self.assertNotIn(keys[0], (self.data / "key_state.json").read_text(encoding="utf-8"))

    def test_reset_stats_clears_counts_and_limits(self):
        pool = self.make_pool(cooldown=60.0)
        pool.mark_ok("fixture-AAAA_1234567890")
        pool.rotate(key="fixture-AAAA_1234567890", status=429, reason="429")
        pool.reset_stats()
        stats = pool.stats()
        self.assertEqual(stats["totals"], {"requests": 0, "limits": 0, "rotations": 0})
        self.assertEqual([item["limits"] for item in stats["keys"]], [0, 0])
        self.assertEqual(stats["ready"], 2)          # 「制限中」の印も消える
        self.assertTrue(pool.is_ready("fixture-AAAA_1234567890"))

    def test_stale_records_are_counted(self):
        pool = self.make_pool(cooldown=0.0)
        pool.mark_limited("fixture-GONE_1234567890", status=429, reason="429")
        stats = pool.stats()
        self.assertEqual(stats["stale_records"], 1)   # 一覧に無いキーの記録
        self.assertEqual(stats["count"], 2)


class KeyStatsCliTest(unittest.TestCase):
    """``actor key stats`` と ``key add --env`` / ``key rm``（CLI）。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeRotatingGemini)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / ".actor"
        _FakeRotatingGemini.seen = []
        _FakeRotatingGemini.limited = set()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, args, stdin: str = "", extra_env: dict | None = None):
        import subprocess

        env = dict(os.environ, ACTOR_DATA_DIR=str(self.data))
        for name in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"):
            env.pop(name, None)
        env.update(extra_env or {})
        return subprocess.run([sys.executable, "-m", "actor", *args], input=stdin,
                              capture_output=True, text=True, env=env, timeout=60,
                              cwd=str(Path(__file__).resolve().parent.parent))

    def test_add_env_appends_and_lists(self):
        env_path = self.root / ".env"
        env_path.write_text("# メモ\nexport GEMINI_API_KEYS=fixture-FIRST_1234567890\n", encoding="utf-8")
        key = "fixture-SECOND_1234567890"

        added = self.run_cli(["key", "add", "--env", "--stdin", "--env-file", str(env_path)], stdin=f"{key}\n")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("追加しました", added.stdout)
        self.assertNotIn(key, added.stdout)                     # 生のキーは出さない
        text = env_path.read_text(encoding="utf-8")
        self.assertIn(f"export GEMINI_API_KEYS=fixture-FIRST_1234567890,{key}", text)
        self.assertIn("# メモ", text)

        listing = json.loads(self.run_cli(["key", "list", "--json", "--env-file", str(env_path)]).stdout)
        self.assertEqual(listing["count"], 2)
        self.assertEqual([item["source"] for item in listing["keys"]], [".env", ".env"])
        self.assertEqual(listing["keys"][0]["path"], str(env_path))

        # rm で .env から消せる（番号指定）
        removed = self.run_cli(["key", "rm", "1", "--env-file", str(env_path)])
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn(".env", removed.stdout)
        self.assertNotIn(key, env_path.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.run_cli(["key", "list", "--json", "--env-file", str(env_path)]).stdout)["count"], 1)

    def test_stats_after_429(self):
        keys = ["fixture-STAT1_1234567890", "fixture-STAT2_1234567890"]
        _FakeRotatingGemini.limited = {keys[0]}
        env = {"GEMINI_API_KEYS": ",".join(keys), "ACTOR_BASE_URL": self.base}
        turn = self.run_cli(["--no-banner", "やあ"], extra_env=env)
        self.assertEqual(turn.returncode, 0, turn.stderr)

        stats = self.run_cli(["key", "stats"], extra_env=env)
        self.assertEqual(stats.returncode, 0, stats.stderr)
        self.assertIn("API キーの利用統計", stats.stdout)
        self.assertIn("429 で切れた 1 回", stats.stdout)
        self.assertIn("よく切れているキー", stats.stdout)
        self.assertIn("fixtur…7890", stats.stdout)
        for key in keys:
            self.assertNotIn(key, stats.stdout)

        payload = json.loads(self.run_cli(["key", "stats", "--json"], extra_env=env).stdout)
        self.assertEqual(payload["totals"]["requests"], 1)
        self.assertEqual(payload["totals"]["limits"], 1)
        self.assertEqual(payload["worst"]["limits"], 1)
        self.assertGreater(payload["worst"]["limit_rate"], 0.5)
        self.assertNotIn(keys[0], json.dumps(payload))

        # doctor / key list にも数字が出る
        doctor = self.run_cli(["doctor"], extra_env=env)
        self.assertIn("キーの統計", doctor.stdout)
        self.assertIn("429 で切れた 1 回", doctor.stdout)
        listing = self.run_cli(["key", "list"], extra_env=env)
        self.assertIn("429 で切り替えた回数: 1", listing.stdout)

        # リセットすると消える
        reset = self.run_cli(["key", "stats", "--reset"], extra_env=env)
        self.assertEqual(reset.returncode, 0, reset.stderr)
        self.assertIn("リセットしました", reset.stdout)
        after = json.loads(self.run_cli(["key", "stats", "--json"], extra_env=env).stdout)
        self.assertEqual(after["totals"]["limits"], 0)
        self.assertEqual(after["keys"][0]["limits"], 0)

    def test_stats_without_keys_is_safe(self):
        result = self.run_cli(["key", "stats"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("未設定", result.stdout)



# ----------------------------------------------------------------------
# モデルのフォールバック（429 が続いたら別モデルへ逃がす）
# ----------------------------------------------------------------------
class _FakeModelGemini(BaseHTTPRequestHandler):
    """モデル単位で 429（ResourceExhausted）を返す偽 Gemini。"""

    protocol_version = "HTTP/1.1"
    limited_models: set = set()
    seen: list = []          # (モデル, キー) の順番

    def log_message(self, *args):  # noqa: A003
        pass

    def _model(self) -> str:
        return self.path.split("?")[0].split("/")[-1].split(":")[0]

    def _send(self, code: int, payload: dict, ctype: str = "application/json") -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _limited(self, model: str) -> None:
        self._send(429, {"error": {
            "code": 429, "status": "RESOURCE_EXHAUSTED",
            "message": f"Quota exceeded for {model} (generateRequestsPerDayPerProjectPerModel)",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "13s"}],
        }})

    def do_GET(self):  # noqa: N802
        _FakeModelGemini.seen.append((self._model(), (self.headers.get("x-goog-api-key") or "").strip()))
        if self.path.endswith("/models"):
            self._send(200, {"models": [
                {"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]},
            ]})
            return
        self._limited(self._model())

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        model = self._model()
        _FakeModelGemini.seen.append((model, (self.headers.get("x-goog-api-key") or "").strip()))
        if model in _FakeModelGemini.limited_models:
            self._limited(model)
            return
        if "streamGenerateContent" in self.path:
            chunks = [
                {"candidates": [{"content": {"role": "model", "parts": [{"text": f"[{model}] "}]}}]},
                {"candidates": [{"content": {"role": "model", "parts": [{"text": "回答です。"}]}}],
                 "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 5, "totalTokenCount": 8}},
            ]
            body = b"".join(f"data: {json.dumps(c, ensure_ascii=False)}\r\n\r\n".encode("utf-8") for c in chunks)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send(200, {
            "candidates": [{"content": {"role": "model", "parts": [{"text": f"[{model}] 回答です。"}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 5, "totalTokenCount": 8},
            "modelVersion": model,
        })


class ModelFallbackTest(unittest.TestCase):
    """主モデルが 429 のとき、別モデルへ自動で逃げるか（偽 Gemini / 依存なし）。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeModelGemini)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        from actor.keys import KeyEntry

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _FakeModelGemini.seen = []
        _FakeModelGemini.limited_models = set()
        keys = ["fixture-FB1_1234567890", "fixture-FB2_1234567890"]
        self.keys = keys
        self.settings = Settings(api_key=keys[0], api_keys=list(keys), base_url=self.base,
                                 model="gemini-3.8-flash", data_dir=self.root / ".actor",
                                 model_fallbacks=["gemini-3.5-flash", "gemini-2.5-flash"],
                                 skills_dirs=[self.root / "skills"], tools=[])
        self.settings.key_entries = [KeyEntry(k, "env", "GEMINI_API_KEYS") for k in keys]

    def tearDown(self):
        self.tmp.cleanup()

    def _models_asked(self) -> list[str]:
        return [model for model, _key in _FakeModelGemini.seen]

    def test_default_fallbacks_come_from_settings(self):
        settings = load_settings(env={"GEMINI_API_KEY": "fixture-X_1234567890"},
                                 env_file=self.root / "no-such.env")
        self.assertEqual(settings.model_fallbacks, ["gemini-3.5-flash", "gemini-2.5-flash"])
        disabled = load_settings(env={"GEMINI_API_KEY": "fixture-X_1234567890", "ACTOR_MODEL_FALLBACKS": ""},
                                 env_file=self.root / "no-such.env")
        self.assertEqual(disabled.model_fallbacks, [])
        custom = load_settings(env={"GEMINI_API_KEY": "fixture-X_1234567890",
                                    "ACTOR_MODEL_FALLBACKS": "gemini-x, gemini-y"},
                               env_file=self.root / "no-such.env")
        self.assertEqual(custom.model_fallbacks, ["gemini-x", "gemini-y"])

    def test_generate_falls_back_to_next_model(self):
        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        provider = get_provider(self.settings)
        result = provider.generate([{"role": "user", "content": "やあ"}])
        self.assertEqual(result["model"], "gemini-3.5-flash")
        self.assertEqual(result["text"], "[gemini-3.5-flash] 回答です。")
        self.assertEqual(result["requested_model"], "gemini-3.8-flash")
        self.assertTrue(any(n["kind"] == "model_fallback" for n in result["notices"]))
        # 主モデルは全キーを試してから逃げ道へ（キーはプロジェクトが違えば枠も別なので）
        self.assertEqual(self._models_asked(), ["gemini-3.8-flash", "gemini-3.8-flash", "gemini-3.5-flash"])

    def test_fallback_is_per_model_not_per_key(self):
        """枠は「モデル × プロジェクト」単位なので、キーを無駄に切り替えない。"""
        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        provider = get_provider(self.settings)
        result = provider.generate([{"role": "user", "content": "やあ"}])
        self.assertEqual(result["model"], "gemini-3.5-flash")
        fallback_key = _FakeModelGemini.seen[-1][1]
        pool = self.settings.key_pool()
        for key in self.keys:
            self.assertFalse(pool.is_ready(key, "gemini-3.8-flash"))    # 主モデルは全キー制限中
            self.assertTrue(pool.is_ready(key, "gemini-3.5-flash"))     # 逃げ道なら同じキーで使える
        self.assertTrue(pool.is_ready(fallback_key, "gemini-3.5-flash"))
        self.assertEqual(pool.summary("gemini-3.5-flash"), "2/2 本が使用可能")
        self.assertEqual(pool.summary("gemini-3.8-flash"), "0/2 本が使用可能")

    def test_next_turn_skips_exhausted_model(self):
        """制限中と分かっている主モデルは、次のターンでは最初から試さない。"""
        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        provider = get_provider(self.settings)
        provider.generate([{"role": "user", "content": "1回目"}])
        _FakeModelGemini.seen = []
        result = provider.generate([{"role": "user", "content": "2回目"}])
        self.assertEqual(self._models_asked(), ["gemini-3.5-flash"])          # 無駄打ちしない
        self.assertEqual(result["model"], "gemini-3.5-flash")
        notices = [n for n in result["notices"] if n.get("to") == "gemini-3.5-flash"]
        self.assertTrue(notices)
        self.assertIn("全キーが 429", notices[0]["text"])

    def test_stream_notifies_and_uses_new_model(self):
        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        events = list(get_provider(self.settings).stream([{"role": "user", "content": "やあ"}]))
        kinds = [e["type"] for e in events]
        self.assertIn("notice", kinds)
        notice = next(e for e in events if e["type"] == "notice")
        self.assertEqual(notice["to"], "gemini-3.5-flash")
        text = "".join(e.get("text", "") for e in events if e["type"] == "text")
        self.assertEqual(text, "[gemini-3.5-flash] 回答です。")
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["model"], "gemini-3.5-flash")
        self.assertEqual(done["requested_model"], "gemini-3.8-flash")

    def test_all_models_limited_reports_every_model(self):
        _FakeModelGemini.limited_models = {"gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash"}
        provider = get_provider(self.settings)
        with self.assertRaises(ProviderError) as ctx:
            provider.generate([{"role": "user", "content": "やあ"}])
        message = str(ctx.exception)
        self.assertEqual(ctx.exception.status, 429)
        self.assertIn("試したモデル: gemini-3.8-flash / gemini-3.5-flash / gemini-2.5-flash", message)
        self.assertIn("ACTOR_MODEL_FALLBACKS", message)          # 対処が書いてある
        self.assertIn("actor key sources", message)              # キーの入手先も案内する
        for key in self.keys:
            self.assertNotIn(key, message)

    def test_fallback_can_be_disabled(self):
        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        self.settings.model_fallbacks = []
        self.settings._key_pool = None
        provider = get_provider(self.settings)
        with self.assertRaises(ProviderError) as ctx:
            provider.generate([{"role": "user", "content": "やあ"}])
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(set(self._models_asked()), {"gemini-3.8-flash"})   # 逃げ道は使わない

    def test_explicit_model_override_is_tried_first(self):
        """スキル / --model で指定されたモデルは、それとして先に試す。"""
        _FakeModelGemini.limited_models = set()
        result = get_provider(self.settings).generate([{"role": "user", "content": "やあ"}],
                                                     model="gemini-2.5-flash")
        self.assertEqual(result["model"], "gemini-2.5-flash")
        self.assertEqual(self._models_asked(), ["gemini-2.5-flash"])

    def test_chat_engine_reports_used_model(self):
        from actor.chat import ChatEngine

        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        engine = ChatEngine(self.settings)
        session = engine.store.create()
        events = list(engine.turn(session, "やあ", auto=False, stream=True))
        done = next(e for e in events if e["type"] == "done")
        self.assertIsNone(done["error"])
        self.assertEqual(done["model"], "gemini-3.5-flash")            # 実際に使ったモデル
        self.assertEqual(done["requested_model"], "gemini-3.8-flash")
        self.assertEqual(done["notices"][0]["kind"], "model_fallback")
        self.assertEqual(session.messages[-1].model, "gemini-3.5-flash")

    def test_stats_show_limits_by_model(self):
        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        provider = get_provider(self.settings)
        provider.generate([{"role": "user", "content": "やあ"}])
        stats = self.settings.key_pool().stats()
        self.assertEqual(stats["by_model"]["gemini-3.8-flash"]["limits"], 2)   # 2 本とも 429
        self.assertEqual(stats["by_model"]["gemini-3.5-flash"]["requests"], 1)
        self.assertEqual(stats["ready_by_model"]["gemini-3.5-flash"], 2)
        self.assertEqual(stats["ready_by_model"]["gemini-3.8-flash"], 0)
        self.assertEqual(stats["models"], ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash"])
        item = stats["keys"][0]
        self.assertEqual(item["limits_by_model"], {"gemini-3.8-flash": 1})
        self.assertEqual(item["worst_model"], "gemini-3.8-flash")
        self.assertNotIn(self.keys[0], json.dumps(stats))

    def test_state_file_v1_is_migrated_as_wildcard(self):
        """古い状態ファイル（バージョン1）は「モデルを問わない制限」として引き継ぐ。"""
        state = self.root / ".actor" / "key_state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        from actor.keys import fingerprint

        state.write_text(json.dumps({
            "version": 1,
            "updated": 1.0,
            "keys": {fingerprint(self.keys[0]): {
                "limited_until": time.time() + 120, "status": 429, "reason": "429 Quota exceeded",
                "uses": 3, "errors": 1, "limits": 1, "last_used": time.time(),
            }},
        }), encoding="utf-8")
        pool = self.settings.key_pool()
        self.assertFalse(pool.is_ready(self.keys[0], "どのモデルでも"))
        self.assertTrue(pool.is_ready(self.keys[1], "どのモデルでも"))
        self.assertEqual(pool.stats()["keys"][0]["limits"], 1)   # 回数も引き継ぐ


@unittest.skipUnless(_genai_available(), "google-genai 未導入（pip install google-genai で有効化）")
class GenAiModelFallbackTest(unittest.TestCase):
    """SDK 経由でもモデルのフォールバックが効くか。"""

    @classmethod
    def setUpClass(cls):
        _FakeModelGemini.seen = []
        _FakeModelGemini.limited_models = set()
        cls.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeModelGemini)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        from actor.keys import KeyEntry

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _FakeModelGemini.seen = []
        self.settings = Settings(api_key="fixture-SDKFB_1234567890", provider="genai", base_url=self.base,
                                 model="gemini-3.8-flash", data_dir=self.root / ".actor",
                                 model_fallbacks=["gemini-3.5-flash"])
        self.settings.key_entries = [KeyEntry("fixture-SDKFB_1234567890", "env", "GEMINI_API_KEY")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_sdk_generate_falls_back(self):
        from actor.genai_provider import GenAiProvider

        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        result = GenAiProvider(self.settings).generate([{"role": "user", "content": "やあ"}])
        self.assertEqual(result["model"], "gemini-3.5-flash")
        self.assertEqual(result["text"], "[gemini-3.5-flash] 回答です。")
        self.assertTrue(any(n["kind"] == "model_fallback" for n in result["notices"]))

    def test_sdk_stream_falls_back_before_first_chunk(self):
        from actor.genai_provider import GenAiProvider

        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        events = list(GenAiProvider(self.settings).stream([{"role": "user", "content": "やあ"}]))
        notices = [e for e in events if e["type"] == "notice"]
        self.assertTrue(notices)
        self.assertEqual(notices[0]["to"], "gemini-3.5-flash")
        text = "".join(e.get("text", "") for e in events if e["type"] == "text")
        self.assertEqual(text, "[gemini-3.5-flash] 回答です。")
        self.assertEqual(next(e for e in events if e["type"] == "done")["model"], "gemini-3.5-flash")


class VertexExpressTest(unittest.TestCase):
    """Vertex AI エクスプレスモード（API キー + aiplatform）でも動くか。"""

    def test_url_shape_for_express_mode(self):
        from actor.providers import GeminiProvider

        settings = load_settings(env={"GEMINI_API_KEY": "fixture-EXPRESS_1234567890",
                                      "ACTOR_BASE_URL": "https://aiplatform.googleapis.com"},
                                 env_file="/nonexistent")
        self.assertEqual(settings.api_version, "v1")
        self.assertEqual(settings.model_path, "publishers/google/models/{model}")
        provider = GeminiProvider(settings)
        url = provider._url("generateContent", model="gemini-3.8-flash")
        self.assertEqual(url, "https://aiplatform.googleapis.com/v1/publishers/google/models/"
                              "gemini-3.8-flash:generateContent")
        stream_url = provider._url("streamGenerateContent", stream=True, model="gemini-2.5-flash")
        self.assertIn(":streamGenerateContent?alt=sse", stream_url)

    def test_default_shape_stays_v1beta(self):
        from actor.providers import GeminiProvider

        settings = load_settings(env={"GEMINI_API_KEY": "fixture-NORMAL_1234567890"},
                                 env_file="/nonexistent")
        provider = GeminiProvider(settings)
        self.assertTrue(provider._url("generateContent", model="gemini-3-flash").endswith(
            "/v1beta/models/gemini-3-flash:generateContent"))

    def test_explicit_api_shape_can_be_overridden(self):
        settings = load_settings(env={"GEMINI_API_KEY": "fixture-X_1234567890",
                                      "ACTOR_API_VERSION": "v1",
                                      "ACTOR_MODEL_PATH": "openai/models/{model}"},
                                 env_file="/nonexistent")
        self.assertEqual((settings.api_version, settings.model_path), ("v1", "openai/models/{model}"))

    def test_request_goes_to_publishers_path(self):
        """偽サーバーで publishers パスが組み立てられているか確認する。"""

        class _FakeExpress(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            paths: list = []

            def log_message(self, *args):  # noqa: A003
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                _FakeExpress.paths.append(self.path)
                body = json.dumps({
                    "candidates": [{"content": {"role": "model", "parts": [{"text": "express ok"}]}}],
                    "modelVersion": "gemini-3.8-flash",
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = HTTPServerProc(("127.0.0.1", 0), _FakeExpress)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            settings = Settings(api_key="fixture-EXPRESS_1234567890", base_url=base,
                                model="gemini-3.8-flash", api_version="v1",
                                model_path="publishers/google/models/{model}", tools=[])
            result = get_provider(settings).generate([{"role": "user", "content": "やあ"}])
            self.assertEqual(result["text"], "express ok")
            self.assertEqual(_FakeExpress.paths[0],
                             "/v1/publishers/google/models/gemini-3.8-flash:generateContent")
        finally:
            httpd.shutdown()
            httpd.server_close()


class KeySourcesTest(unittest.TestCase):
    """``actor key sources``（キーを無料で用意できる場所）。"""

    def run_cli(self, args, extra_env: dict | None = None):
        import subprocess

        tmp = tempfile.TemporaryDirectory()
        env = dict(os.environ, ACTOR_DATA_DIR=str(Path(tmp.name) / ".actor"))
        for name in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"):
            env.pop(name, None)
        env.update(extra_env or {})
        result = subprocess.run([sys.executable, "-m", "actor", *args], capture_output=True, text=True,
                                env=env, timeout=60, cwd=str(Path(__file__).resolve().parent.parent))
        tmp.cleanup()
        return result

    def test_lists_free_places_with_caution(self):
        from actor.keys import KEY_SOURCES_CAUTION

        result = self.run_cli(["key", "sources"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("https://aistudio.google.com/apikey", result.stdout)
        self.assertIn("aiplatform.googleapis.com", result.stdout)
        self.assertIn("規約違反", result.stdout)

        payload = json.loads(self.run_cli(["key", "sources", "--json"]).stdout)
        self.assertTrue(any("aistudio" in item["url"] for item in payload["sources"]))
        self.assertEqual(payload["caution"], KEY_SOURCES_CAUTION)
        # どこにも生のキーは出てこない
        self.assertNotIn("fixture-", result.stdout)

    def test_no_key_hint_points_to_sources(self):
        result = self.run_cli(["key", "show"])
        self.assertIn("actor key sources", result.stdout)



class KeyCliModelTest(unittest.TestCase):
    """CLI から見たモデルの逃げ道（``key list`` / ``--fallback-model`` / ``key stats``）。"""

    @classmethod
    def setUpClass(cls):
        _FakeModelGemini.seen = []
        _FakeModelGemini.limited_models = set()
        cls.httpd = HTTPServerProc(("127.0.0.1", 0), _FakeModelGemini)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / ".actor"
        _FakeModelGemini.seen = []
        _FakeModelGemini.limited_models = set()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, args, extra_env: dict | None = None, stdin: str = ""):
        import subprocess

        env = dict(os.environ, ACTOR_DATA_DIR=str(self.data), ACTOR_BASE_URL=self.base,
                   ACTOR_MODEL="gemini-3.8-flash")
        for name in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"):
            env.pop(name, None)
        env.update(extra_env or {})
        return subprocess.run([sys.executable, "-m", "actor", *args], input=stdin, capture_output=True,
                              text=True, env=env, timeout=60,
                              cwd=str(Path(__file__).resolve().parent.parent))

    def test_list_json_has_models(self):
        payload = json.loads(self.run_cli(
            ["key", "list", "--json"], {"GEMINI_API_KEYS": "fixture-M1_1234567890,fixture-M2_1234567890"}).stdout)
        self.assertEqual(payload["model"], "gemini-3.8-flash")
        self.assertEqual(payload["model_fallbacks"], ["gemini-3.5-flash", "gemini-2.5-flash"])
        self.assertEqual(payload["ready_by_model"]["gemini-3.8-flash"], 2)
        self.assertNotIn("fixture-M1_1234567890", json.dumps(payload))

    def test_run_falls_back_and_reports_notice(self):
        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        env = {"GEMINI_API_KEY": "fixture-CLIFB_1234567890"}
        result = self.run_cli(["--no-banner", "やあ"], env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("🔁", result.stdout)
        self.assertIn("gemini-3.5-flash", result.stdout)
        self.assertIn("[gemini-3.5-flash] 回答です。", result.stdout)

        payload = json.loads(self.run_cli(["--json", "やあ"], env).stdout)
        self.assertEqual(payload["model"], "gemini-3.5-flash")
        self.assertEqual(payload["requested_model"], "gemini-3.8-flash")
        self.assertEqual(payload["notices"][0]["kind"], "model_fallback")
        self.assertNotIn("fixture-CLIFB_1234567890", json.dumps(payload))

    def test_custom_fallback_model_is_used_first(self):
        _FakeModelGemini.limited_models = {"gemini-3.8-flash", "gemini-3.5-flash"}
        result = self.run_cli(["--no-banner", "--fallback-model", "gemini-2.5-flash", "やあ"],
                             {"GEMINI_API_KEY": "fixture-CLIFB2_1234567890"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([model for model, _key in _FakeModelGemini.seen][-1], "gemini-2.5-flash")
        self.assertIn("[gemini-2.5-flash] 回答です。", result.stdout)

    def test_no_model_fallback_keeps_error(self):
        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        result = self.run_cli(["--no-banner", "--no-model-fallback", "やあ"],
                              {"GEMINI_API_KEY": "fixture-CLIFB3_1234567890"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("すべての Gemini API キーが使えません", result.stdout)
        self.assertIn("試したモデル: gemini-3.8-flash", result.stdout)
        self.assertNotIn("gemini-3.5-flash\t", "".join(f"{m}\t" for m, _k in _FakeModelGemini.seen))

    def test_stats_show_model_sections(self):
        _FakeModelGemini.limited_models = {"gemini-3.8-flash"}
        env = {"GEMINI_API_KEY": "fixture-CLISTATS_1234567890"}
        self.run_cli(["--no-banner", "やあ"], env)
        stats = self.run_cli(["key", "stats"], env)
        self.assertEqual(stats.returncode, 0, stats.stderr)
        self.assertIn("モデル別", stats.stdout)
        self.assertIn("gemini-3.8-flash", stats.stdout)
        self.assertIn("いちばん切れているモデル: gemini-3.8-flash", stats.stdout)
        self.assertIn("このモデルが切れたら自動で逃げます: gemini-3.5-flash", stats.stdout)

        listing = self.run_cli(["key", "list"], env)
        self.assertIn("モデルごとの空き", listing.stdout)
        self.assertIn("逃げ道: gemini-3.5-flash, gemini-2.5-flash", listing.stdout)

        doctor = self.run_cli(["doctor"], env)
        self.assertIn("モデルの逃げ道", doctor.stdout)
        self.assertIn("切れやすいモデル : gemini-3.8-flash", doctor.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
