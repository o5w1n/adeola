"""Lightweight HTTP API server for local & Tailscale access (Phase 5)."""

from __future__ import annotations

import base64
import io
import ipaddress
import json
import socket
import threading
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from app import config

_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")

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

    def _authorized(self, headers: dict[str, str], query: dict[str, list[str]]) -> bool:
        if not self.token:
            return True
        lower_headers = {k.lower(): v for k, v in (headers or {}).items()}
        auth = lower_headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and auth[7:].strip() == self.token:
            return True
        if lower_headers.get("x-adeola-token", "").strip() == self.token:
            return True
        q_tokens = query.get("token", [])
        if q_tokens and q_tokens[0] == self.token:
            return True
        return False

    def dispatch(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> tuple[int, str, bytes]:
        """Pure request dispatcher returning (status_code, content_type, response_bytes)."""
        parsed = urllib.parse.urlparse(path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        hdrs = headers or {}

        if route.startswith("/api/") and not self._authorized(hdrs, query):
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

        if route == "/manifest.json":
            return (
                200,
                "application/manifest+json; charset=utf-8",
                json.dumps(MANIFEST_JSON).encode("utf-8"),
            )

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
                    "voice": self._active_voice_name(),
                    "counts": {
                        "apps": len(tb.apps.apps) if tb and tb.apps else 0,
                        "projects": len(tb.projects.all()) if tb and tb.projects else 0,
                        "notes": len(notes_idx.notes) if notes_idx else 0,
                        "facts": len(tb.memory.facts()) if tb and tb.memory else 0,
                    },
                },
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
        server_ref = self

        class _Handler(BaseHTTPRequestHandler):
            def _forward(self, method: str) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw_body = self.rfile.read(length) if length > 0 else b""
                hdrs = {k: v for k, v in self.headers.items()}
                status, ctype, resp_bytes = server_ref.dispatch(method, self.path, hdrs, raw_body)
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.end_headers()
                self.wfile.write(resp_bytes)

            def do_GET(self) -> None:  # noqa: N802
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
