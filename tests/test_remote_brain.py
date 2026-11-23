"""Unit tests for RemoteBrain and Router cloud switching (mocked HTTP, no live API key)."""

import json
import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import contextlib  # noqa: E402
import io  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app import config  # noqa: E402
from app.brain.remote import RemoteBrain, cloud_blocked_tools  # noqa: E402
from app.brain.router import Router  # noqa: E402
from app.brain.switching import INTERNET_HONESTY_TEXT, LOCAL_HONESTY_TEXT  # noqa: E402
from app.events import BUS  # noqa: E402
from app.memory.notes import NotesIndex  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.tools.applications import AppIndex  # noqa: E402
from app.tools.projects import Projects  # noqa: E402
from app.tools.registry import SCHEMAS, Toolbox  # noqa: E402


class _FakeLocalBrain:
    def __init__(self, model="qwen3:4b"):
        self.model = model
        self.history = []
        self.last_stats = {}

    def brain_name(self):
        return "smart"

    def switch_model(self, name_or_tag):
        self.model = "qwen3:4b" if name_or_tag == "smart" else name_or_tag
        return True, f"Switched to {self.model}"

    def decide(self, text, facts=None, notes=None):
        return [], "local reply"

    def synthesize(self, text, observations):
        return "local synth"

    def remember(self, user_text, reply):
        self.history.append((user_text, reply))


class RemoteBrainTests(unittest.TestCase):
    def test_openai_compat_decide_and_synthesize(self):
        captured = []

        def fake_post(url, headers, payload, timeout=30.0):
            captured.append((url, headers, payload))
            if len(captured) == 1:
                content = json.dumps({"actions": [{"tool": "get_time", "argument": ""}], "reply": ""})
            else:
                content = json.dumps({"actions": [], "reply": "It is 3:15 PM."})
            return {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 18},
            }

        rb = RemoteBrain(SCHEMAS, provider_name="gemini", api_key="test-key", requester=fake_post)
        calls, reply = rb.decide("what time is it")
        self.assertEqual(calls, [("get_time", {})])
        self.assertEqual(reply, "")
        self.assertIn("generativelanguage.googleapis.com", captured[0][0])
        self.assertEqual(captured[0][1]["Authorization"], "Bearer test-key")
        self.assertEqual(rb.last_stats["prompt_tokens"], 120)

        synth = rb.synthesize("what time is it", [("get_time", {}, "3:15 PM")])
        self.assertEqual(synth, "It is 3:15 PM.")

    def test_strips_markdown_fences_and_retries_bad_json(self):
        responses = iter([
            {"choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}], "usage": {}},
            {"choices": [{"message": {"content": "```json\n{\"actions\": [], \"reply\": \"Fixed.\"}\n```"}, "finish_reason": "stop"}], "usage": {}},
        ])
        rb = RemoteBrain(SCHEMAS, provider_name="gemini", api_key="k", requester=lambda *a, **kw: next(responses))
        calls, reply = rb.decide("hey")
        self.assertEqual(calls, [])
        self.assertEqual(reply, "Fixed.")
        self.assertTrue(rb.last_stats["retried"])

    def test_router_switches_to_gemini_and_back_to_local(self):
        mem = Memory(":memory:")
        local = _FakeLocalBrain()
        router = Router(Toolbox(AppIndex(), mem, Projects(mem, roots=[])), local)

        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-gem-key"}, clear=False):
            res = router.handle("switch to gemini")
            self.assertEqual(res.source, "fast")
            self.assertIn("gemini cloud brain", res.reply.lower())
            self.assertIsInstance(router.brain, RemoteBrain)
            self.assertTrue(router.policy.pinned)

            which = router.handle("which brain are you using")
            self.assertIn("gemini", which.reply.lower())

            back = router.handle("go local")
            self.assertIn("all local", back.reply.lower())
            self.assertIs(router.brain, local)
            self.assertFalse(router.policy.pinned)
        mem.close()

    def test_remote_system_prompt_is_honest_about_cloud(self):
        rb = RemoteBrain(SCHEMAS, provider_name="gemini", api_key="k", requester=lambda *a, **kw: {})
        self.assertNotIn("never send conversation data", rb.system_prompt)
        self.assertNotIn("Only opening websites and web search use the internet.", rb.system_prompt)
        self.assertIn("gemini cloud brain", rb.system_prompt)

    def test_remote_decide_and_synthesize_privacy_gating(self):
        captured = []

        def fake_post(_url, _headers, payload, timeout=30.0):
            captured.append(payload["messages"])
            return {
                "choices": [{"message": {"content": json.dumps({"actions": [], "reply": "Ok."})}, "finish_reason": "stop"}],
                "usage": {},
            }

        rb = RemoteBrain(SCHEMAS, provider_name="gemini", api_key="k", requester=fake_post)

        # Default: CLOUD_SHARES_MEMORY=False, CLOUD_SHARES_NOTES=False
        with (
            patch.object(config, "CLOUD_SHARES_MEMORY", False),
            patch.object(config, "CLOUD_SHARES_NOTES", False),
        ):
            rb.decide(
                "check everything",
                facts=["Secret exam is Friday"],
                notes=[("Secret Note", "Private vault excerpt")],
                observations=[
                    ("recall", {"query": "exam"}, "Secret exam is Friday"),
                    ("search_notes", {"query": "secret"}, "Private vault excerpt"),
                    ("get_time", {}, "It's 10:30 on Monday."),
                ],
            )
            sent_text = "\n".join(m["content"] for m in captured[-1])
            self.assertNotIn("Secret exam is Friday", sent_text)
            self.assertNotIn("Private vault excerpt", sent_text)
            self.assertNotIn("Excerpts from the user's notes", sent_text)
            self.assertIn("Tool observations from earlier steps", sent_text)
            self.assertIn("It's 10:30 on Monday.", sent_text)

            # synthesize skips HTTP for blocked tools
            calls_before = len(captured)
            self.assertEqual(rb.synthesize("when?", [("recall", {}, "Secret exam")]), "")
            self.assertEqual(rb.synthesize("notes?", [("search_notes", {}, "Vault stuff")]), "")
            self.assertEqual(len(captured), calls_before)

            # synthesize still works for non-private observations
            self.assertEqual(rb.synthesize("time?", [("get_time", {}, "10:30 AM")]), "Ok.")
            self.assertEqual(len(captured), calls_before + 1)

        # Opted in: both facts and notes are included
        with (
            patch.object(config, "CLOUD_SHARES_MEMORY", True),
            patch.object(config, "CLOUD_SHARES_NOTES", True),
        ):
            rb.decide(
                "check everything",
                facts=["Exam is Friday"],
                notes=[("Exam", "Hall B")],
                observations=[("recall", {"query": "exam"}, "Exam is Friday")],
            )
            sent_text = "\n".join(m["content"] for m in captured[-1])
            self.assertIn("Exam is Friday", sent_text)
            self.assertIn("Hall B", sent_text)
            self.assertIn("Excerpts from the user's notes", sent_text)

    def test_router_honesty_reflects_cloud_and_local_state(self):
        mem = Memory(":memory:")
        self.addCleanup(mem.close)
        local = _FakeLocalBrain()
        router = Router(Toolbox(AppIndex(), mem, Projects(mem, roots=[])), local)

        self.assertEqual(router.handle("are you local").reply, LOCAL_HONESTY_TEXT)
        self.assertEqual(router.handle("what needs the internet").reply, INTERNET_HONESTY_TEXT)

        rb = RemoteBrain(SCHEMAS, provider_name="gemini", api_key="k", requester=lambda *a, **kw: {})
        router.brain = rb
        with (
            patch.object(config, "CLOUD_SHARES_MEMORY", False),
            patch.object(config, "CLOUD_SHARES_NOTES", False),
        ):
            local_ans = router.handle("do you cost anything").reply
            self.assertIn("gemini cloud brain", local_ans)
            self.assertIn("stay on this PC", local_ans)
            net_ans = router.handle("what needs the internet").reply
            self.assertIn("gemini cloud brain", net_ans)
            self.assertIn("go local", net_ans)

        with (
            patch.object(config, "CLOUD_SHARES_MEMORY", True),
            patch.object(config, "CLOUD_SHARES_NOTES", False),
        ):
            mem_only = router.handle("are you local").reply
            self.assertIn("saved memory", mem_only)
            self.assertIn("notes stay on this PC", mem_only)

        router.handle("go local")
        self.assertEqual(router.handle("are you local").reply, LOCAL_HONESTY_TEXT)

    def test_router_cloud_privacy_end_to_end_and_bus_event(self):
        mem = Memory(":memory:")
        self.addCleanup(mem.close)
        mem.add_fact("Secret PIN is 9876")
        captured = []

        def fake_post(_url, _headers, payload, timeout=30.0):
            captured.append(payload["messages"])
            user_text = payload["messages"][-1]["content"].lower()
            if "secret pin" in user_text:
                content = json.dumps({"actions": [{"tool": "recall", "argument": "pin"}], "reply": ""})
            else:
                content = json.dumps({"actions": [], "reply": "Cloud answer."})
            return {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {},
            }

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Secret.md").write_text("Private note contents about project X.", encoding="utf-8")
            notes = NotesIndex([Path(tmp)])
            local = _FakeLocalBrain()
            router = Router(Toolbox(AppIndex(), mem, Projects(mem, roots=[]), notes), local, notes)
            rb = RemoteBrain(SCHEMAS, provider_name="gemini", api_key="k", requester=fake_post)
            router.brain = rb

            with (
                patch.object(config, "CLOUD_SHARES_MEMORY", False),
                patch.object(config, "CLOUD_SHARES_NOTES", False),
            ):
                # 1. General chat strips facts and notes
                q_bus = BUS.subscribe()
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        res_chat = router.handle("tell me about project x and my pin")
                    self.assertEqual(res_chat.reply, "Cloud answer.")
                    sent = "\n".join(m["content"] for m in captured[-1])
                    self.assertNotIn("9876", sent)
                    self.assertNotIn("Private note contents", sent)
                    events = []
                    while not q_bus.empty():
                        events.append(q_bus.get_nowait())
                    turn_evt = next(e for e in events if e["type"] == "turn")
                    self.assertTrue(turn_evt["is_cloud"])
                finally:
                    BUS.unsubscribe(q_bus)

                # 2. Notes queries answer locally with zero cloud calls & empty history
                calls_before = len(captured)
                res_nq = router.handle("what do my notes say about project x")
                res_rnq = router.handle("what was the latest note")
                self.assertEqual(len(captured), calls_before)
                self.assertEqual(res_nq.brain, "local")
                self.assertEqual(res_rnq.brain, "local")
                self.assertIn("Cloud note sharing is off", res_nq.reply)
                self.assertIn("Cloud note sharing is off", res_rnq.reply)

                # 3. 2-step recall skips synthesize() and does not store secret into RemoteBrain.history
                rb.history.clear()
                calls_before = len(captured)
                res_mem = router.handle("what is my secret pin")
                self.assertEqual(len(captured), calls_before + 1)
                self.assertIn("9876", res_mem.reply)
                self.assertEqual(rb.history, [])

            # 4. Cloud failure gives a cloud-specific error, not "Is Ollama running?"
            def fail_post(*_a, **_kw):
                raise RuntimeError("HTTP 503")

            router.brain = RemoteBrain(SCHEMAS, provider_name="groq", api_key="k", requester=fail_post)
            err_res = router.handle("hello from cloud")
            self.assertEqual(err_res.source, "none")
            self.assertIn("groq cloud brain", err_res.reply)
            self.assertIn("go local", err_res.reply)
            self.assertNotIn("Ollama", err_res.reply)

            # 5. Switching back to local sets is_cloud=False on BUS turn event
            router.brain = local
            q_bus = BUS.subscribe()
            try:
                router.handle("what time is it")
                events = []
                while not q_bus.empty():
                    events.append(q_bus.get_nowait())
                turn_evt = next(e for e in events if e["type"] == "turn")
                self.assertFalse(turn_evt["is_cloud"])
            finally:
                BUS.unsubscribe(q_bus)

    def test_cloud_forget_does_not_leak_the_forgotten_fact(self):
        mem = Memory(":memory:")
        self.addCleanup(mem.close)
        mem.add_fact("Secret PIN is 9876")
        captured = []

        def fake_post(_url, _headers, payload, timeout=30.0):
            captured.append("\n".join(m["content"] for m in payload["messages"]))
            user_text = payload["messages"][-1]["content"].lower()
            if "drop the pin" in user_text:
                content = json.dumps({"actions": [{"tool": "forget", "argument": "pin"}], "reply": ""})
            else:
                content = json.dumps({"actions": [], "reply": "Cloud answer."})
            return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {}}

        router = Router(Toolbox(AppIndex(), mem, Projects(mem, roots=[])), _FakeLocalBrain())
        rb = RemoteBrain(SCHEMAS, provider_name="gemini", api_key="k", requester=fake_post)
        router.brain = rb
        with (
            patch.object(config, "CLOUD_SHARES_MEMORY", False),
            patch.object(config, "CLOUD_SHARES_NOTES", False),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            res = router.handle("please drop the pin thing")
            self.assertIn("9876", res.reply)
            router.handle("tell me a joke")
        self.assertNotIn("9876", "\n".join(str(m) for m in rb.history))
        self.assertNotIn("9876", captured[-1])
        self.assertIn("forget", cloud_blocked_tools())

    def test_hud_has_cloud_indicator_and_telemetry_fixes(self):
        hud_path = Path(__file__).resolve().parents[1] / "app" / "ui" / "hud.html"
        html = hud_path.read_text(encoding="utf-8")
        self.assertIn('id="cloud-pill"', html)
        self.assertIn('id="cloud-badge"', html)
        self.assertIn("cloud-active", html)
        self.assertIn('["auto", "quick", "smart", "coder", "gemini", "groq"]', html)
        self.assertIn("loadTelemetry()", html)
        self.assertNotIn("refreshTelemetry", html)

