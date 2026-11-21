"""Tests for Adeola HTTP API server (Phase 5)."""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import base64  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
import wave  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402
from unittest.mock import patch  # noqa: E402

import numpy as np  # noqa: E402

from app import config  # noqa: E402
from app.audio.tts import Speaker  # noqa: E402
from app.brain.router import Router  # noqa: E402
from app.memory.notes import NotesIndex  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.server import (  # noqa: E402
    AdeolaServer,
    client_access,
    decode_wav_16k,
    load_or_create_token,
    tailscale_ip,
)
from app.tools.applications import App, AppIndex  # noqa: E402
from app.tools.projects import Projects  # noqa: E402
from app.tools.registry import Toolbox  # noqa: E402
from tests.test_router import FAKE_APPS, FakeBrain  # noqa: E402
from tests.test_tts import FakeEngine, FakePlayer  # noqa: E402


class ServerApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)
        self.db_path = tmp_path / "test.db"
        self.notes_dir = tmp_path / "notes"
        self.captures_dir = tmp_path / "captures"
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        (self.notes_dir / "Kotlin Flows.md").write_text(
            "# Kotlin Flows\n\nStateFlow and SharedFlow are hot streams in Kotlin coroutines.\n",
            encoding="utf-8",
        )
        self.memory = Memory(self.db_path)
        self.projects = Projects(self.memory, roots=[])
        self.notes = NotesIndex([self.notes_dir], captures_dir=self.captures_dir)
        self.brain = FakeBrain(reply="Hello from FakeBrain.")
        self.toolbox = Toolbox(FAKE_APPS, self.memory, self.projects, self.notes)
        self.router = Router(self.toolbox, self.brain, self.notes)

    def tearDown(self):
        self.memory.close()
        self._tmp.cleanup()

    def _json_call(self, server: AdeolaServer, method: str, path: str,
                   payload: dict | None = None, headers: dict | None = None,
                   client_ip: str = "127.0.0.1") -> tuple[int, dict]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        status, ctype, raw = server.dispatch(method, path, headers=headers or {}, body=body,
                                             client_ip=client_ip)
        self.assertTrue(ctype.startswith("application/json"), ctype)
        return status, json.loads(raw.decode("utf-8"))

    def test_config_defaults_present(self):
        self.assertEqual(config.SERVER_HOST, "0.0.0.0")
        self.assertFalse(config.SERVER_ALLOW_LAN)
        self.assertIsInstance(config.SERVER_PORT, int)
        self.assertIsInstance(config.SERVER_TOKEN, str)

    def test_status_endpoint(self):
        server = AdeolaServer(self.router)
        status, data = self._json_call(server, "GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["assistant"], config.ASSISTANT_NAME)
        self.assertEqual(data["brain"], "smart")
        self.assertEqual(data["model"], config.MODEL)
        self.assertEqual(data["voice"], "default")
        self.assertEqual(data["counts"]["apps"], len(FAKE_APPS.apps))
        self.assertEqual(data["counts"]["notes"], 1)
        self.assertEqual(data["counts"]["facts"], 0)

    def test_chat_endpoint_fast_and_speaker_options(self):
        spoken = []
        fake_speaker = NS(
            voice_name=lambda: "warm",
            stop=lambda: spoken.append("STOP"),
            say=lambda txt: spoken.append(txt),
            synthesize_wav=lambda txt: b"RIFFfakewav",
        )
        server = AdeolaServer(self.router, speaker=fake_speaker)

        status, data = self._json_call(
            server,
            "POST",
            "/api/chat",
            {"text": "open Brave", "speak_on_pc": True, "return_audio": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["reply"].startswith("Opening Brave."), data["reply"])
        self.assertEqual(data["source"], "fast")
        self.assertEqual(data["calls"], [{"tool": "open_app", "args": {"name": "brave"}}])
        self.assertEqual(spoken, ["STOP", data["reply"]])
        self.assertEqual(base64.b64decode(data["audio_wav"]), b"RIFFfakewav")

        # Missing text returns 400
        status_bad, err = self._json_call(server, "POST", "/api/chat", {"text": "   "})
        self.assertEqual(status_bad, 400)
        self.assertIn("error", err)

    def test_notes_search_endpoint(self):
        server = AdeolaServer(self.router)
        status, data = self._json_call(server, "GET", "/api/notes?q=Kotlin")
        self.assertEqual(status, 200)
        self.assertEqual(data["query"], "Kotlin")
        self.assertEqual(len(data["notes"]), 1)
        self.assertEqual(data["notes"][0]["title"], "Kotlin Flows")
        self.assertIn("StateFlow", data["notes"][0]["excerpt"])

        status_empty, data_empty = self._json_call(server, "GET", "/api/notes")
        self.assertEqual(status_empty, 200)
        self.assertEqual(data_empty["notes"], [])

    def test_remember_endpoint(self):
        server = AdeolaServer(self.router)
        status, data = self._json_call(
            server, "POST", "/api/remember", {"text": "My dentist appointment is Friday"}
        )
        self.assertEqual(status, 200)
        self.assertIn("remember", data["reply"].lower())
        self.assertEqual(len(self.memory.facts()), 1)

        status_bad, _ = self._json_call(server, "POST", "/api/remember", {"text": ""})
        self.assertEqual(status_bad, 400)

    def test_token_authentication(self):
        server = AdeolaServer(self.router, token="secret123")
        phone = "100.101.102.103"

        status, _ = self._json_call(server, "GET", "/api/status", client_ip=phone)
        self.assertEqual(status, 401)
        status, _ = self._json_call(
            server, "GET", "/api/status", headers={"Authorization": "Bearer wrong"}, client_ip=phone
        )
        self.assertEqual(status, 401)
        status, _ = self._json_call(
            server, "GET", "/api/status", headers={"Authorization": "Bearer secret123"}, client_ip=phone
        )
        self.assertEqual(status, 200)
        status, _ = self._json_call(
            server, "GET", "/api/status", headers={"x-adeola-token": "secret123"}, client_ip=phone
        )
        self.assertEqual(status, 200)
        status, _ = self._json_call(server, "GET", "/api/status?token=secret123", client_ip=phone)
        self.assertEqual(status, 200)

    def test_loopback_is_token_optional_but_rejects_a_wrong_token(self):
        server = AdeolaServer(self.router, token="secret123")
        status, _ = self._json_call(server, "GET", "/api/status")
        self.assertEqual(status, 200)
        status, _ = self._json_call(server, "GET", "/api/status?token=wrong")
        self.assertEqual(status, 401)
        status, _ = self._json_call(server, "GET", "/api/status", client_ip="::1")
        self.assertEqual(status, 200)

    def test_remote_peer_without_configured_token_is_refused(self):
        server = AdeolaServer(self.router, token="")
        status, _ = self._json_call(server, "GET", "/api/status", client_ip="100.101.102.103")
        self.assertEqual(status, 401)
        status, _ = self._json_call(server, "POST", "/api/chat", {"text": "lock my pc"},
                                    client_ip="100.101.102.103")
        self.assertEqual(status, 401)

    def test_lan_peer_is_refused_even_with_token(self):
        server = AdeolaServer(self.router, token="secret123")
        for ip in ("192.168.1.42", "10.0.0.5", "::ffff:192.168.1.42"):
            status, _ = self._json_call(server, "GET", "/api/status?token=secret123", client_ip=ip)
            self.assertEqual(status, 403, ip)
        status, _, _ = server.dispatch("GET", "/hud", client_ip="192.168.1.42")
        self.assertEqual(status, 403)

    def test_lan_peer_allowed_with_opt_in_and_token(self):
        server = AdeolaServer(self.router, token="secret123")
        with patch.object(config, "SERVER_ALLOW_LAN", True):
            status, _ = self._json_call(server, "GET", "/api/status?token=secret123",
                                        client_ip="192.168.1.42")
            self.assertEqual(status, 200)
            status, _ = self._json_call(server, "GET", "/api/status", client_ip="192.168.1.42")
            self.assertEqual(status, 401)

    def test_client_access_classification(self):
        self.assertEqual(client_access("127.0.0.1"), "loopback")
        self.assertEqual(client_access("::1"), "loopback")
        self.assertEqual(client_access("::ffff:127.0.0.1"), "loopback")
        self.assertEqual(client_access("100.64.0.1"), "tailscale")
        self.assertEqual(client_access("100.128.0.1"), "lan")
        self.assertEqual(client_access("192.168.1.42"), "lan")
        self.assertEqual(client_access("not-an-ip"), "lan")

    def test_token_is_generated_once_and_persisted(self):
        path = Path(self._tmp.name) / "server_token"
        first = load_or_create_token(path)
        self.assertGreaterEqual(len(first), 24)
        self.assertEqual(load_or_create_token(path), first)

    def test_start_generates_token_for_non_loopback_bind(self):
        server = AdeolaServer(self.router, host="0.0.0.0", port=0, token="")
        with patch("app.server.load_or_create_token", return_value="generated-token"):
            server.start()
        try:
            self.assertEqual(server.token, "generated-token")
        finally:
            server.stop()

    def test_status_reports_is_cloud(self):
        server = AdeolaServer(self.router)
        _, data = self._json_call(server, "GET", "/api/status")
        self.assertIs(data["is_cloud"], False)
        _, tele = self._json_call(server, "GET", "/api/telemetry")
        self.assertIs(tele["status"]["is_cloud"], False)

    def test_tailscale_ip_detection(self):
        fake_addrs = [
            (2, 1, 6, "", ("192.168.1.42", 0)),
            (2, 1, 6, "", ("100.101.102.103", 0)),
        ]
        with patch("app.server.socket.getaddrinfo", return_value=fake_addrs):
            self.assertEqual(tailscale_ip(), "100.101.102.103")

        non_ts_addrs = [
            (2, 1, 6, "", ("192.168.1.42", 0)),
            (2, 1, 6, "", ("100.128.0.1", 0)),  # outside 100.64.0.0/10
        ]
        with patch("app.server.socket.getaddrinfo", return_value=non_ts_addrs):
            self.assertIsNone(tailscale_ip())

        with patch("app.server.socket.getaddrinfo", side_effect=OSError("no net")):
            self.assertIsNone(tailscale_ip())

    @staticmethod
    def _make_pcm_wav(samples: np.ndarray, rate: int = 16000, channels: int = 1) -> bytes:
        buf = io.BytesIO()
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()

    def test_speaker_synthesize_wav(self):
        engine = FakeEngine()
        speaker = Speaker(engine=engine, player=FakePlayer(), cache_dir=None)
        wav_bytes = speaker.synthesize_wav("Opening Brave. Ready!")
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 24000)
            self.assertGreater(wf.getnframes(), 480)  # 2 chunks of 240 + sentence pause
        self.assertEqual(engine.spoken, ["Opening Brave.", "Ready!"])

        # Empty text returns 0.1s silence at 24000 Hz
        empty_wav = speaker.synthesize_wav("   ")
        with wave.open(io.BytesIO(empty_wav), "rb") as wf:
            self.assertEqual(wf.getframerate(), 24000)
            self.assertEqual(wf.getnframes(), 2400)

    def test_decode_wav_16k_mono_and_stereo_resample(self):
        # 48 kHz stereo sine wave -> 16 kHz mono float32
        t = np.linspace(0.0, 0.1, 4800, endpoint=False, dtype=np.float32)
        sine = 0.5 * np.sin(2.0 * np.pi * 440.0 * t)
        stereo = np.column_stack([sine, sine]).reshape(-1)
        wav_bytes = self._make_pcm_wav(stereo, rate=48000, channels=2)
        audio = decode_wav_16k(wav_bytes)
        self.assertEqual(audio.dtype, np.float32)
        self.assertEqual(len(audio), 1600)
        self.assertAlmostEqual(float(np.max(np.abs(audio))), 0.5, places=1)

    def test_voice_endpoint_valid_empty_and_missing_transcriber(self):
        speaker = Speaker(engine=FakeEngine(), player=FakePlayer(), cache_dir=None)

        # 1. Missing transcriber -> 503
        server_no_stt = AdeolaServer(self.router, speaker=speaker, transcriber=None)
        wav_in = self._make_pcm_wav(np.zeros(1600, dtype=np.float32), rate=16000)
        b64_in = base64.b64encode(wav_in).decode("ascii")
        status, err = self._json_call(server_no_stt, "POST", "/api/voice", {"audio_wav": b64_in})
        self.assertEqual(status, 503)
        self.assertIn("Speech recognition", err["error"])

        # 2. Valid speech -> transcribed, routed, and synthesized WAV returned
        seen_audio = []

        class FakeTranscriber:
            def __init__(self, text: str):
                self.text = text

            def transcribe(self, audio: np.ndarray) -> str:
                seen_audio.append(audio)
                return self.text

        server = AdeolaServer(
            self.router, speaker=speaker, transcriber=FakeTranscriber("open Brave")
        )
        status, data = self._json_call(
            server,
            "POST",
            "/api/voice",
            {"audio_wav": b64_in, "speak_on_pc": False, "return_audio": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["transcript"], "open Brave")
        self.assertTrue(data["reply"].startswith("Opening Brave."), data["reply"])
        self.assertEqual(data["source"], "fast")
        self.assertEqual(data["calls"], [{"tool": "open_app", "args": {"name": "brave"}}])
        self.assertTrue(base64.b64decode(data["audio_wav"]).startswith(b"RIFF"))
        self.assertEqual(len(seen_audio), 1)
        self.assertEqual(len(seen_audio[0]), 1600)

        # 3. Empty transcription -> friendly missed reply
        server_empty = AdeolaServer(
            self.router, speaker=speaker, transcriber=FakeTranscriber("   ")
        )
        status_empty, data_empty = self._json_call(
            server_empty, "POST", "/api/voice", {"audio_wav": b64_in, "return_audio": True}
        )
        self.assertEqual(status_empty, 200)
        self.assertEqual(data_empty["transcript"], "")
        self.assertEqual(data_empty["reply"], "Sorry, I didn't catch that.")
        self.assertEqual(data_empty["source"], "voice")
        self.assertTrue(base64.b64decode(data_empty["audio_wav"]).startswith(b"RIFF"))

        # 4. Invalid WAV -> 400
        status_bad, err_bad = self._json_call(
            server, "POST", "/api/voice", {"audio_wav": base64.b64encode(b"not-a-wav").decode("ascii")}
        )
        self.assertEqual(status_bad, 400)
        self.assertIn("Invalid WAV", err_bad["error"])

    def test_companion_ui_and_manifest_endpoints(self):
        server = AdeolaServer(self.router)

        status_html, ctype_html, raw_html = server.dispatch("GET", "/")
        self.assertEqual(status_html, 200)
        self.assertTrue(ctype_html.startswith("text/html"), ctype_html)
        html = raw_html.decode("utf-8")
        self.assertIn("status-badge", html)
        self.assertIn("Hold to Talk", html)
        self.assertIn("/api/voice", html)
        self.assertIn("/api/chat", html)
        for chip in ("What's playing?", "Pause", "Resume", "Mute", "What do you remember?"):
            self.assertIn(chip, html)
        for shelf in ("Quick", "Smart", "Coder"):
            self.assertIn(shelf, html)

        status_mf, ctype_mf, raw_mf = server.dispatch("GET", "/manifest.json")
        self.assertEqual(status_mf, 200)
        self.assertTrue(ctype_mf.startswith("application/manifest+json"), ctype_mf)
        manifest = json.loads(raw_mf.decode("utf-8"))
        self.assertEqual(manifest["name"], "Adeola")
        self.assertEqual(manifest["display"], "standalone")

    def test_main_start_server(self):
        import contextlib
        import threading

        import main

        buf = io.StringIO()
        lock = threading.Lock()
        with (
            patch.object(AdeolaServer, "start") as mock_start,
            patch.object(AdeolaServer, "stop") as mock_stop,
            patch("app.server.tailscale_ip", return_value="100.88.99.11"),
            contextlib.redirect_stdout(buf),
        ):
            srv = main.start_server(self.router, lock, port=8765)
            self.assertIsInstance(srv, AdeolaServer)
            self.assertEqual(srv.port, 8765)
            self.assertIs(srv.lock, lock)
            mock_start.assert_called_once()
            srv.stop()
            mock_stop.assert_called_once()

        out = buf.getvalue()
        self.assertIn("http://127.0.0.1:8765", out)
        self.assertIn("http://100.88.99.11:8765", out)

    def test_event_bus_and_pipeline_emission(self):
        from app.events import BUS, EventBus

        bus = EventBus(min_level_interval=0.0)
        q = bus.subscribe()
        bus.emit("state", state="listening", level=0.42, wake_score=0.81)
        evt = q.get_nowait()
        self.assertEqual(evt["type"], "state")
        self.assertEqual(evt["state"], "listening")
        self.assertAlmostEqual(evt["level"], 0.42, places=2)
        self.assertAlmostEqual(evt["wake_score"], 0.81, places=2)

        snap = bus.snapshot()
        self.assertEqual(snap["state"], "listening")
        self.assertAlmostEqual(snap["level"], 0.42, places=2)

        # numpy float32 values from openWakeWord must serialize cleanly to JSON
        bus.emit("level", state="idle", wake_score=np.float32(0.37), level=np.float32(0.12))
        np_evt = q.get_nowait()
        encoded = json.dumps(np_evt)
        self.assertIn("0.37", encoded)
        bus.unsubscribe(q)

        # Router.handle emits turn event to global BUS
        q_global = BUS.subscribe()
        try:
            res = self.router.handle("open Brave")
            self.assertTrue(res.reply.startswith("Opening Brave."))
            events = []
            while not q_global.empty():
                events.append(q_global.get_nowait())
            types = [e["type"] for e in events]
            self.assertIn("turn", types)
            turn_evt = next(e for e in events if e["type"] == "turn")
            self.assertEqual(turn_evt["source"], "fast")
            self.assertTrue(turn_evt["reply"].startswith("Opening Brave."))
        finally:
            BUS.unsubscribe(q_global)

    def test_hud_and_telemetry_and_sse_endpoints(self):
        # Record a couple of turns so telemetry has rich data
        self.router.handle("open Brave")
        self.router.handle("remember that my project deadline is Monday")
        server = AdeolaServer(self.router)

        # 1. GET /hud returns the WebGL presence + radar dashboard HTML
        status_hud, ctype_hud, raw_hud = server.dispatch("GET", "/hud")
        self.assertEqual(status_hud, 200)
        self.assertTrue(ctype_hud.startswith("text/html"), ctype_hud)
        hud_html = raw_hud.decode("utf-8")
        self.assertIn("orb-canvas", hud_html)
        self.assertIn("/api/telemetry", hud_html)
        self.assertIn("/api/events", hud_html)
        self.assertIn("FAST-PATH EFFICIENCY", hud_html)
        self.assertIn("PIPELINE SIGNALS", hud_html)

        # 2. GET /api/telemetry returns aggregated metrics, radar axes, insight, traces, facts, notes
        status_tel, tel = self._json_call(server, "GET", "/api/telemetry")
        self.assertEqual(status_tel, 200)
        self.assertEqual(tel["status"]["assistant"], config.ASSISTANT_NAME)
        self.assertGreaterEqual(tel["metrics"]["total_turns"], 2)
        self.assertEqual(len(tel["metrics"]["axes"]), 6)
        axis_names = [a["key"] for a in tel["metrics"]["axes"]]
        self.assertEqual(axis_names, ["wake", "hear", "think", "tools", "speak", "memory"])
        self.assertIn("summary", tel["insight"])
        self.assertIn("friction", tel["insight"])
        self.assertGreaterEqual(len(tel["traces"]), 2)
        self.assertEqual(len(tel["facts"]), 1)
        self.assertGreaterEqual(len(tel["recent_notes"]), 1)

        # 3. GET /api/events via dispatch returns initial SSE snapshot event
        status_sse, ctype_sse, raw_sse = server.dispatch("GET", "/api/events")
        self.assertEqual(status_sse, 200)
        self.assertTrue(ctype_sse.startswith("text/event-stream"), ctype_sse)
        sse_text = raw_sse.decode("utf-8")
        self.assertIn("data: ", sse_text)

        # 4. GET /api/avatar returns adeola-v1.avatar.json
        status_av, av = self._json_call(server, "GET", "/api/avatar")
        self.assertEqual(status_av, 200)
        self.assertEqual(av["name"], "Adeola v1")
        self.assertIn("expressions", av)


if __name__ == "__main__":
    unittest.main()
