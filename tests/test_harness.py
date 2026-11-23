"""Unit tests for the Tier 3 AgentHarness and confidence scoring."""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import unittest  # noqa: E402

from unittest.mock import patch  # noqa: E402

from app import config  # noqa: E402
from app.brain.harness import AgentHarness  # noqa: E402
from app.brain.model import Brain  # noqa: E402
from app.brain.remote import RemoteBrain  # noqa: E402
from app.brain.router import Router  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.tools.applications import AppIndex  # noqa: E402
from app.tools.projects import Projects  # noqa: E402
from app.tools.registry import SCHEMAS, Toolbox  # noqa: E402


class _StepBrain:
    model = "fake-step"
    history = []
    last_stats = {}

    def __init__(self, steps):
        self._steps = iter(steps)
        self.calls_seen = []

    def decide(self, text, facts=None, notes=None, observations=None):
        self.calls_seen.append((text, facts, notes, observations))
        return next(self._steps)

    def synthesize(self, text, observations):
        return f"Synthesized {len(observations)} observation(s)."

    def remember(self, u, r):
        self.history.append((u, r))


class HarnessTests(unittest.TestCase):
    def setUp(self):
        mem = Memory(":memory:")
        self.addCleanup(mem.close)
        mem.add_fact("Exam is on Friday")
        self.tb = Toolbox(AppIndex(), mem, Projects(mem, roots=[]))

    def test_multi_step_harness_loops_then_replies(self):
        brain = _StepBrain([
            ([("recall", {"query": "exam"})], ""),
            ([("get_time", {})], ""),
            ([], "Your exam is on Friday and it's afternoon now."),
        ])
        res = AgentHarness(self.tb, max_steps=4).run(brain, "check my exam and time")
        self.assertEqual(res.steps, 3)
        self.assertEqual(len(res.calls), 2)
        self.assertEqual(len(res.observations), 2)
        self.assertIn("Friday", res.reply)
        self.assertGreaterEqual(res.confidence, 0.85)

    def test_harness_respects_max_steps_and_synthesizes(self):
        brain = _StepBrain([
            ([("get_time", {})], ""),
            ([("list_projects", {})], ""),
        ])
        res = AgentHarness(self.tb, max_steps=2).run(brain, "keep checking")
        self.assertEqual(res.steps, 2)
        self.assertEqual(res.reply, "Synthesized 2 observation(s).")

    def test_harness_drops_duplicate_calls_and_lists_already_done(self):
        # 1. Step 2 repeating the exact same call synthesizes & stops early
        brain_dup = _StepBrain([
            ([("get_time", {})], ""),
            ([("get_time", {})], ""),
            ([("list_projects", {})], ""),
        ])
        res_dup = AgentHarness(self.tb, max_steps=4).run(brain_dup, "check time and exam")
        self.assertEqual(res_dup.steps, 2)
        self.assertEqual(res_dup.calls, [("get_time", {})])
        self.assertEqual(len(res_dup.observations), 1)
        self.assertEqual(res_dup.reply, "Synthesized 1 observation(s).")
        step1_prompt = brain_dup.calls_seen[0][0]
        self.assertIn("Step 1:", step1_prompt)
        self.assertIn("ALL needed tools", step1_prompt)
        step2_prompt = brain_dup.calls_seen[1][0]
        self.assertIn("Already done: get_time()", step2_prompt)
        self.assertIn("Don't repeat these", step2_prompt)
        self.assertIn("Check EVERY part", step2_prompt)

        # 2. Step 2 returning one duplicate and one fresh call runs only the fresh call
        brain_mixed = _StepBrain([
            ([("get_time", {})], ""),
            ([("get_time", {}), ("recall", {"query": "exam"})], ""),
            ([], "Exam is on Friday."),
        ])
        res_mixed = AgentHarness(self.tb, max_steps=4).run(brain_mixed, "check time and exam")
        self.assertEqual(res_mixed.steps, 3)
        self.assertEqual(res_mixed.calls, [("get_time", {}), ("recall", {"query": "exam"})])
        self.assertEqual(res_mixed.reply, "Exam is on Friday.")

    def test_router_tier3_prefix_and_confidence(self):
        brain = _StepBrain([
            ([("recall", {"query": "exam"})], ""),
            ([], "Exam is Friday."),
        ])
        router = Router(self.tb, brain)
        fast_res = router.handle("what time is it")
        self.assertEqual(fast_res.source, "fast")
        self.assertEqual(fast_res.confidence, 1.0)

        t3_res = router.handle("research: when is my exam")
        self.assertEqual(t3_res.source, "agent")
        self.assertEqual(t3_res.steps, 2)
        self.assertIn("Friday", t3_res.reply)

    def test_harness_passes_observations_in_dedicated_slot_not_notes(self):
        from types import SimpleNamespace as NS

        brain = _StepBrain([
            ([("get_time", {})], ""),
            ([], "Time checked."),
        ])
        res = AgentHarness(self.tb, max_steps=3).run(
            brain,
            "what time is it",
            notes=[("Note A", "body")],
        )
        self.assertEqual(res.reply, "Time checked.")
        self.assertEqual(len(brain.calls_seen), 2)
        self.assertEqual(brain.calls_seen[0][2], [("Note A", "body")])
        self.assertIsNone(brain.calls_seen[0][3])
        self.assertEqual(brain.calls_seen[1][2], [("Note A", "body")])
        self.assertEqual(len(brain.calls_seen[1][3]), 1)
        self.assertEqual(brain.calls_seen[1][3][0][0], "get_time")

        # Brain.decide() puts observations in its own system message, separate from notes
        class CaptureOllama:
            def chat(self, **kw):
                self.kw = kw
                msg = NS(content='{"actions": [], "reply": "Done."}', tool_calls=None, thinking=None)
                return NS(message=msg, done_reason="stop", load_duration=1e6,
                          prompt_eval_count=10, prompt_eval_duration=1e6,
                          eval_count=5, eval_duration=1e6)

        client = CaptureOllama()
        local_brain = Brain(SCHEMAS, model="qwen3:4b", client=client, mode="json")
        local_brain.decide(
            "what time is it",
            notes=[("Note A", "body")],
            observations=[("get_time", {}, "It's 12:00.")],
        )
        sys_msgs = [m["content"] for m in client.kw["messages"] if m["role"] == "system"]
        obs_msgs = [m for m in sys_msgs if "Tool observations from earlier steps" in m]
        note_msgs = [m for m in sys_msgs if "Excerpts from the user's notes" in m]
        self.assertEqual(len(obs_msgs), 1)
        self.assertIn("It's 12:00.", obs_msgs[0])
        self.assertEqual(len(note_msgs), 1)
        self.assertNotIn("It's 12:00.", note_msgs[0])

    def test_harness_stops_before_sending_private_observations_to_cloud(self):
        import json

        captured = []

        def fake_post(_url, _headers, payload, timeout=30.0):
            captured.append(payload["messages"])
            content = json.dumps({"actions": [{"tool": "recall", "argument": "exam"}], "reply": ""})
            return {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {},
            }

        rb = RemoteBrain(SCHEMAS, provider_name="gemini", api_key="k", requester=fake_post)
        with patch.object(config, "CLOUD_SHARES_MEMORY", False):
            res = AgentHarness(self.tb, max_steps=4).run(rb, "when is my exam")
        self.assertEqual(len(captured), 1)
        self.assertEqual(res.steps, 1)
        self.assertIn("Friday", res.reply)

