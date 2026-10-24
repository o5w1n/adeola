"""Media control tests with fake Windows media sessions. No Windows needed.

Run from the project folder:  python -m unittest -v
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import unittest  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402
from unittest import mock  # noqa: E402

from app import config  # noqa: E402
from app.tools import media as M  # noqa: E402


class FakeSession:
    def __init__(self, app, status, title="", artist=""):
        self.source_app_user_model_id = app
        self.status, self.title, self.artist = status, title, artist
        self.calls = []

    def get_playback_info(self):
        return NS(playback_status=self.status)

    def _op(name, result=True):
        async def op(self):
            self.calls.append(name)
            return result
        return op

    try_pause_async = _op("pause")
    try_play_async = _op("play")
    try_toggle_play_pause_async = _op("toggle")
    try_skip_next_async = _op("next")
    try_skip_previous_async = _op("previous")

    async def try_get_media_properties_async(self):
        return NS(title=self.title, artist=self.artist)


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.spotify = FakeSession("Brave.ABC123", M._Status.PLAYING, "Calm Down", "Rema")
        self.vlc = FakeSession("VideoLAN.VLC", M._Status.PAUSED, "Lecture 4")
        self.m = M.Media()
        self.m._sessions = lambda: (None, [self.vlc, self.spotify], self.vlc)
        patches = [mock.patch.object(M.sys, "platform", "win32"),
                   mock.patch.object(config, "DRY_RUN", False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_pause_targets_the_playing_session(self):
        self.assertEqual(self.m.playback("pause"), "Paused.")
        self.assertEqual(self.spotify.calls, ["pause"])
        self.assertEqual(self.vlc.calls, [])

    def test_play_targets_the_paused_session(self):
        self.spotify.status = M._Status.PAUSED
        self.m._sessions = lambda: (None, [self.spotify, self.vlc], self.spotify)
        self.assertEqual(self.m.playback("play"), "Playing.")
        self.assertEqual(self.spotify.calls, ["play"])

    def test_next_and_now_playing(self):
        self.assertEqual(self.m.playback("next"), "Next track.")
        self.assertEqual(self.m.now_playing(), "Playing: Calm Down by Rema in Brave.")

    def test_nothing_playing(self):
        self.m._sessions = lambda: (None, [], None)
        self.assertEqual(self.m.playback("pause"), "Nothing is playing right now.")
        self.assertEqual(self.m.now_playing(), "Nothing is playing right now.")

    def test_player_refuses(self):
        async def nope(_self):
            return False
        self.spotify.try_pause_async = nope.__get__(self.spotify)
        self.assertIn("didn't respond", self.m.playback("pause"))

    def test_parse(self):
        cases = {"pause": ("pause", None), "stop the music": ("pause", None), "resume": ("play", None),
                 "next song": ("next", None), "skip": ("next", None), "go back a song": ("previous", None),
                 "what's playing": ("now_playing", None), "who sings this": ("now_playing", None),
                 "volume up": ("volume_up", None), "turn it down": ("volume_down", None),
                 "mute": ("mute", None), "unmute": ("unmute", None),
                 "set volume to 30": ("volume_set", 30), "volume 150": ("volume_set", 100)}
        for text, want in cases.items():
            self.assertEqual(M.parse(text), want, text)

    def test_friendly_names(self):
        self.assertEqual(M._friendly("Spotify.exe"), "Spotify")
        self.assertEqual(M._friendly("308046B0AF4A39CB"), "Firefox")
        self.assertEqual(M._friendly("Something.Else"), "")


if __name__ == "__main__":
    unittest.main()
