"""Push-to-talk tests with a fake mic and fake Whisper. No audio hardware needed.

Run from the project folder:  python -m unittest -v
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import contextlib  # noqa: E402
import io          # noqa: E402
import unittest    # noqa: E402

import numpy as np  # noqa: E402

from app import config  # noqa: E402
from app.audio.push_to_talk import PushToTalk  # noqa: E402
from app.audio.recorder import BLOCK, PREROLL_SECONDS, Recorder  # noqa: E402
from app.audio.stt import hint_prompt, is_noise  # noqa: E402
from app.brain.router import Router  # noqa: E402
from app.tools.applications import App, AppIndex  # noqa: E402
from app.tools.registry import Toolbox  # noqa: E402

KEY = object()    # stands in for the push-to-talk key
OTHER = object()  # any other key


class FakeTranscriber:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def transcribe(self, audio):
        self.calls += 1
        return self.text


class FakeRecorder:
    def __init__(self, seconds=1.5):
        self.seconds, self.started = seconds, 0

    def start(self):
        self.started += 1

    def stop(self):
        return np.zeros(int(self.seconds * config.SAMPLE_RATE), dtype=np.float32)


def make(text="Adeola, open Brave.", seconds=1.5):
    apps = AppIndex({"brave": App("Brave", "brave.exe")})
    out = []
    ptt = PushToTalk(Router(Toolbox(apps)), FakeRecorder(seconds), FakeTranscriber(text),
                     key=KEY, say=out.append)
    return ptt, out


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


class PushToTalkTests(unittest.TestCase):
    def test_spoken_command_runs(self):
        ptt, out = make()
        self.assertEqual(quiet(ptt.process, ptt.recorder.stop()), "Opening Brave.")
        self.assertIn("You (voice): Adeola, open Brave.", out)
        self.assertIn("Adeola: Opening Brave.", out)

    def test_short_press_ignored(self):
        ptt, out = make(seconds=0.1)
        self.assertIsNone(ptt.process(ptt.recorder.stop()))
        self.assertEqual(ptt.transcriber.calls, 0)

    def test_nothing_heard(self):
        ptt, out = make(text="")
        self.assertIsNone(ptt.process(ptt.recorder.stop()))
        self.assertIn("didn't catch", out[-1])

    def test_key_repeat_and_other_keys(self):
        ptt, _ = make()
        ptt.on_press(OTHER)
        self.assertFalse(ptt.listening)
        for _ in range(5):          # Windows repeats key-down while held
            ptt.on_press(KEY)
        self.assertEqual(ptt.recorder.started, 1)
        ptt.on_release(OTHER)
        self.assertTrue(ptt.listening)


class RecorderTests(unittest.TestCase):
    def feed(self, rec, value, blocks):
        for _ in range(blocks):
            rec._callback(np.full((BLOCK, 1), value, dtype=np.float32), BLOCK, None, None)

    def test_keeps_preroll_and_recording_only(self):
        rec = Recorder()
        self.feed(rec, 0.1, 20)   # 2 s before the key: only the last 0.5 s is kept
        rec.start()
        self.feed(rec, 0.5, 10)   # 1 s while held
        audio = rec.stop()
        self.feed(rec, 0.9, 5)    # after release: not recorded
        preroll_blocks = int(PREROLL_SECONDS * config.SAMPLE_RATE / BLOCK)
        self.assertEqual(len(audio), (preroll_blocks + 10) * BLOCK)
        self.assertAlmostEqual(float(audio[0]), 0.1)
        self.assertAlmostEqual(float(audio[-1]), 0.5)
        self.assertEqual(len(rec.stop()), 0)


class SttHelpersTests(unittest.TestCase):
    def test_noise_filter(self):
        for junk in ("", "Thank you.", " you ", "Thanks for watching!"):
            self.assertTrue(is_noise(junk), junk)
        self.assertFalse(is_noise("open brave"))

    def test_hint_prompt_spells_names(self):
        prompt = hint_prompt()
        self.assertTrue(prompt.startswith("Adeola, open "))
        self.assertIn("Visual Studio Code", prompt)


if __name__ == "__main__":
    unittest.main()
