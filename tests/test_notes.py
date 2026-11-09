"""Read-only markdown notes index and routing tests."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ADEOLA_DRY_RUN", "1")

from app import config  # noqa: E402
from app.brain.model import Brain  # noqa: E402
from app.brain.router import Router  # noqa: E402
from app.memory.notes import Note, NotesIndex  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.tools.applications import App, AppIndex  # noqa: E402
from app.tools.registry import SCHEMAS, Toolbox  # noqa: E402

FAKE_APPS = AppIndex({
    "brave": App("Brave", "brave.exe"),
    "notepad": App("Notepad", "notepad.exe"),
})


class FakeBrain:
    def __init__(self, calls=None, reply=""):
        self.calls = calls or []
        self.reply = reply
        self.seen = []
        self.seen_notes = []
        self.history = []

    def decide(self, text, facts=None, notes=None):
        self.seen.append(text.lower())
        self.seen_notes.append(notes or [])
        return self.calls, self.reply

    def remember(self, *_):
        pass


class FakeOllama:
    def __init__(self, content='{"actions": [], "reply": "900 ms."}'):
        self.content = content
        self.kw = {}

    def chat(self, **kw):
        from types import SimpleNamespace as NS
        self.kw = kw
        msg = NS(content=self.content, tool_calls=None, thinking=None)
        return NS(message=msg, done_reason="stop", load_duration=5e6,
                  prompt_eval_count=200, prompt_eval_duration=1e8,
                  eval_count=20, eval_duration=5e8)


def run(router, text):
    with contextlib.redirect_stdout(io.StringIO()):
        return router.handle(text)


class NotesIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_dir.name)

        # Finish Window.md (frontmatter + "set FINISH_MS to 900")
        padding = " ".join(["filler"] * 30)
        (self.tmp / "Finish Window.md").write_text(
            f"---\ntags: [voice, tuning]\n---\n{padding} finish window: set FINISH_MS to 900 {padding}\n",
            encoding="utf-8",
        )

        # Daily/2026-11-01.md (mentions "finish window" once in a long body)
        (self.tmp / "Daily").mkdir()
        (self.tmp / "Daily" / "2026-11-01.md").write_text(
            ("Today I worked on many things. " * 20) + "Also adjusted the finish window setting.",
            encoding="utf-8",
        )

        # Ideas.md (with [[Finish Window|the window note]] and [[Plain Link]])
        (self.tmp / "Ideas.md").write_text(
            "See [[Finish Window|the window note]] and [[Plain Link]] for details.",
            encoding="utf-8",
        )

        # .obsidian/workspace.md (contains "finish window")
        (self.tmp / ".obsidian").mkdir()
        (self.tmp / ".obsidian" / "workspace.md").write_text(
            "finish window internal state",
            encoding="utf-8",
        )

        # Big.md (> NOTES_MAX_BYTES)
        (self.tmp / "Big.md").write_bytes(b"x" * (config.NOTES_MAX_BYTES + 100))

        # Bad.md (raw bytes b"\xff\xfe finish \x00")
        (self.tmp / "Bad.md").write_bytes(b"\xff\xfe finish \x00")

        self.idx = NotesIndex([self.tmp])

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_title_match_ranks_first(self):
        self.assertEqual(self.idx.search("finish window")[0].title, "Finish Window")

    def test_obsidian_internals_and_big_files_skipped(self):
        titles = {n.title for n in self.idx.notes}
        self.assertNotIn("workspace", titles)
        self.assertNotIn("Big", titles)

    def test_bad_bytes_do_not_crash(self):
        self.assertIn("Bad", {n.title for n in self.idx.notes})

    def test_frontmatter_and_wikilinks_cleaned(self):
        n = next(n for n in self.idx.notes if n.title == "Ideas")
        self.assertIn("the window note", n.body)
        self.assertIn("Plain Link", n.body)
        self.assertNotIn("[[", n.body)
        self.assertFalse(next(n for n in self.idx.notes if n.title == "Finish Window").body.startswith("---"))

    def test_vague_question_attaches_nothing(self):
        self.assertEqual(self.idx.search("what about it"), [])

    def test_missing_folder_is_reported_not_raised(self):
        i = NotesIndex([self.tmp / "nope"])
        self.assertEqual(i.load(), 0)
        self.assertEqual(i.missing, [self.tmp / "nope"])

    def test_excerpt_is_bounded_and_centred(self):
        ex = self.idx.excerpt(self.idx.search("finish window")[0], "finish window", chars=80)
        self.assertLessEqual(len(ex), 80)
        self.assertIn("900", ex)

    def test_note_dataclass_and_folder(self):
        daily = next(n for n in self.idx.notes if n.title == "2026-11-01")
        self.assertIsInstance(daily, Note)
        self.assertEqual(daily.folder, "Daily")

    def test_add_capture_writes_markdown_and_indexes_live(self):
        captures_dir = self.tmp / "captures"
        idx = NotesIndex([self.tmp], captures_dir=captures_dir)
        note = idx.add_capture("The finish window should be 900 milliseconds")
        self.assertIsNotNone(note)
        self.assertTrue(note.path.exists())
        self.assertEqual(note.folder, "captures")
        self.assertEqual(idx.search("finish window 900")[0].path, note.path)


class RouterNotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_dir.name)
        padding = " ".join(["filler"] * 30)
        (self.tmp / "Finish Window.md").write_text(
            f"---\ntags: [voice, tuning]\n---\n{padding} finish window: set FINISH_MS to 900 {padding}\n",
            encoding="utf-8",
        )
        self.idx = NotesIndex([self.tmp])
        self.tb = Toolbox(FAKE_APPS)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_explicit_notes_question_uses_notes_and_cites(self):
        brain = FakeBrain(reply="Set it to 900 ms.")
        res = run(Router(self.tb, brain, self.idx), "what do my notes say about the finish window")
        self.assertEqual(res.source, "notes")
        self.assertEqual(res.sources, ["Finish Window"])
        self.assertEqual(brain.seen_notes[0][0][0], "Finish Window")

    def test_no_match_says_so_without_model_or_web(self):
        brain = FakeBrain()
        res = run(Router(self.tb, brain, self.idx), "search my notes for zebras")
        self.assertEqual(res.reply, "Your notes don't mention zebras.")
        self.assertEqual(brain.seen, [])
        self.assertEqual(res.calls, [])

    def test_plain_search_still_goes_to_web(self):
        self.assertEqual(run(Router(self.tb, None, self.idx), "search for zebras").calls[0][0], "web_search")

    def test_implicit_relevant_question_gets_notes(self):
        brain = FakeBrain(reply="900 ms.")
        res = run(Router(self.tb, brain, self.idx), "should the finish window be 900 milliseconds")
        self.assertTrue(brain.seen_notes[0])
        self.assertEqual(res.sources, ["Finish Window"])

    def test_vague_question_gets_no_notes_or_sources(self):
        brain = FakeBrain(reply="Sure.")
        res = run(Router(self.tb, brain, self.idx), "is that ok")
        self.assertFalse(brain.seen_notes[0])
        self.assertEqual(res.sources, [])

    def test_generic_command_words_do_not_attach_notes(self):
        (self.tmp / "Coding Log.md").write_text(
            "Sometimes people want to write some code and open their editor today.",
            encoding="utf-8",
        )
        self.idx.load()
        brain = FakeBrain(calls=[("open_app", {"name": "Visual Studio Code"})])
        run(Router(self.tb, brain, self.idx), "I want to write some code")
        self.assertEqual(brain.seen_notes[0], [])

    def test_tool_turns_never_carry_sources(self):
        brain = FakeBrain(calls=[("open_app", {"name": "notepad"})])
        self.assertEqual(
            run(Router(self.tb, brain, self.idx), "open the finish window notes in notepad").sources,
            [],
        )

    def test_remember_makes_capture_immediately_searchable_in_notes(self):
        mem = Memory(":memory:")
        self.addCleanup(mem.close)
        tb = Toolbox(FAKE_APPS, mem, notes=self.idx)
        brain = FakeBrain(reply="It is 750 ms.")
        router = Router(tb, brain, self.idx)
        run(router, "note to self: the barge in threshold is 750 milliseconds")
        res = run(router, "what do my notes say about barge in threshold")
        self.assertEqual(res.source, "notes")
        self.assertGreaterEqual(len(res.sources), 1)


class BrainNotesTests(unittest.TestCase):
    def test_notes_block_sits_between_history_and_user(self):
        client = FakeOllama()
        brain = Brain(SCHEMAS, model="qwen3:4b", client=client, mode="json")
        brain.remember("hello", "hi")
        brain.decide(
            "what do my notes say about the finish window",
            facts=["I am testing voice"],
            notes=[("Finish Window", "set FINISH_MS to 900")],
        )
        msgs = client.kw["messages"]
        self.assertEqual(msgs[0]["content"], brain.system_prompt)   # stable prefix untouched
        self.assertIn("### Finish Window", msgs[-2]["content"])
        self.assertEqual(msgs[-1]["role"], "user")


if __name__ == "__main__":
    unittest.main()
