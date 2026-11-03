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

from app.audio.push_to_talk import beep  # noqa: E402
from app.brain.router import Router  # noqa: E402
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

