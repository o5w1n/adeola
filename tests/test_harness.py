"""Unit tests for the Tier 3 AgentHarness and confidence scoring."""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import unittest  # noqa: E402

from app.brain.harness import AgentHarness  # noqa: E402
from app.brain.router import Router  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.tools.applications import AppIndex  # noqa: E402
from app.tools.projects import Projects  # noqa: E402
from app.tools.registry import Toolbox  # noqa: E402


class _StepBrain:
    model = "fake-step"
    history = []
    last_stats = {}

    def __init__(self, steps):
        self._steps = iter(steps)

    def decide(self, text, facts=None, notes=None):
        return next(self._steps)

    def synthesize(self, text, observations):
        return f"Synthesized {len(observations)} observation(s)."

    def remember(self, u, r):
        self.history.append((u, r))


class HarnessTests(unittest.TestCase):
    def setUp(self):
        mem = Memory(":memory:")
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
            ([("get_time", {})], ""),
        ])
        res = AgentHarness(self.tb, max_steps=2).run(brain, "keep checking")
        self.assertEqual(res.steps, 2)
        self.assertEqual(res.reply, "Synthesized 2 observation(s).")

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

