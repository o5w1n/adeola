"""Lightweight HTTP API server for local & Tailscale access (Phase 5)."""

from __future__ import annotations

import base64
import hmac
import io
import ipaddress
import json
import queue
import secrets
import socket
import threading
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from app import config
from app.brain.remote import RemoteBrain
from app.brain.traces import TraceAnalyzer
from app.events import BUS
from app.skills.loader import discover_skills, save_vault_output, suggest_skills_from_traces

_HUD_PATH = Path(__file__).resolve().parent / "ui" / "hud.html"
_AVATAR_PATH = Path(__file__).resolve().parent.parent / "adeola-v1.avatar.json"

_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
_TOKEN_PATH = config.DATA_DIR / "server_token"

MANIFEST_JSON = {
    "name": "Adeola",
    "short_name": "Adeola",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0d1117",
    "theme_color": "#0d1117",
}

COMPANION_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
  <meta name="theme-color" content="#0d1117" />
  <link rel="manifest" href="/manifest.json" />
  <title>Adeola Companion</title>
  <style>
    :root {
      --bg: #0d1117;
      --panel: #161b22;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #2f81f7;
      --user-bg: #1f6feb;
      --bot-bg: #21262d;
      --danger: #f85149;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      height: 100dvh;
      overflow: hidden;
    }
    header {
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      padding: 10px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      flex-wrap: wrap;
    }
    .brand { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 16px; }
    #status-badge {
      font-size: 12px;
      color: var(--muted);
      background: var(--bg);
      border: 1px solid var(--border);
      padding: 3px 8px;
      border-radius: 999px;
    }
    .shelf { display: flex; gap: 6px; }
    .shelf button, .chips button {
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      padding: 5px 10px;
      border-radius: 999px;
      font-size: 12px;
      cursor: pointer;
    }
    .shelf button.active { border-color: var(--accent); color: var(--accent); }
    #feed {
      flex: 1;
      overflow-y: auto;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .msg {
      max-width: 85%;
      padding: 10px 13px;
      border-radius: 14px;
      line-height: 1.4;
      font-size: 15px;
      word-wrap: break-word;
    }
    .msg.user { align-self: flex-end; background: var(--user-bg); color: #fff; border-bottom-right-radius: 4px; }
    .msg.bot { align-self: flex-start; background: var(--bot-bg); border: 1px solid var(--border); border-bottom-left-radius: 4px; }
    .meta { font-size: 11px; color: var(--muted); margin-top: 4px; }
    .chips {
      display: flex;
      gap: 6px;
      overflow-x: auto;
      padding: 8px 12px;
      border-top: 1px solid var(--border);
      background: var(--panel);
      scrollbar-width: none;
    }
    .chips button { white-space: nowrap; flex-shrink: 0; }
    .composer {
      display: flex;
      gap: 8px;
      padding: 10px 12px;
      background: var(--panel);
      border-top: 1px solid var(--border);
      align-items: center;
    }
    #text-input {
      flex: 1;
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 10px 14px;
      font-size: 15px;
      outline: none;
    }
    .btn {
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 20px;
      padding: 10px 14px;
      font-weight: 600;
      font-size: 14px;
      cursor: pointer;
      user-select: none;
      -webkit-user-select: none;
    }
    #ptt-btn.recording { background: var(--danger); }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span>Adeola</span>
      <span id="status-badge">loading...</span>
    </div>
    <div class="shelf">
      <button type="button" onclick="switchBrain('Quick')">Quick</button>
      <button type="button" onclick="switchBrain('Smart')">Smart</button>
      <button type="button" onclick="switchBrain('Coder')">Coder</button>
    </div>
  </header>

  <main id="feed"></main>

  <div class="chips">
    <button type="button" onclick="sendQuick(&quot;What's playing?&quot;)">What's playing?</button>
    <button type="button" onclick="sendQuick('Pause')">Pause</button>
    <button type="button" onclick="sendQuick('Resume')">Resume</button>
    <button type="button" onclick="sendQuick('Mute')">Mute</button>
    <button type="button" onclick="sendQuick('What do you remember?')">What do you remember?</button>
  </div>

  <form class="composer" onsubmit="onSubmit(event)">
    <input id="text-input" type="text" placeholder="Ask Adeola..." autocomplete="off" />
    <button class="btn" type="submit">Send</button>
    <button class="btn" id="ptt-btn" type="button">Hold to Talk</button>
  </form>
  <audio id="player" hidden></audio>

  <script>
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token") || localStorage.getItem("adeola_token") || "";
    if (params.get("token")) localStorage.setItem("adeola_token", token);

    function apiHeaders() {
      const h = { "Content-Type": "application/json" };
      if (token) h["X-Adeola-Token"] = token;
      return h;
    }

    function addMessage(role, text, metaText, sources) {
      const feed = document.getElementById("feed");
      const div = document.createElement("div");
      div.className = "msg " + role;
      div.textContent = text;
      if (sources && sources.length) {
        const src = document.createElement("div");
        src.className = "meta";
        src.textContent = "(from your notes: " + sources.join(", ") + ")";
        div.appendChild(src);
      }
      if (metaText) {
        const m = document.createElement("div");
        m.className = "meta";
        m.textContent = metaText;
        div.appendChild(m);
      }
      feed.appendChild(div);
      feed.scrollTop = feed.scrollHeight;
    }

    function playWavBase64(b64) {
      if (!b64) return;
      const audio = document.getElementById("player");
      audio.src = "data:audio/wav;base64," + b64;
      audio.play().catch(() => {});
    }

    async function refreshStatus() {
      try {
        const res = await fetch("/api/status", { headers: apiHeaders() });
        if (!res.ok) return;
        const d = await res.json();
        document.getElementById("status-badge").textContent = (d.brain || "local") + " · " + (d.model || "fast");
      } catch (_) {}
    }

    async function sendText(text) {
      if (!text.trim()) return;
      addMessage("user", text);
      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: apiHeaders(),
          body: JSON.stringify({ text, return_audio: true })
        });
        const d = await res.json();
        if (!res.ok) {
          addMessage("bot", d.error || "Request failed");
          return;
        }
        addMessage("bot", d.reply, "[" + d.source + " · " + d.ms + " ms]", d.sources);
        if (d.brain) {
          document.getElementById("status-badge").textContent = d.brain + " · " + (d.model || "");
        }
        playWavBase64(d.audio_wav);
      } catch (e) {
        addMessage("bot", "Network error: " + e.message);
      }
    }

    function onSubmit(e) {
      e.preventDefault();
      const inp = document.getElementById("text-input");
      const val = inp.value;
      inp.value = "";
      sendText(val);
    }

    function sendQuick(cmd) { sendText(cmd); }
    function switchBrain(name) { sendText("switch to your " + name.toLowerCase() + " brain"); }

    // Hold-to-Talk WebAudio 16-bit PCM WAV recorder
    let audioCtx = null, mediaStream = null, processor = null, chunks = [];
    const pttBtn = document.getElementById("ptt-btn");

    function encodeWav(samples, sampleRate) {
      const buffer = new ArrayBuffer(44 + samples.length * 2);
      const view = new DataView(buffer);
      const writeStr = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
      writeStr(0, "RIFF");
      view.setUint32(4, 36 + samples.length * 2, true);
      writeStr(8, "WAVE");
      writeStr(12, "fmt ");
      view.setUint32(16, 16, true);
      view.setUint16(20, 1, true);
      view.setUint16(22, 1, true);
      view.setUint32(24, sampleRate, true);
      view.setUint32(28, sampleRate * 2, true);
      view.setUint16(32, 2, true);
      view.setUint16(34, 16, true);
      writeStr(36, "data");
      view.setUint32(40, samples.length * 2, true);
      let offset = 44;
      for (let i = 0; i < samples.length; i++, offset += 2) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
      }
      const bytes = new Uint8Array(buffer);
      let binary = "";
      for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
      return btoa(binary);
    }

    async function startRecording(e) {
      e.preventDefault();
      if ( audioCtx ) return;
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        const src = audioCtx.createMediaStreamSource(mediaStream);
        processor = audioCtx.createScriptProcessor(4096, 1, 1);
        chunks = [];
        processor.onaudioprocess = (ev) => {
          chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
        };
        src.connect(processor);
        processor.connect(audioCtx.destination);
        pttBtn.classList.add("recording");
        pttBtn.textContent = "Listening...";
      } catch (err) {
        addMessage("bot", "Mic access denied: " + err.message);
      }
    }

    async function stopRecording(e) {
      e.preventDefault();
      if (!audioCtx) return;
      const rate = audioCtx.sampleRate || 16000;
      processor.disconnect();
      mediaStream.getTracks().forEach(t => t.stop());
      await audioCtx.close();
      audioCtx = null;
      pttBtn.classList.remove("recording");
      pttBtn.textContent = "Hold to Talk";

      const total = chunks.reduce((acc, c) => acc + c.length, 0);
      if (total < rate * 0.25) return;
      const merged = new Float32Array(total);
      let off = 0;
      for (const c of chunks) { merged.set(c, off); off += c.length; }
      const b64 = encodeWav(merged, rate);
      try {
        const res = await fetch("/api/voice", {
          method: "POST",
          headers: apiHeaders(),
          body: JSON.stringify({ audio_wav: b64, return_audio: true })
        });
        const d = await res.json();
        if (!res.ok) {
          addMessage("bot", d.error || "Voice error");
          return;
        }
        if (d.transcript) addMessage("user", d.transcript);
        addMessage("bot", d.reply, "[" + d.source + " · " + d.ms + " ms]", d.sources);
        playWavBase64(d.audio_wav);
      } catch (err) {
        addMessage("bot", "Voice error: " + err.message);
      }
    }

    pttBtn.addEventListener("mousedown", startRecording);
    pttBtn.addEventListener("mouseup", stopRecording);
    pttBtn.addEventListener("touchstart", startRecording, { passive: false });
    pttBtn.addEventListener("touchend", stopRecording, { passive: false });

    refreshStatus();
  </script>
</body>
</html>
"""


def decode_wav_16k(wav_bytes: bytes) -> np.ndarray:
    """Decode 16-bit PCM WAV bytes into mono 16 kHz float32 audio in [-1.0, 1.0]."""
    if not wav_bytes:
        raise ValueError("Empty WAV buffer")
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        n_frames = wf.getnframes()
        if sampwidth != 2:
            raise ValueError(f"Unsupported WAV sample width: {sampwidth} bytes (expected 16-bit PCM)")
        raw = wf.readframes(n_frames)
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1)
    if rate != config.SAMPLE_RATE and len(audio) > 0:
        target_len = int(round(len(audio) * config.SAMPLE_RATE / rate))
        if target_len > 0:
            x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
            x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
            audio = np.interp(x_new, x_old, audio).astype(np.float32)
    return audio.astype(np.float32)


def tailscale_ip() -> str | None:
    """Return the first local IPv4 address in the Tailscale CGNAT range (100.64.0.0/10), or None."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return None
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        try:
            if ipaddress.ip_address(ip_str) in _TAILSCALE_NET:
                return ip_str
        except ValueError:
            continue
    return None


def load_or_create_token(path: Path = _TOKEN_PATH) -> str:
    """Return the persisted server token, creating one on first use so the phone's saved copy stays valid."""
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    return token


def client_access(client_ip: str) -> str:
    """Classify a peer as 'loopback', 'tailscale' or 'lan' (anything else)."""
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return "lan"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "loopback"
    if isinstance(ip, ipaddress.IPv4Address) and ip in _TAILSCALE_NET:
        return "tailscale"
    return "lan"


def _json_response(status: int, payload: dict) -> tuple[int, str, bytes]:
    return status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8")


class AdeolaServer:
    """HTTP API server exposing Adeola over localhost and Tailscale."""

    def __init__(
        self,
        router,
        lock: threading.Lock | None = None,
        speaker=None,
        transcriber=None,
        host: str = config.SERVER_HOST,
        port: int = config.SERVER_PORT,
        token: str | None = None,
        verbose: bool = False,
    ):
        self.router = router
        self.lock = lock or threading.Lock()
        self.speaker = speaker if speaker is not None else getattr(router, "speaker", None)
        self.transcriber = transcriber
        self.host = host
        self.port = port
        self.token = config.SERVER_TOKEN if token is None else token
        self.verbose = verbose
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _active_brain_name(self) -> str:
        if hasattr(self.router, "_active_brain_name") and callable(self.router._active_brain_name):
            return self.router._active_brain_name()
        brain = getattr(self.router, "brain", None)
        if brain is None:
            return "none"
        if hasattr(brain, "brain_name") and callable(brain.brain_name):
            return brain.brain_name()
        model = getattr(brain, "model", None)
        for name, tag in config.BRAINS.items():
            if tag == model:
                return name
        return model or "none"

    def _active_voice_name(self) -> str:
        speaker = self.speaker or getattr(self.router, "speaker", None)
        if speaker is None:
            return "default"
        vn = getattr(speaker, "voice_name", "default")
        return vn() if callable(vn) else str(vn)

    def _token_matches(self, candidate: str) -> bool:
        return bool(self.token) and hmac.compare_digest(candidate.encode("utf-8"), self.token.encode("utf-8"))

    @staticmethod
    def _presented_token(headers: dict[str, str], query: dict[str, list[str]]) -> str:
        lower_headers = {k.lower(): v for k, v in (headers or {}).items()}
        auth = lower_headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if header_token := lower_headers.get("x-adeola-token", "").strip():
            return header_token
        q_tokens = query.get("token", [])
        return q_tokens[0] if q_tokens else ""

    def _access_status(self, headers: dict[str, str], query: dict[str, list[str]],
                       client_ip: str) -> int:
        """200 allowed, 401 bad or missing token, 403 network not allowed.
        Loopback is token-optional (a wrong token is still refused); every other peer needs
        the token, and peers outside Tailscale are refused unless ADEOLA_ALLOW_LAN=1."""
        access = client_access(client_ip)
        presented = self._presented_token(headers, query)
        if access == "loopback":
            return 200 if not presented or self._token_matches(presented) else 401
        if access == "lan" and not config.SERVER_ALLOW_LAN:
            return 403
        return 200 if self._token_matches(presented) else 401

    def _authorized(self, headers: dict[str, str], query: dict[str, list[str]],
                    client_ip: str = "127.0.0.1") -> bool:
        return self._access_status(headers, query, client_ip) == 200

    def dispatch(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        client_ip: str = "127.0.0.1",
    ) -> tuple[int, str, bytes]:
        """Pure request dispatcher returning (status_code, content_type, response_bytes)."""
        parsed = urllib.parse.urlparse(path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        hdrs = headers or {}

        access = self._access_status(hdrs, query, client_ip)
        if access == 403:
            return _json_response(403, {"error": "Only this PC and Tailscale devices may connect"})
        if route.startswith("/api/") and access != 200:
            return _json_response(401, {"error": "Unauthorized"})

        upper = method.upper()
        if upper == "GET":
            return self._handle_get(route, query)
        if upper == "POST":
            return self._handle_post(route, body, hdrs)
        return _json_response(405, {"error": f"Method {upper} not allowed"})

    def _handle_get(self, route: str, query: dict[str, list[str]]) -> tuple[int, str, bytes]:
        if route == "/":
            return 200, "text/html; charset=utf-8", COMPANION_HTML.encode("utf-8")

        if route in ("/hud", "/orb"):
            html = _HUD_PATH.read_text(encoding="utf-8") if _HUD_PATH.exists() else COMPANION_HTML
            return 200, "text/html; charset=utf-8", html.encode("utf-8")

        if route == "/manifest.json":
            return (
                200,
                "application/manifest+json; charset=utf-8",
                json.dumps(MANIFEST_JSON).encode("utf-8"),
            )

        if route == "/api/events":
            snap = {"type": "snapshot", **BUS.snapshot()}
            payload = f"data: {json.dumps(snap)}\n\n".encode("utf-8")
            return 200, "text/event-stream; charset=utf-8", payload

        if route == "/api/telemetry":
            return _json_response(200, self._build_telemetry())

        if route == "/api/avatar":
            if not _AVATAR_PATH.exists():
                return _json_response(404, {"error": "Avatar definition not found"})
            return 200, "application/json; charset=utf-8", _AVATAR_PATH.read_bytes()

        if route == "/api/status":
            tb = getattr(self.router, "toolbox", None)
            notes_idx = getattr(self.router, "notes", None)
            brain = getattr(self.router, "brain", None)
            return _json_response(
                200,
                {
                    "assistant": config.ASSISTANT_NAME,
                    "brain": self._active_brain_name(),
                    "model": brain.model if brain else None,
                    "is_cloud": isinstance(brain, RemoteBrain),
                    "voice": self._active_voice_name(),
                    "counts": {
                        "apps": len(tb.apps.apps) if tb and tb.apps else 0,
                        "projects": len(tb.projects.all()) if tb and tb.projects else 0,
                        "notes": len(notes_idx.notes) if notes_idx else 0,
                        "facts": len(tb.memory.facts()) if tb and tb.memory else 0,
                    },
                },
            )

        if route == "/api/skills":
            tb = getattr(self.router, "toolbox", None)
            mem = tb.memory if tb else None
            skills = [s.to_dict() for s in discover_skills()]
            return _json_response(
                200,
                {"skills": skills, "suggested": suggest_skills_from_traces(mem)},
            )

        if route == "/api/notes":
            q = (query.get("q", [""])[0] or "").strip()
            notes_idx = getattr(self.router, "notes", None)
            if notes_idx is None or not q:
                return _json_response(200, {"query": q, "notes": []})
            hits = notes_idx.search(q, limit=5)
            return _json_response(
                200,
                {
                    "query": q,
                    "notes": [
                        {
                            "title": n.title,
                            "folder": n.folder,
                            "excerpt": notes_idx.excerpt(n, q),
                        }
                        for n in hits
                    ],
                },
            )

        return _json_response(404, {"error": "Not found"})

    def _build_telemetry(self) -> dict:
        tb = getattr(self.router, "toolbox", None)
        mem = getattr(tb, "memory", None) if tb else None
        notes_idx = getattr(self.router, "notes", None)
        brain = getattr(self.router, "brain", None)
        policy = getattr(self.router, "policy", None)
        pinned = bool(getattr(policy, "pinned", False)) if policy else False
        auto_brain = bool(getattr(policy, "auto_active", False)) if policy else False
        active_brain = self._active_brain_name()
        live = BUS.snapshot()

        traces = mem.recent_traces(limit=100) if (mem and hasattr(mem, "recent_traces")) else []
        n_turns = len(traces)
        n_fast = sum(1 for t in traces if t.source == "fast")
        fast_rate = int(round((n_fast / n_turns) * 100)) if n_turns else 100
        avg_ms = round(sum(t.ms for t in traces) / n_turns, 1) if n_turns else 0.0
        chron = list(reversed(traces))
        sparkline_ms = [round(t.ms, 1) for t in chron[-30:]]

        route_mix = {"fast": 0, "llm": 0, "agent": 0, "notes": 0, "error": 0}
        for t in traces:
            if t.source == "fast":
                route_mix["fast"] += 1
            elif t.source == "agent":
                route_mix["agent"] += 1
            elif t.source == "notes":
                route_mix["notes"] += 1
            elif t.source in ("error", "none"):
                route_mix["error"] += 1
            else:
                route_mix["llm"] += 1

        n_apps = len(tb.apps.apps) if tb and tb.apps else 0
        n_projects = len(tb.projects.all()) if tb and tb.projects else 0
        n_notes = len(notes_idx.notes) if notes_idx else 0
        facts_list = mem.facts() if mem else []
        n_facts = len(facts_list)

        axes = [
            {
                "key": "wake",
                "label": "WAKE",
                "sub": f"Score {live.get('wake_score', 0.0):.2f}",
                "health": 0.95,
            },
            {
                "key": "hear",
                "label": "HEAR",
                "sub": f"{live.get('hear_ms', 0.0):.0f} ms" if live.get("hear_ms") else "Whisper CPU",
                "health": 0.92,
            },
            {
                "key": "think",
                "label": "THINK",
                "sub": f"{active_brain.title()} · {avg_ms:.0f} ms",
                "health": 0.90,
            },
            {
                "key": "tools",
                "label": "TOOLS",
                "sub": f"{n_apps} apps · {fast_rate}% fast",
                "health": 0.96,
            },
            {
                "key": "speak",
                "label": "SPEAK",
                "sub": (
                    f"{live.get('speak_ms', 0.0):.0f} ms · {self._active_voice_name()}"
                    if live.get("speak_ms")
                    else f"Kokoro · {self._active_voice_name()}"
                ),
                "health": 0.94,
            },
            {
                "key": "memory",
                "label": "MEMORY",
                "sub": f"{n_facts} facts · {n_notes} notes",
                "health": 0.98,
            },
        ]

        analyzer = TraceAnalyzer(mem)
        insight = {
            "summary": analyzer.summary_report(auto_brain=auto_brain, current_brain=active_brain),
            "friction": analyzer.friction_report(),
        }

        recent_notes = []
        if notes_idx is not None:
            note_items = (
                notes_idx.recent(limit=15)
                if hasattr(notes_idx, "recent")
                else list(notes_idx.notes)[:15]
            )
            for n in note_items:
                recent_notes.append(
                    {
                        "title": n.title,
                        "folder": n.folder,
                        "excerpt": notes_idx.excerpt(n, n.title),
                    }
                )

        return {
            "status": {
                "assistant": config.ASSISTANT_NAME,
                "brain": active_brain,
                "model": brain.model if brain else None,
                "is_cloud": isinstance(brain, RemoteBrain),
                "voice": self._active_voice_name(),
                "pinned": pinned,
                "auto_brain": auto_brain,
                "counts": {
                    "apps": n_apps,
                    "projects": n_projects,
                    "notes": n_notes,
                    "facts": n_facts,
                    "traces": n_turns,
                },
            },
            "live": live,
            "metrics": {
                "total_turns": n_turns,
                "fast_hit_rate": fast_rate,
                "avg_ms": avg_ms,
                "sparkline_ms": sparkline_ms,
                "route_mix": route_mix,
                "axes": axes,
            },
            "insight": insight,
            "traces": [
                {
                    "id": t.id,
                    "ts": t.ts,
                    "raw_text": t.raw_text,
                    "clean_text": t.clean_text,
                    "source": t.source,
                    "brain": t.brain,
                    "calls": [{"tool": n, "args": a} for n, a in t.calls],
                    "reply": t.reply,
                    "ms": round(t.ms, 1),
                    "tok_per_s": round(t.tok_per_s, 1),
                    "steps": t.steps,
                }
                for t in traces[:25]
            ],
            "facts": [{"id": f.id, "text": f.text, "created": f.created} for f in facts_list],
            "recent_notes": recent_notes,
        }

    def _handle_post(
        self, route: str, body: bytes, headers: dict[str, str] | None = None
    ) -> tuple[int, str, bytes]:
        if route == "/api/voice":
            if self.transcriber is None and not config.DRY_RUN:
                try:
                    from app.audio.stt import Transcriber

                    self.transcriber = Transcriber()
                except Exception:
                    pass
            if self.transcriber is None:
                return _json_response(
                    503, {"error": "Speech recognition is not loaded on this server"}
                )
            ctype = ""
            if headers:
                ctype = next(
                    (v for k, v in headers.items() if k.lower() == "content-type"), ""
                ).lower()
            speak_on_pc = False
            return_audio = True
            if body.startswith(b"RIFF") or ctype.startswith("audio/"):
                wav_bytes = body
            else:
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return _json_response(400, {"error": "Invalid JSON body"})
                if not isinstance(data, dict):
                    return _json_response(400, {"error": "Expected JSON object"})
                speak_on_pc = bool(data.get("speak_on_pc", False))
                return_audio = bool(data.get("return_audio", True))
                b64_wav = str(data.get("audio_wav") or "").strip()
                if not b64_wav:
                    return _json_response(400, {"error": "Invalid WAV audio"})
                try:
                    wav_bytes = base64.b64decode(b64_wav, validate=False)
                except Exception:
                    return _json_response(400, {"error": "Invalid WAV audio"})

            try:
                audio = decode_wav_16k(wav_bytes)
            except Exception:
                return _json_response(400, {"error": "Invalid WAV audio"})

            text = (self.transcriber.transcribe(audio) or "").strip()
            speaker = self.speaker or getattr(self.router, "speaker", None)
            brain = getattr(self.router, "brain", None)

            if not text:
                missed = "Sorry, I didn't catch that."
                audio_wav: str | None = None
                if return_audio and speaker is not None and hasattr(speaker, "synthesize_wav"):
                    out_wav = speaker.synthesize_wav(missed)
                    if out_wav:
                        audio_wav = base64.b64encode(out_wav).decode("ascii")
                return _json_response(
                    200,
                    {
                        "transcript": "",
                        "reply": missed,
                        "source": "voice",
                        "ms": 0.0,
                        "calls": [],
                        "sources": [],
                        "brain": self._active_brain_name(),
                        "model": brain.model if brain else None,
                        "audio_wav": audio_wav,
                    },
                )

            with self.lock:
                res = self.router.handle(text)

            if speak_on_pc and speaker is not None:
                speaker.stop()
                speaker.say(res.reply)

            audio_wav = None
            if return_audio and speaker is not None and hasattr(speaker, "synthesize_wav"):
                out_wav = speaker.synthesize_wav(res.reply)
                if out_wav:
                    audio_wav = base64.b64encode(out_wav).decode("ascii")

            return _json_response(
                200,
                {
                    "transcript": text,
                    "reply": res.reply,
                    "source": res.source,
                    "ms": round(res.ms, 1),
                    "calls": [{"tool": name, "args": args} for name, args in res.calls],
                    "sources": res.sources,
                    "brain": self._active_brain_name(),
                    "model": brain.model if brain else None,
                    "audio_wav": audio_wav,
                },
            )

        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json_response(400, {"error": "Invalid JSON body"})
        if not isinstance(data, dict):
            return _json_response(400, {"error": "Expected JSON object"})

        if route == "/api/chat":
            text = str(data.get("text") or "").strip()
            if not text:
                return _json_response(400, {"error": "Missing 'text'"})
            speak_on_pc = bool(data.get("speak_on_pc", False))
            return_audio = bool(data.get("return_audio", False))

            with self.lock:
                res = self.router.handle(text)

            speaker = self.speaker or getattr(self.router, "speaker", None)
            if speak_on_pc and speaker is not None:
                speaker.stop()
                speaker.say(res.reply)

            audio_wav: str | None = None
            if return_audio and speaker is not None and hasattr(speaker, "synthesize_wav"):
                wav_bytes = speaker.synthesize_wav(res.reply)
                if wav_bytes:
                    audio_wav = base64.b64encode(wav_bytes).decode("ascii")

            brain = getattr(self.router, "brain", None)
            return _json_response(
                200,
                {
                    "reply": res.reply,
                    "source": res.source,
                    "ms": round(res.ms, 1),
                    "calls": [{"tool": name, "args": args} for name, args in res.calls],
                    "sources": res.sources,
                    "brain": self._active_brain_name(),
                    "model": brain.model if brain else None,
                    "audio_wav": audio_wav,
                },
            )

        if route == "/api/skills/run":
            name = str(data.get("name") or "").strip()
            if not name:
                return _json_response(400, {"error": "Missing 'name'"})
            match = next((s for s in discover_skills() if s.name == name), None)
            if match is None:
                return _json_response(404, {"error": f"Unknown skill '{name}'"})
            with self.lock:
                res = self.router.handle(match.prompt)
            saved_path = None
            if match.save_to_vault and res.reply:
                saved_path = str(save_vault_output(match.name, res.reply))
            return _json_response(
                200,
                {
                    "skill": match.name,
                    "reply": res.reply,
                    "source": res.source,
                    "steps": res.steps,
                    "saved_to": saved_path,
                },
            )

        if route == "/api/remember":
            text = str(data.get("text") or "").strip()
            if not text:
                return _json_response(400, {"error": "Missing 'text'"})
            with self.lock:
                reply = self.router.toolbox.remember(text)
            return _json_response(200, {"reply": reply})

        return _json_response(404, {"error": "Not found"})

    def start(self) -> None:
        """Start the ThreadingHTTPServer on a background daemon thread."""
        if self._httpd is not None:
            return
        if not self.token and client_access(self.host) != "loopback":
            self.token = load_or_create_token()
        server_ref = self

        class _Handler(BaseHTTPRequestHandler):
            def _forward(self, method: str) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw_body = self.rfile.read(length) if length > 0 else b""
                hdrs = {k: v for k, v in self.headers.items()}
                status, ctype, resp_bytes = server_ref.dispatch(
                    method, self.path, hdrs, raw_body, client_ip=self.client_address[0]
                )
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.end_headers()
                self.wfile.write(resp_bytes)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/api/events":
                    query = urllib.parse.parse_qs(parsed.query)
                    hdrs = {k: v for k, v in self.headers.items()}
                    access = server_ref._access_status(hdrs, query, self.client_address[0])
                    if access != 200:
                        self.send_response(access)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(b'{"error":"Unauthorized"}')
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    q = BUS.subscribe()
                    try:
                        snap = {"type": "snapshot", **BUS.snapshot()}
                        self.wfile.write(f"data: {json.dumps(snap)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        while server_ref._httpd is not None:
                            try:
                                evt = q.get(timeout=1.0)
                                self.wfile.write(f"data: {json.dumps(evt)}\n\n".encode("utf-8"))
                            except queue.Empty:
                                self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    finally:
                        BUS.unsubscribe(q)
                    return
                self._forward("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._forward("POST")

            def log_message(self, fmt: str, *args) -> None:
                if server_ref.verbose:
                    super().log_message(fmt, *args)

        self._httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="adeola-http", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Shut down the ThreadingHTTPServer if running."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
