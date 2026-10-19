"""Fast-path and safety tests. No Ollama or Windows needed.

Run from the project folder:  python -m unittest -v
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import contextlib  # noqa: E402
import io          # noqa: E402
import unittest    # noqa: E402

from app.brain.router import Router, clean  # noqa: E402
from app.tools.applications import App, AppIndex  # noqa: E402
from app.tools.registry import Toolbox  # noqa: E402

FAKE_APPS = AppIndex({
    "brave": App("Brave", "brave.exe"),
    "visual studio code": App("Visual Studio Code", "code.exe"),
    "android studio": App("Android Studio", "studio64.exe"),
    "spotify": App("Spotify", "shell:AppsFolder\\Spotify"),
    "notepad": App("Notepad", "notepad.exe"),
    "calculator": App("Calculator", "calc.exe"),
})


class FakeBrain:
    """Stands in for the model and records what reached it."""
    def __init__(self, calls=None, reply=""):
        self.calls, self.reply, self.seen, self.history = calls or [], reply, [], []

    def decide(self, text):
        self.seen.append(text)
        return self.calls, self.reply

    def remember(self, *_):
        pass


def run(router, text):
    with contextlib.redirect_stdout(io.StringIO()):
        return router.handle(text)


class CleanTests(unittest.TestCase):
    def test_strips_wake_word_and_politeness(self):
        self.assertEqual(clean("Adeola, open Brave."), "open brave")
        self.assertEqual(clean("hey adeola can you please open brave for me"), "open brave")
        self.assertEqual(clean("  OPEN   Brave!! "), "open brave")
        self.assertEqual(clean("Adeola, open Brave."), "open brave")
        self.assertEqual(clean("hey adeola what time is it"), "what time is it")


class BrainTests(unittest.TestCase):
    def test_decide_parses_tools_and_timings(self):
        from types import SimpleNamespace as NS
        from app.brain.model import Brain

        class FakeClient:
            def chat(self, **kw):
                self.kw = kw
                call = NS(function=NS(name="open_app", arguments={"name": "Brave"}))
                msg = NS(content="<think>hmm</think>", tool_calls=[call], thinking=None)
                return NS(message=msg, load_duration=5e6, prompt_eval_count=200,
                          prompt_eval_duration=1e8, eval_count=20, eval_duration=5e8)

        client = FakeClient()
        brain = Brain([], model="qwen3:4b", client=client)
        calls, reply = brain.decide("I want to browse")
        self.assertEqual(calls, [("open_app", {"name": "Brave"})])
        self.assertEqual(reply, "")
        self.assertFalse(client.kw["think"])
        self.assertTrue(client.kw["messages"][0]["content"].endswith("/no_think"))
        self.assertEqual(brain.last_stats["tok_per_s"], 40)
        self.assertEqual(brain.last_stats["thinking_chars"], len("<think>hmm</think>"))


class FastPathTests(unittest.TestCase):
    def setUp(self):
        self.brain = FakeBrain()
        self.router = Router(Toolbox(FAKE_APPS), self.brain)

    def test_open_known_app_skips_model(self):
        res = run(self.router, "Adeola, open Brave")
        self.assertEqual(res.source, "fast")
        self.assertEqual(res.calls, [("open_app", {"name": "brave"})])
        self.assertEqual(res.reply, "Opening Brave.")
        self.assertEqual(self.brain.seen, [])

    def test_alias(self):
        self.assertEqual(run(self.router, "launch vs code").reply, "Opening Visual Studio Code.")

    def test_multiple_apps(self):
        res = run(self.router, "open notepad and calculator")
        self.assertEqual(len(res.calls), 2)
        self.assertEqual(res.reply, "Opening Notepad. Opening Calculator.")

    def test_url_and_site_shortcut(self):
        self.assertEqual(run(self.router, "open github.com").calls[0][0], "open_url")
        self.assertIn("youtube", run(self.router, "open youtube").reply)

    def test_search_time_lock_help(self):
        self.assertEqual(run(self.router, "search for kotlin flows on google").calls,
                         [("web_search", {"query": "kotlin flows"})])
        self.assertEqual(run(self.router, "what time is it?").calls, [("get_time", {})])
        self.assertEqual(run(self.router, "lock my computer").calls, [("lock_pc", {})])
        self.assertIn("open apps", run(self.router, "what can you do").reply)

    def test_vague_open_goes_to_model(self):
        run(self.router, "open the thing I use for music")
        self.assertEqual(self.brain.seen, ["open the thing i use for music"])


class ModelPathTests(unittest.TestCase):
    def test_model_tool_call_is_executed(self):
        router = Router(Toolbox(FAKE_APPS), FakeBrain([("open_app", {"name": "Spotify"})]))
        res = run(router, "play me some music")
        self.assertEqual((res.source, res.reply), ("llm", "Opening Spotify."))

    def test_chat_reply(self):
        router = Router(Toolbox(FAKE_APPS), FakeBrain(reply="I'm Adeola."))
        res = run(router, "who are you")
        self.assertEqual((res.source, res.reply), ("chat", "I'm Adeola."))

    def test_invented_tool_is_refused(self):
        router = Router(Toolbox(FAKE_APPS), FakeBrain([("delete_files", {"path": "C:\\"})]))
        self.assertIn("don't have a tool", run(router, "clean my disk").reply)

    def test_unknown_app_gives_suggestion(self):
        router = Router(Toolbox(FAKE_APPS), FakeBrain([("open_app", {"name": "spotfy"})]))
        reply = run(router, "music please").reply
        self.assertTrue("Spotify" in reply, reply)

    def test_missing_argument(self):
        router = Router(Toolbox(FAKE_APPS), FakeBrain([("open_app", {})]))
        self.assertIn("need a name", run(router, "open something").reply)

    def test_model_offline(self):
        class Broken(FakeBrain):
            def decide(self, text):
                raise ConnectionError
        res = run(Router(Toolbox(FAKE_APPS), Broken()), "do something clever")
        self.assertIn("Ollama", res.reply)


if __name__ == "__main__":
    unittest.main()
