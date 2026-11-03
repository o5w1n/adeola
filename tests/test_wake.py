"""Wake-word state machine tests with a fake detector. No mic or model needed.

Run from the project folder:  python -m unittest -v
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import threading  # noqa: E402
import time       # noqa: E402
import unittest   # noqa: E402

import numpy as np  # noqa: E402

from app import config  # noqa: E402
from app.audio.recorder import BLOCK  # noqa: E402
from app.audio.wake import WakeWord, wake_model_ref  # noqa: E402

WAKE, WAKE_SOFT, SPEECH, SILENCE = 0.7, 0.6, 0.2, 0.0


def block(level):
    return np.full(BLOCK, level, dtype=np.float32)


class FakeDetector:
    """Fires on blocks at the WAKE (0.9) or WAKE_SOFT (0.6) level."""
    def __init__(self):
        self.resets = 0

    def score(self, b):
        val = float(b[0])
        if abs(val - WAKE) < 1e-6:
            return 0.9
        if abs(val - WAKE_SOFT) < 1e-6:
            return 0.6
        return 0.0

    def reset(self):
        self.resets += 1


class FakeSpeaker:
    def __init__(self, speaking=False):
        self.speaking, self.last_spoke, self.stops = speaking, 0.0, 0

    def stop(self):
        self.stops += 1


class WakeTests(unittest.TestCase):
    def make(self, **kw):
        self.handled = []
        self.done = threading.Event()

        def handle(audio):
            self.handled.append(audio)
            self.done.set()

        self.detector = FakeDetector()
        self.out = []
        return WakeWord(handle, self.detector, say=self.out.append, **kw)

    def feed(self, wake, level, n=1):
        for _ in range(n):
            wake.feed(block(level))

    def blocks_for(self, seconds):
        return int(round(seconds / 0.08)) + 1

    def test_wake_then_command_then_silence(self):
        speaker = FakeSpeaker()
        wake = self.make(speaker=speaker)
        self.feed(wake, SILENCE, 5)
        self.assertEqual(wake.state, wake.IDLE)
        self.feed(wake, WAKE)
        self.assertEqual(wake.state, wake.CAPTURE)
        self.assertEqual(speaker.stops, 1)             # wake word interrupts Adeola
        self.feed(wake, SPEECH, 12)                    # ~1 s of "open brave"
        self.feed(wake, SILENCE, self.blocks_for(config.END_SILENCE_SECONDS + config.FINISH_SECONDS))
        self.assertTrue(self.done.wait(2))
        audio = self.handled[0]
        self.assertGreaterEqual(len(audio), 12 * BLOCK)
        for _ in range(100):
            if wake.state == wake.IDLE:
                break
            time.sleep(0.01)
        self.assertEqual(wake.state, wake.IDLE)
        self.assertGreaterEqual(self.detector.resets, 1)

    def test_no_command_after_wake(self):
        wake = self.make()
        self.feed(wake, WAKE)
        self.feed(wake, SILENCE, self.blocks_for(config.NO_SPEECH_TIMEOUT))
        self.assertEqual(wake.state, wake.IDLE)
        self.assertEqual(self.handled, [])

    def test_long_command_is_cut_at_max(self):
        wake = self.make()
        self.feed(wake, WAKE)
        self.feed(wake, SPEECH, self.blocks_for(config.MAX_COMMAND_SECONDS))
        self.assertTrue(self.done.wait(2))

    def test_ignored_while_adeola_speaks_below_barge_in(self):
        speaker = FakeSpeaker(speaking=True)
        wake = self.make(speaker=speaker)
        self.feed(wake, WAKE_SOFT)
        self.assertEqual(wake.state, wake.IDLE)
        self.assertEqual(speaker.stops, 0)

    def test_barge_in_stops_adeola_when_score_high(self):
        speaker = FakeSpeaker(speaking=True)
        wake = self.make(speaker=speaker)
        self.feed(wake, SPEECH, 3)                     # Adeola's own voice while speaking
        self.feed(wake, WAKE)
        self.assertEqual(wake.state, wake.CAPTURE)
        self.assertEqual(speaker.stops, 1)
        self.assertEqual(wake._audio, [])              # her own voice is not prepended to capture

    def test_ignored_right_after_adeola_speaks(self):
        speaker = FakeSpeaker()
        speaker.last_spoke = time.monotonic()
        wake = self.make(speaker=speaker)
        self.feed(wake, WAKE)
        self.assertEqual(wake.state, wake.IDLE)

    def test_ignored_while_push_to_talk_held(self):
        wake = self.make(is_busy=lambda: True)
        self.feed(wake, WAKE)
        self.assertEqual(wake.state, wake.IDLE)

    def test_mic_ignored_while_busy(self):
        release = threading.Event()
        detector = FakeDetector()
        wake = WakeWord(lambda audio: release.wait(2), detector, say=lambda *_: None)
        self.feed(wake, WAKE)
        self.feed(wake, SPEECH, 5)
        self.feed(wake, SILENCE, self.blocks_for(config.END_SILENCE_SECONDS + config.FINISH_SECONDS))
        self.assertEqual(wake.state, wake.BUSY)
        self.feed(wake, WAKE)                          # ignored, still handling
        self.assertEqual(wake.state, wake.BUSY)
        release.set()

    def test_preroll_is_prepended_to_capture(self):
        wake = self.make()
        marker = 0.02
        self.feed(wake, marker, 4)
        self.feed(wake, WAKE)
        self.feed(wake, SPEECH, 6)
        self.feed(wake, SILENCE, self.blocks_for(config.END_SILENCE_SECONDS + config.FINISH_SECONDS))
        self.assertTrue(self.done.wait(2))
        audio = self.handled[0]
        np.testing.assert_allclose(audio[: 4 * BLOCK], marker)

    def test_mid_sentence_pause_resumes_capture(self):
        wake = self.make()
        self.feed(wake, WAKE)
        self.feed(wake, SPEECH, 6)
        self.feed(wake, SILENCE, self.blocks_for(config.END_SILENCE_SECONDS))
        self.assertEqual(wake.state, wake.FINISH)
        self.assertEqual(self.handled, [])
        self.feed(wake, SPEECH, 6)
        self.assertEqual(wake.state, wake.CAPTURE)
        self.feed(wake, SILENCE, self.blocks_for(config.END_SILENCE_SECONDS + config.FINISH_SECONDS))
        self.assertTrue(self.done.wait(2))
        self.assertEqual(len(self.handled), 1)
        speech_samples = np.sum(np.isclose(self.handled[0], SPEECH))
        self.assertEqual(speech_samples, 12 * BLOCK)


class ModelChoiceTests(unittest.TestCase):
    def test_falls_back_until_trained_model_exists(self):
        ref, phrase = wake_model_ref()
        if (config.MODELS_DIR / config.WAKE_MODEL).exists():
            self.assertEqual(phrase, config.ASSISTANT_NAME)
        else:
            self.assertEqual((ref, phrase), ("hey_jarvis", "hey jarvis"))


if __name__ == "__main__":
    unittest.main()
