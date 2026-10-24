"""Speaker tests with a fake Kokoro and a fake sound card. No model download needed.

Run from the project folder:  python -m unittest -v
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import threading  # noqa: E402
import unittest   # noqa: E402

import numpy as np  # noqa: E402

from app import config  # noqa: E402

from app.audio.tts import (AudioCache, Speaker, parse_blend, speech_chunks,  # noqa: E402
                           speech_text, split_sentences)


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
        said = config.SPOKEN_FORMS.get("Adeola", "Adeola")   # whatever pronunciation is configured
        self.assertEqual(speech_text("I'm Adeola 🎙"), f"I'm {said}")
        self.assertEqual(speech_text("adeola, hi"), f"{said}, hi")
        self.assertEqual(speech_text("Tip: Ctrl+Shift+T brings back a tab."),
                         "Tip: Control Shift T brings back a tab.")
        self.assertEqual(speech_text("Windows+E opens it."), "Windows E opens it.")

    def test_split_sentences(self):
        self.assertEqual(split_sentences("Opening Brave. Anything else? Yes!"),
                         ["Opening Brave.", "Anything else?", "Yes!"])
        self.assertEqual(split_sentences("..."), [])


class SpeakerTests(unittest.TestCase):
    def make(self):
        engine, player = FakeEngine(), FakePlayer()
        return (Speaker("af_heart*0.5 + bf_emma*0.5", engine=engine, player=player, cache_dir=None),
                engine, player)

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
        said = config.SPOKEN_FORMS.get("Adeola", "Adeola")
        self.assertEqual(engine.spoken, [f"Hi, I'm {said}.", "Opening Brave."])
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


class CacheTests(unittest.TestCase):
    def test_repeat_is_not_regenerated_and_survives_restart(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            engine, player = FakeEngine(), FakePlayer()
            sp = Speaker("af_heart", engine=engine, player=player, cache_dir=Path(d))
            for _ in range(3):
                sp.say("Opening Brave.")
                sp.wait_until_done()
            self.assertEqual(engine.spoken, ["Opening Brave."])          # generated once
            self.assertEqual(len(player.played), 3)                      # played every time
            engine2 = FakeEngine()
            sp2 = Speaker("af_heart", engine=engine2, player=FakePlayer(), cache_dir=Path(d))
            sp2.say("Opening Brave.")
            sp2.wait_until_done()
            self.assertEqual(engine2.spoken, [])                         # read from disk
            sp3 = Speaker("bf_emma", engine=FakeEngine(), player=FakePlayer(), cache_dir=Path(d))
            sp3.say("Opening Brave.")
            sp3.wait_until_done()
            self.assertEqual(sp3.engine.spoken, ["Opening Brave."])      # new voice, new audio

    def test_numbers_not_cached(self):
        self.assertFalse(AudioCache.cacheable("It's 16:20 on Saturday."))
        self.assertTrue(AudioCache.cacheable("Opening Brave."))

    def test_prewarm_runs_when_idle(self):
        engine = FakeEngine()
        sp = Speaker("af_heart", engine=engine, player=FakePlayer(), cache_dir=None)
        sp.prewarm(["Opening Brave.", "Paused."], idle_seconds=0).join(2)
        self.assertEqual(engine.spoken, ["Opening Brave.", "Paused."])
        sp.say("Paused.")
        sp.wait_until_done()
        self.assertEqual(engine.spoken, ["Opening Brave.", "Paused."])  # no regeneration

    def test_chunks(self):
        long = "Take a few slow breaths, arrive a little early, and tell them you're nervous."
        self.assertEqual(speech_chunks(long), ["Take a few slow breaths,", "arrive a little early,",
                                               "and tell them you're nervous."])
        self.assertEqual(speech_chunks("Opening Brave. Tip: Control L jumps to the bar."),
                         ["Opening Brave.", "Tip: Control L jumps to the bar."])
        self.assertEqual(speech_chunks("Yes, sure, I can do that for you right now, no problem at all."),
                         ["Yes, sure, I can do that for you right now,", "no problem at all."])


if __name__ == "__main__":
    unittest.main()
