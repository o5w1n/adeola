"""Speaker tests with a fake Kokoro and a fake sound card. No model download needed.

Run from the project folder:  python -m unittest -v
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import threading  # noqa: E402
import unittest   # noqa: E402

import numpy as np  # noqa: E402

from app.audio.tts import Speaker, parse_blend, speech_text, split_sentences  # noqa: E402


class FakeEngine:
    VOICES = {"af_heart": 1.0, "af_bella": 2.0, "bf_emma": 4.0}

    def __init__(self):
        self.spoken = []

    def get_voices(self):
        return list(self.VOICES)

    def get_voice_style(self, name):
        return np.full((4, 256), self.VOICES[name], dtype=np.float32)

    def create(self, text, voice, speed, lang):
        self.spoken.append(text)
        return np.zeros(240), 24000


class FakePlayer:
    def __init__(self):
        self.played, self.stops = [], 0
        self.gate = threading.Event()
        self.gate.set()

    def play(self, audio, rate):
        self.played.append(len(audio))

    def wait(self):
        self.gate.wait(2)

    def stop(self):
        self.stops += 1


class HelperTests(unittest.TestCase):
    def test_parse_blend(self):
        self.assertEqual(parse_blend("af_heart"), [("af_heart", 1.0)])
        blend = parse_blend("af_heart*0.6 + bf_emma*40%")
        self.assertAlmostEqual(blend[0][1], 0.6)
        self.assertAlmostEqual(blend[1][1], 0.4)
        self.assertAlmostEqual(sum(w for _, w in parse_blend("af_heart*3 + af_bella*1")), 1.0)
        with self.assertRaises(ValueError):
            parse_blend("af_heart*0")

    def test_speech_text(self):
        self.assertEqual(speech_text("I'm Adeola 🎙"), "I'm Ahdeh-ohla")
        self.assertEqual(speech_text("adeola, hi"), "Ahdeh-ohla, hi")

    def test_split_sentences(self):
        self.assertEqual(split_sentences("Opening Brave. Anything else? Yes!"),
                         ["Opening Brave.", "Anything else?", "Yes!"])
        self.assertEqual(split_sentences("..."), [])


class SpeakerTests(unittest.TestCase):
    def make(self):
        engine, player = FakeEngine(), FakePlayer()
        return Speaker("af_heart*0.5 + bf_emma*0.5", engine=engine, player=player), engine, player

    def test_blend_mixes_styles(self):
        speaker, _, _ = self.make()
        self.assertAlmostEqual(float(speaker.style[0, 0]), 2.5)  # 0.5*1 + 0.5*4

    def test_unknown_voice(self):
        speaker, _, _ = self.make()
        with self.assertRaises(ValueError):
            speaker.set_voice("af_nobody")

    def test_says_each_sentence_in_order(self):
        speaker, engine, player = self.make()
        speaker.say("Hi, I'm Adeola. Opening Brave.")
        speaker.wait_until_done()
        self.assertEqual(engine.spoken, ["Hi, I'm Ahdeh-ohla.", "Opening Brave."])
        self.assertEqual(len(player.played), 2)

    def test_stop_cancels_queued_speech(self):
        speaker, engine, player = self.make()
        player.gate.clear()                  # sound card "busy" playing sentence one
        speaker.say("One. Two. Three.")
        speaker.say("Never said.")
        for _ in range(200):                 # wait until sentence one is playing
            if player.played:
                break
            threading.Event().wait(0.01)
        speaker.stop()
        player.gate.set()
        speaker.wait_until_done()
        self.assertEqual(len(player.played), 1)
        self.assertNotIn("Never said.", engine.spoken)
        self.assertGreaterEqual(player.stops, 1)


if __name__ == "__main__":
    unittest.main()
