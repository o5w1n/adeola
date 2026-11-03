"""Fast-path and safety tests. No Ollama or Windows needed.

Run from the project folder:  python -m unittest -v
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import contextlib  # noqa: E402
import io          # noqa: E402
import unittest    # noqa: E402

from app import config  # noqa: E402
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
        self.model = config.MODEL

    def brain_name(self):
        for name, tag in config.BRAINS.items():
            if tag == self.model:
                return name
        return self.model

    def switch_model(self, name_or_tag):
        key = name_or_tag.strip().lower()
        target = config.BRAINS.get(key, name_or_tag.strip())
        label = key if key in config.BRAINS else target
        if target == self.model:
            return True, f"Already on my {label} brain ({self.model})."
        self.model = target
        return True, f"Switched to my {label} brain ({self.model})."

    def decide(self, text, facts=None, notes=None):
        self.seen.append(text.lower())
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


class FakeOllama:
    """Returns a canned Ollama response and remembers the request."""
    def __init__(self, content="", tool_calls=None):
        self.content, self.tool_calls = content, tool_calls

    def chat(self, **kw):
        from types import SimpleNamespace as NS
        self.kw = kw
        msg = NS(content=self.content, tool_calls=self.tool_calls, thinking=None)
        return NS(message=msg, load_duration=5e6, prompt_eval_count=200,
                  prompt_eval_duration=1e8, eval_count=20, eval_duration=5e8)


class SeqOllama:
    """Returns queued Ollama responses and records each request's num_predict budget."""
    def __init__(self, replies, done_reasons=None):
        self.replies = list(replies)
        self.done_reasons = list(done_reasons or [])
        self.budgets = []

    def chat(self, **kw):
        from types import SimpleNamespace as NS
        self.budgets.append(kw["options"]["num_predict"])
        item = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(item, tuple):
            content, done_reason = item
        else:
            content = item
            done_reason = self.done_reasons.pop(0) if self.done_reasons else "stop"
        msg = NS(content=content, tool_calls=None, thinking=None)
        return NS(message=msg, done_reason=done_reason, load_duration=5e6,
                  prompt_eval_count=200, prompt_eval_duration=1e8,
                  eval_count=20, eval_duration=5e8)


class BrainTests(unittest.TestCase):
    def make(self, client, mode):
        from app.brain.model import Brain
        from app.tools.registry import SCHEMAS
        return Brain(SCHEMAS, model="qwen3:4b", client=client, mode=mode)

    def test_json_mode_tool_call(self):
        client = FakeOllama('{"actions": [{"tool": "open_app", "argument": "Brave"},'
                            ' {"tool": "get_time", "argument": ""}], "reply": ""}')
        brain = self.make(client, "json")
        calls, reply = brain.decide("I want to browse")
        self.assertEqual(calls, [("open_app", {"name": "Brave"}), ("get_time", {})])
        self.assertEqual(reply, "")
        self.assertIn("properties", client.kw["format"])     # shape enforced by Ollama
        self.assertNotIn("tools", client.kw)
        self.assertFalse(client.kw["think"])
        self.assertEqual(client.kw["options"]["num_predict"], 150)
        self.assertTrue(client.kw["messages"][0]["content"].endswith("/no_think"))
        self.assertEqual(brain.last_stats["tok_per_s"], 40)
        self.assertEqual(brain.last_stats["prompt_tok_per_s"], 2000)

    def test_json_mode_chat_and_bad_json(self):
        brain = self.make(FakeOllama('{"actions": [], "reply": "Accra."}'), "json")
        self.assertEqual(brain.decide("capital of ghana"), ([], "Accra."))
        brain = self.make(FakeOllama('{"actions": [{"tool": "op'), "json")
        calls, reply = brain.decide("x")
        self.assertEqual(calls, [])
        self.assertTrue(brain.last_stats["parse_error"])

    def test_tools_mode(self):
        from types import SimpleNamespace as NS
        call = NS(function=NS(name="open_app", arguments={"name": "Brave"}))
        client = FakeOllama("<think>hmm</think>", [call])
        brain = self.make(client, "tools")
        self.assertEqual(brain.decide("browse"), ([("open_app", {"name": "Brave"})], ""))
        self.assertIn("tools", client.kw)
        self.assertEqual(brain.last_stats["thinking_chars"], len("<think>hmm</think>"))

    def test_truncated_json_is_retried_with_bigger_budget(self):
        client = SeqOllama(['{"actions": [], "reply": "A long answer that got cu',
                            '{"actions": [], "reply": "Full answer."}'])
        brain = self.make(client, "json")
        calls, reply = brain.decide("tell me about cats")
        self.assertEqual(calls, [])
        self.assertEqual(reply, "Full answer.")
        self.assertEqual(client.budgets, [config.MAX_OUTPUT_TOKENS, config.RETRY_OUTPUT_TOKENS])
        self.assertTrue(brain.last_stats["retried"])

    def test_done_reason_length_triggers_retry(self):
        client = SeqOllama(
            [('{"actions": [], "reply": "Valid JSON but hit length limit."}', "length"),
             ('{"actions": [], "reply": "Complete answer."}', "stop")]
        )
        brain = self.make(client, "json")
        calls, reply = brain.decide("explain quantum physics")
        self.assertEqual(calls, [])
        self.assertEqual(reply, "Complete answer.")
        self.assertEqual(len(client.budgets), 2)
        self.assertEqual(client.budgets, [config.MAX_OUTPUT_TOKENS, config.RETRY_OUTPUT_TOKENS])
        self.assertTrue(brain.last_stats["retried"])

    def test_two_bad_replies_give_friendly_line_and_stop(self):
        client = SeqOllama(["{bad", "{still bad"])
        brain = self.make(client, "json")
        _, reply = brain.decide("hi there")
        self.assertIn("train of thought", reply)
        self.assertEqual(len(client.budgets), 2)
        self.assertTrue(brain.last_stats["retried"])
        self.assertTrue(brain.last_stats["parse_error"])

    def test_good_reply_is_not_retried(self):
        client = SeqOllama(['{"actions": [], "reply": "Short and sweet."}'])
        brain = self.make(client, "json")
        calls, reply = brain.decide("say hi")
        self.assertEqual(calls, [])
        self.assertEqual(reply, "Short and sweet.")
        self.assertEqual(len(client.budgets), 1)
        self.assertFalse(brain.last_stats["retried"])

    def test_ping_if_idle_only_pings_after_threshold(self):
        client = FakeOllama()
        brain = self.make(client, "json")
        brain.warm_up()
        client.kw = None
        self.assertFalse(brain.ping_if_idle(600))
        self.assertIsNone(client.kw)
        brain._last_call -= 601
        self.assertTrue(brain.ping_if_idle(600))
        self.assertEqual(client.kw["options"]["num_predict"], 1)

    def test_start_keep_warm_disabled_when_zero(self):
        brain = self.make(FakeOllama(), "json")
        self.assertIsNone(brain.start_keep_warm(0))

    def test_warm_up_caches_system_prompt_and_schema(self):
        client = FakeOllama()
        brain = self.make(client, "json")
        brain.warm_up()
        self.assertEqual(client.kw["messages"][0]["content"], brain.system_prompt)
        self.assertIn("properties", client.kw["format"])
        self.assertEqual(client.kw["options"]["num_predict"], 1)

    def test_switch_model_updates_model_and_rolls_back_on_error(self):
        client = FakeOllama()
        brain = self.make(client, "json")
        self.assertEqual(brain.brain_name(), "smart")

        ok, msg = brain.switch_model("quick")
        self.assertTrue(ok)
        self.assertEqual(brain.model, "qwen3:1.7b")
        self.assertEqual(brain.brain_name(), "quick")
        self.assertIn("/no_think", brain.system_prompt)
        self.assertIn("Switched to my quick brain", msg)

        ok, msg = brain.switch_model("coder")
        self.assertTrue(ok)
        self.assertEqual(brain.model, "qwen2.5-coder:3b")
        self.assertEqual(brain.brain_name(), "coder")
        self.assertNotIn("/no_think", brain.system_prompt)

        def fail_chat(**_kw):
            raise RuntimeError("model not found")

        client.chat = fail_chat
        ok, msg = brain.switch_model("quick")
        self.assertFalse(ok)
        self.assertIn("ollama pull qwen3:1.7b", msg)
        self.assertEqual(brain.model, "qwen2.5-coder:3b")
        self.assertNotIn("/no_think", brain.system_prompt)

    def test_persona_under_300_words_and_stable(self):
        from app.brain.model import BASE_PROMPT
        self.assertLessEqual(len(BASE_PROMPT.split()), 300)
        self.assertIn("Receipts law", BASE_PROMPT)
        self.assertIn("cost nothing", BASE_PROMPT)
        b1 = self.make(FakeOllama(), "json")
        b2 = self.make(FakeOllama(), "json")
        self.assertEqual(b1.system_prompt, b2.system_prompt)


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
        self.assertEqual(run(self.router, "what's today's date").calls, [("get_time", {})])
        self.assertEqual(run(self.router, "lock my computer").calls, [("lock_pc", {})])
        self.assertIn("open apps", run(self.router, "what can you do").reply)
        for hi in ("Adeola, what's up?", "hello", "good morning"):
            res = run(self.router, hi)
            self.assertEqual((res.source, res.calls), ("fast", []), hi)

    def test_vague_open_goes_to_model(self):
        run(self.router, "open the thing I use for music")
        self.assertEqual(self.brain.seen, ["open the thing i use for music"])

    def test_interrupt_phrases_handled_on_fast_path(self):
        for phrase in ("Adeola, wait", "never mind", "stop talking", "cancel"):
            res = run(self.router, phrase)
            self.assertEqual(res.reply, "Okay.", phrase)
            self.assertEqual(res.source, "fast", phrase)
            self.assertEqual(res.calls, [], phrase)
        self.assertEqual(self.brain.seen, [])

    def test_brain_and_voice_shelf_commands(self):
        res = run(self.router, "which brain are you on")
        self.assertEqual(res.source, "fast")
        self.assertIn("smart brain", res.reply)

        res = run(self.router, "switch to your quick brain")
        self.assertEqual(res.source, "fast")
        self.assertIn("Switched to my quick brain (qwen3:1.7b).", res.reply)
        self.assertEqual(self.brain.model, "qwen3:1.7b")

        res = run(self.router, "switch to Martian brain")
        self.assertEqual(res.source, "fast")
        self.assertIn("Unknown brain 'martian'", res.reply)
        self.assertIn("Available:", res.reply)

        res = run(self.router, "which voice are you using")
        self.assertEqual(res.source, "fast")
        self.assertIn("Voice is off right now", res.reply)
        self.assertIn("Available:", res.reply)

        res = run(self.router, "use your calm voice")
        self.assertEqual(res.source, "fast")
        self.assertIn("Voice is off right now, but 'calm' is available", res.reply)
        self.assertEqual(self.brain.seen, [])

    def test_greeting_rotates_across_turns(self):
        from app.brain.router import GREETING_REPLIES
        self.assertEqual(run(self.router, "hello").reply, GREETING_REPLIES[0])
        self.assertEqual(run(self.router, "hello").reply, GREETING_REPLIES[1])
        self.assertEqual(run(self.router, "hello").reply, GREETING_REPLIES[2])
        self.assertEqual(self.brain.seen, [])

    def test_honesty_lines_handled_on_fast_path(self):
        for q in ("do you cost anything", "are you local", "where do you run"):
            res = run(self.router, q)
            self.assertEqual((res.source, res.calls), ("fast", []), q)
            self.assertIn("cost nothing", res.reply, q)
        res = run(self.router, "what needs the internet")
        self.assertEqual((res.source, res.calls), ("fast", []))
        self.assertIn("offline", res.reply)
        self.assertEqual(self.brain.seen, [])


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
            def decide(self, text, facts=None, notes=None):
                raise ConnectionError
        res = run(Router(Toolbox(FAKE_APPS), Broken()), "do something clever")
        self.assertIn("Ollama", res.reply)


if __name__ == "__main__":
    unittest.main()
