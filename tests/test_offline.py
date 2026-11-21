"""Offline hardening and connection watcher tests."""

import contextlib
import pathlib
import socket
import unittest
from unittest import mock

from app import config
from app.audio.stt import Transcriber, hint_prompt, load_whisper
from app.netwatch import NEEDS_INTERNET, ConnectionLog


class OfflineTests(unittest.TestCase):
    def test_whisper_loads_offline_first(self):
        seen = []

        class FakeWM:
            def __init__(self, name, **kw):
                seen.append(kw.get("local_files_only", False))

        with mock.patch("faster_whisper.WhisperModel", FakeWM):
            load_whisper("base.en")
        self.assertEqual(seen, [True])

    def test_whisper_downloads_when_not_cached(self):
        seen = []

        class FakeWM:
            def __init__(self, name, **kw):
                local_only = kw.get("local_files_only", False)
                seen.append(local_only)
                if local_only:
                    raise OSError("not found in local cache")

        with mock.patch("faster_whisper.WhisperModel", FakeWM):
            load_whisper("base.en")
        self.assertEqual(seen, [True, False])

    def test_transcriber_uses_load_whisper(self):
        with mock.patch("app.audio.stt.load_whisper", return_value="fake-model") as m:
            t = Transcriber("base.en")
        m.assert_called_once_with("base.en")
        self.assertEqual(t.model, "fake-model")

    def test_hint_prompt_includes_domain_vocabulary(self):
        prompt = hint_prompt()
        self.assertIn(config.ASSISTANT_NAME, prompt)
        self.assertIn("Brave", prompt)
        for phrase in ("quick brain", "smart brain", "coder brain", "local brain", "Claude", "calm voice"):
            self.assertIn(phrase, prompt)

    def test_loopback_is_not_external(self):
        with socket.create_server(("127.0.0.1", 0)) as srv:
            port = srv.getsockname()[1]
            with ConnectionLog() as log:
                with contextlib.suppress(OSError):
                    socket.create_connection(("127.0.0.1", port), timeout=1).close()
        self.assertEqual(log.external, [])

    def test_external_attempt_is_logged_even_if_it_fails(self):
        with ConnectionLog() as log:
            with contextlib.suppress(OSError):
                socket.create_connection(("203.0.113.1", 80), timeout=0.05)  # TEST-NET, unroutable
        self.assertEqual(log.external, ["203.0.113.1"])

    def test_hostname_from_getaddrinfo_and_deduplication(self):
        real_getaddrinfo = socket.getaddrinfo

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "huggingface.co":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.2", port))]
            return real_getaddrinfo(host, port, *args, **kwargs)

        with mock.patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            with ConnectionLog() as log:
                with contextlib.suppress(OSError):
                    socket.create_connection(("huggingface.co", 443), timeout=0.05)
                with contextlib.suppress(OSError):
                    socket.create_connection(("203.0.113.2", 443), timeout=0.05)
        self.assertEqual(log.external, ["huggingface.co"])

    def test_patch_is_removed_on_exit(self):
        before = socket.socket.connect
        before_gai = socket.getaddrinfo
        with ConnectionLog():
            pass
        self.assertIs(socket.socket.connect, before)
        self.assertIs(socket.getaddrinfo, before_gai)

    def test_needs_internet_manifest(self):
        self.assertEqual(
            NEEDS_INTERNET,
            {
                "web_search": "opens Google in your browser",
                "open_url": "opens websites in your browser",
                "first run": "downloads Whisper, Kokoro and wake-word models once",
                "cloud brain": "only when you switch to a cloud brain (Gemini, Groq, OpenRouter or Claude)",
            },
        )

    def test_requirements_are_exact_pinned(self):
        req_text = pathlib.Path(__file__).resolve().parents[1].joinpath("requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn(">=", req_text)


if __name__ == "__main__":
    unittest.main()
