"""Resilience tests: tool and fast-path errors are spoken rather than raised."""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import contextlib  # noqa: E402
import io          # noqa: E402
import sqlite3     # noqa: E402
import threading   # noqa: E402
import time        # noqa: E402
import unittest    # noqa: E402
from unittest import mock  # noqa: E402

from app import config  # noqa: E402
from app.audio.push_to_talk import beep  # noqa: E402
from app.brain.router import Router  # noqa: E402
from app.events import BUS  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.tools.applications import App, AppIndex  # noqa: E402
from app.tools.registry import Toolbox  # noqa: E402

FAKE_APPS = AppIndex({
    "brave": App("Brave", "brave.exe"),
    "notepad": App("Notepad", "notepad.exe"),
})


def run(router, text):
    with contextlib.redirect_stdout(io.StringIO()):
        return router.handle(text)


class _SwitchyBrain:
    """A local brain whose auto-switch fails: `failure` is raised, or returned as (ok, msg)."""

    model = config.MODEL

    def __init__(self, failure):
        self.failure = failure
        self.switch_attempts: list[str] = []
        self.history: list[dict] = []
        self.last_stats: dict = {}

    def brain_name(self):
        return "smart"

    def switch_model(self, name):
        self.switch_attempts.append(name)
        if isinstance(self.failure, Exception):
            raise self.failure
        return self.failure

    def decide(self, text, facts=None, notes=None, observations=None):
        return [], "Answer."

    def remember(self, user_text, reply):
        pass


class BrokenMemory(Memory):           # Memory(":memory:") subclass
    def add_fact(self, text):
        raise sqlite3.OperationalError("database is locked")


class ResilienceTests(unittest.TestCase):
    def test_tool_error_is_spoken_not_raised(self):
        mem = BrokenMemory(":memory:")
        self.addCleanup(mem.close)
        r = Router(Toolbox(FAKE_APPS, mem), None)
        res = run(r, "remember that my exam is on friday")
        self.assertIn("didn't work", res.reply.lower())
        self.assertIn("OperationalError", res.reply)

    def test_fast_path_crash_is_caught(self):
        mem = Memory(":memory:")
        self.addCleanup(mem.close)
        tb = Toolbox(FAKE_APPS, mem)
        tb.tips.set_enabled = mock.Mock(side_effect=RuntimeError("boom"))
        res = run(Router(tb, None), "no more tips")
        self.assertEqual(res.source, "error")
        self.assertIn("still here", res.reply.lower())

    def _warnings(self, router, text):
        q = BUS.subscribe()
        try:
            res = run(router, text)
            events = []
            while not q.empty():
                events.append(q.get_nowait())
        finally:
            BUS.unsubscribe(q)
        return res, [e for e in events if e["type"] == "warning"]

    def test_failed_auto_switch_is_reported_not_swallowed(self):
        for failure in (RuntimeError("model 'qwen2.5-coder:3b' not found"),
                        (False, "Couldn't load qwen2.5-coder:3b.")):
            brain = _SwitchyBrain(failure)
            router = Router(Toolbox(FAKE_APPS), brain)
            with mock.patch.object(config, "AUTO_BRAIN", True):
                res, warnings = self._warnings(router, "why does my python script crash")
            self.assertEqual(res.reply, "Answer.")          # the turn still completes
            self.assertEqual(brain.switch_attempts, ["coder"])
            self.assertEqual(len(warnings), 1, failure)
            self.assertEqual(warnings[0]["where"], "auto_switch")
            self.assertIn("coder", warnings[0]["message"])

    def test_failed_trace_write_is_reported_not_swallowed(self):
        mem = Memory(":memory:")
        self.addCleanup(mem.close)
        mem.add_trace = mock.Mock(side_effect=sqlite3.OperationalError("database is locked"))
        router = Router(Toolbox(FAKE_APPS, mem), None)
        res, warnings = self._warnings(router, "what time is it")
        self.assertIn("It's", res.reply)
        self.assertEqual([w["where"] for w in warnings], ["trace"])
        self.assertIn("database is locked", warnings[0]["message"])

    def test_warnings_print_only_when_verbose(self):
        mem = Memory(":memory:")
        self.addCleanup(mem.close)
        mem.add_trace = mock.Mock(side_effect=sqlite3.OperationalError("database is locked"))
        for verbose, expect in ((False, ""), (True, "[warning] trace")):
            router = Router(Toolbox(FAKE_APPS, mem), None)
            router.verbose = verbose
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                router.handle("what time is it")
            warning_lines = [ln for ln in out.getvalue().splitlines() if "[warning]" in ln]
            self.assertEqual(bool(warning_lines), bool(expect))
            if expect:
                self.assertIn(expect, warning_lines[0])

    def test_beep_failure_is_swallowed(self):
        errors = []
        old_hook = threading.excepthook
        threading.excepthook = errors.append
        try:
            with mock.patch("winsound.Beep", side_effect=RuntimeError("Failed to beep")):
                beep(880)   # must not print a traceback from the thread
                time.sleep(0.05)
        finally:
            threading.excepthook = old_hook
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()

