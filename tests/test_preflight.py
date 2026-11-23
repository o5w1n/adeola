"""Unit tests for tools/preflight.py pure logic and check orchestration."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

os.environ.setdefault("ADEOLA_DRY_RUN", "1")

from app import config  # noqa: E402
from app.memory.notes import NotesIndex  # noqa: E402
from tools.preflight import (  # noqa: E402
    LOOP_MIN,
    LOOP_SENTENCE,
    SWAP_WARN_SECONDS,
    Check,
    budget_line,
    check_brain_swap,
    check_notes,
    loop_match,
    main,
    run_check,
    summary,
)


class PreflightTests(unittest.TestCase):
    def test_exception_becomes_fail_not_traceback(self):
        c = run_check("Ollama", lambda: (_ for _ in ()).throw(ConnectionError("refused")))
        self.assertEqual((c.status, "ConnectionError" in c.detail), ("FAIL", True))

    def test_warn_only_downgrades(self):
        self.assertEqual(run_check("Mic", lambda: 1 / 0, warn_only=True).status, "WARN")

    def test_summary_exit_codes(self):
        self.assertEqual(
            summary([Check("a", "PASS", ""), Check("b", "WARN", "")]),
            ("1 pass · 0 fail · 1 warn", 0),
        )
        self.assertEqual(summary([Check("a", "FAIL", "")])[1], 1)

    def test_loop_match_tolerates_small_differences(self):
        self.assertGreaterEqual(
            loop_match(
                "Good afternoon. The local voice is live.",
                "good afternoon the local voices live",
            ),
            LOOP_MIN,
        )
        self.assertLess(
            loop_match("Good afternoon. The local voice is live.", "open notepad"),
            LOOP_MIN,
        )

    def test_budget_line(self):
        self.assertEqual(
            budget_line(0.5, 1.0, None),
            "LATENCY BUDGET: hear 0.50 s · think 1.00 s · speak n/a · total 1.50 s",
        )

    def test_budget_line_all_present_and_all_none(self):
        self.assertEqual(
            budget_line(0.42, 1.10, 0.35),
            "LATENCY BUDGET: hear 0.42 s · think 1.10 s · speak 0.35 s · total 1.87 s",
        )
        self.assertEqual(
            budget_line(None, None, None),
            "LATENCY BUDGET: hear n/a · think n/a · speak n/a · total n/a",
        )

    def test_run_check_accepts_tuple_with_explicit_seconds(self):
        c = run_check("Voice", lambda: ("ok", 0.35))
        self.assertEqual(c.status, "PASS")
        self.assertEqual(c.detail, "ok")
        self.assertAlmostEqual(c.seconds, 0.35)

    def test_main_mismatch_in_loop_fails(self):
        import numpy as np

        fake_model_entry = mock.Mock()
        fake_model_entry.model = "qwen3:4b"
        fake_list = mock.Mock(models=[fake_model_entry])

        fake_brain = mock.Mock()
        fake_brain.decide.side_effect = [
            ([("open_app", {"name": "notepad"})], ""),
            ([], "Mittens."),
        ]

        fake_kokoro = mock.Mock()
        fake_kokoro.get_voice_style.return_value = np.zeros((1, 256), dtype=np.float32)
        fake_kokoro.create.return_value = (np.zeros(2400, dtype=np.float32), 24000)

        fake_transcriber = mock.Mock()
        fake_transcriber.transcribe.return_value = "open notepad"

        buf = io.StringIO()
        with (
            mock.patch("tools.preflight.ollama.Client") as m_client,
            mock.patch("tools.preflight.Brain", return_value=fake_brain),
            mock.patch("tools.preflight.load_kokoro", return_value=fake_kokoro),
            mock.patch("tools.preflight.Transcriber", return_value=fake_transcriber),
            mock.patch("tools.preflight.config.MEMORY_DB", new=":memory:"),
            mock.patch("tools.preflight.check_notes", return_value="2 notes indexed (1 ms)"),
            mock.patch("sounddevice.query_devices", return_value={"name": "Fake Mic"}),
            redirect_stdout(buf),
        ):
            m_client.return_value.list.return_value = fake_list
            code = main()

        out = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("✗ The loop", out)
        self.assertIn("LATENCY BUDGET:", out)
        self.assertIn("Needs the internet:", out)


    def test_notes_check_passes_with_two_notes(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "Alpha Note.md").write_text("Alpha content here.", encoding="utf-8")
            (tmp / "Beta Note.md").write_text("Beta content here.", encoding="utf-8")
            idx = NotesIndex([tmp])
            c = run_check("Notes", lambda: check_notes(idx), warn_only=True)
            self.assertEqual(c.status, "PASS")
            self.assertIn("2 notes", c.detail)

    def test_notes_check_warns_on_missing_folder(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            idx = NotesIndex([tmp / "missing"])
            c = run_check("Notes", lambda: check_notes(idx), warn_only=True)
            self.assertEqual(c.status, "WARN")

    def test_notes_check_fails_when_search_misses_first_note(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "Alpha Note.md").write_text("Alpha content here.", encoding="utf-8")
            idx = NotesIndex([tmp])
            idx.search = lambda *a, **kw: []
            with self.assertRaises(AssertionError):
                check_notes(idx)

    def test_brain_swap_skips_when_auto_brain_disabled(self):
        brain = mock.Mock()
        with mock.patch.object(config, "AUTO_BRAIN", False):
            c = run_check("Brain swap", lambda: check_brain_swap(brain, ["qwen3:4b"]), warn_only=True)
        self.assertEqual(c.status, "PASS")
        self.assertIn("auto-brain off", c.detail)
        brain.switch_model.assert_not_called()

    def test_brain_swap_warns_when_coder_model_not_pulled(self):
        brain = mock.Mock()
        with mock.patch.object(config, "AUTO_BRAIN", True):
            c = run_check("Brain swap", lambda: check_brain_swap(brain, ["qwen3:4b"]), warn_only=True)
        self.assertEqual(c.status, "WARN")
        self.assertIn("not pulled", c.detail)
        self.assertIn("AUTO_BRAIN = False", c.detail)
        brain.switch_model.assert_not_called()

    def test_brain_swap_times_fast_and_slow_round_trip(self):
        coder = config.BRAINS["coder"]
        installed = [config.MODEL, coder]

        # Fast swap (1.2 s + 1.5 s) -> PASS
        fast_brain = mock.Mock()
        fast_brain.switch_model.return_value = (True, "Switched")
        fast_ticks = iter([0.0, 1.2, 1.2, 2.7])
        with mock.patch.object(config, "AUTO_BRAIN", True):
            c_fast = run_check(
                "Brain swap",
                lambda: check_brain_swap(fast_brain, installed, warn_seconds=4.0, clock=lambda: next(fast_ticks)),
                warn_only=True,
            )
        self.assertEqual(c_fast.status, "PASS")
        self.assertIn("smart→coder 1.20 s", c_fast.detail)
        self.assertIn("coder→smart 1.50 s", c_fast.detail)
        self.assertAlmostEqual(c_fast.seconds, 2.7)
        self.assertEqual(
            [call.args[0] for call in fast_brain.switch_model.call_args_list],
            ["coder", "smart"],
        )

        # Slow swap (5.5 s + 2.0 s > SWAP_WARN_SECONDS) -> WARN
        slow_brain = mock.Mock()
        slow_brain.switch_model.return_value = (True, "Switched")
        slow_ticks = iter([0.0, SWAP_WARN_SECONDS + 1.5, SWAP_WARN_SECONDS + 1.5, SWAP_WARN_SECONDS + 3.5])
        with mock.patch.object(config, "AUTO_BRAIN", True):
            c_slow = run_check(
                "Brain swap",
                lambda: check_brain_swap(slow_brain, installed, clock=lambda: next(slow_ticks)),
                warn_only=True,
            )
        self.assertEqual(c_slow.status, "WARN")
        self.assertIn("AUTO_BRAIN = False", c_slow.detail)

    def test_brain_swap_warns_when_switch_fails(self):
        coder = config.BRAINS["coder"]
        brain = mock.Mock()
        brain.switch_model.return_value = (False, "OOM")
        with mock.patch.object(config, "AUTO_BRAIN", True):
            c = run_check("Brain swap", lambda: check_brain_swap(brain, [config.MODEL, coder]), warn_only=True)
        self.assertEqual(c.status, "WARN")
        self.assertIn("OOM", c.detail)


if __name__ == "__main__":
    unittest.main()
