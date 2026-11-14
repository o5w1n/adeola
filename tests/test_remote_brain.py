"""Unit tests for RemoteBrain and Router cloud switching (mocked HTTP, no live API key)."""

import json
import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import unittest  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app.brain.remote import RemoteBrain  # noqa: E402
from app.brain.router import Router  # noqa: E402
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

