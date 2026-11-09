# Adeola Phase 5: Local & Tailscale Server + Android Companion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Adeola over a lightweight HTTP server (`python main.py --serve`) reachable from localhost and Tailscale (`100.x.y.z`), providing JSON endpoints (`/api/status`, `/api/chat`, `/api/voice`, `/api/notes`, `/api/remember`) and a mobile-first Android Web/PWA companion UI with text chat, hold-to-talk voice, brain-shelf switching, and audio reply playback.

**Architecture:** We deliver Phase 5 in three focused tasks (using stdlib `http.server.ThreadingHTTPServer` so zero external packages or internet installs are needed, preserving 100% offline pins):
1. **Task 5.1 — HTTP API Server (`app/config.py`, `app/server.py`, `tests/test_server.py`):**
   - Add `SERVER_HOST = "0.0.0.0"`, `SERVER_PORT = 8765`, and `SERVER_TOKEN = os.getenv("ADEOLA_TOKEN", "")` in `app/config.py`.
   - Build `AdeolaServer` in `app/server.py` wrapping `ThreadingHTTPServer`:
     - Optional token check (`Authorization: Bearer <token>` or `X-Adeola-Token` header / `?token=` query param when `SERVER_TOKEN` is set).
     - `GET /api/status` → `{assistant, brain, model, voice, counts: {apps, projects, notes, facts}}`.
     - `POST /api/chat` (`{"text": "...", "speak_on_pc": false, "return_audio": false}`) → acquires `lock`, runs `router.handle(text)`, optionally speaks on PC or returns base64 WAV (`audio_wav`) synthesized via `speaker`, and returns `{reply, source, ms, calls, sources, brain, model, audio_wav}`.
     - `POST /api/voice` (`{"audio_wav": "<base64>", "speak_on_pc": false, "return_audio": true}`) → decodes 16-bit PCM WAV (resampling to 16 kHz mono float32), transcribes via `transcriber.transcribe(audio)`, runs `router.handle(text)`, and returns `{transcript, reply, source, ms, calls, sources, audio_wav}`.
     - `GET /api/notes?q=...` → searches `router.notes` and returns matching note titles, folders, and excerpts.
     - `POST /api/remember` (`{"text": "..."}`) → calls `router.toolbox.remember(text)` (saving to SQLite + `data/captures/`).
   - Helper `tailscale_ip() -> str | None` inspecting local network interfaces for a `100.64.0.0/10` CGNAT Tailscale address to print the exact phone URL at boot.
2. **Task 5.2 — Mobile-First Android Companion UI & WAV Helper (`app/server.py`, `app/audio/tts.py`, `tests/test_server.py`):**
   - Add `Speaker.synthesize_wav(text: str) -> bytes` in `app/audio/tts.py` to concatenate synthesized float32 chunks for a reply into a 16-bit PCM `.wav` byte buffer so remote clients (Android browser/app) can play Adeola's Kokoro/SAPI voice directly on the phone.
   - Serve a responsive, zero-dependency dark-mode mobile companion web UI at `GET /` (with `GET /manifest.json` for Android "Add to Home screen"):
     - Live status header (`brain` / `model` badge + brain-shelf switch pills: `Quick`, `Smart`, `Coder`).
     - Chat feed showing user messages, Adeola replies, `[source · ms]` tags, and `(from your notes: ...)` citations.
     - **Hold-to-Talk** microphone button (records standard 16-bit PCM WAV in browser WebAudio, posts to `/api/voice`, and plays back returned `audio_wav` on the phone) + text input bar + quick-action chips (`Pause/Play`, `What's playing?`, `Mute`, `Lock PC`).
3. **Task 5.3 — CLI Wiring (`main.py`, `README.md`, `tests/test_server.py`):**
   - Add `--serve` and `--port` flags to `main.py` so `python main.py --serve` (or `python main.py --voice --serve`) starts `AdeolaServer` in the background while keeping the terminal loop active.
   - Document Phase 5 (`--serve`, Tailscale setup, Android companion UI, and JSON API) in `README.md`.

**Tech Stack:** Python 3.14 (`.adenv`), stdlib `http.server`, `json`, `wave`, `base64`, `unittest`, `numpy`. **No new dependencies.**

---

### Task 5.1: HTTP API Server (`app/config.py`, `app/server.py`, `tests/test_server.py`)

- [x] **Step 1: Write failing API tests in `tests/test_server.py` (`/api/status`, `/api/chat`, `/api/notes`, `/api/remember`, token auth)**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_server -v` and confirm failure**
- [x] **Step 3: Implement `SERVER_HOST`, `SERVER_PORT`, `SERVER_TOKEN` in `app/config.py` and `AdeolaServer` in `app/server.py`**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_server -v` → OK**

---

### Task 5.2: Mobile Companion UI & Remote Voice Synthesis/Transcription (`app/audio/tts.py`, `app/server.py`, `tests/test_server.py`)

- [x] **Step 1: Write failing tests in `tests/test_server.py` for `Speaker.synthesize_wav`, `/api/voice` WAV round-trip, and `GET /` companion HTML**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_server -v` and confirm failure**
- [x] **Step 3: Implement `Speaker.synthesize_wav` in `app/audio/tts.py`, WAV decode/resample + `/api/voice` + mobile UI in `app/server.py`**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_server -v` → OK**

---

### Task 5.3: CLI Integration & Documentation (`main.py`, `README.md`, `tests/test_server.py`)

- [x] **Step 1: Write failing CLI/build integration test in `tests/test_server.py`**
- [x] **Step 2: Wire `--serve` and `--port` into `main.py` and update `README.md`**
- [x] **Step 3: Run full unit test suite & live preflight**

---

## Review

- **Task 5.1 (`app/config.py`, `app/server.py`, `tests/test_server.py`):** Added `SERVER_HOST`, `SERVER_PORT`, and `SERVER_TOKEN` in `app/config.py`; built `AdeolaServer` (`ThreadingHTTPServer` + pure `dispatch()`) with `GET /api/status`, `POST /api/chat`, `GET /api/notes`, `POST /api/remember`, optional token auth (`Authorization: Bearer`, `X-Adeola-Token`, `?token=`), and `tailscale_ip()` auto-detection.
- **Task 5.2 (`app/audio/tts.py`, `app/server.py`, `tests/test_server.py`):** Added `Speaker.synthesize_wav(text) -> bytes`, `decode_wav_16k(wav_bytes)`, `POST /api/voice` (with lazy `Transcriber` loading when `not config.DRY_RUN`), and the responsive dark-mode Android Companion Web/PWA UI (`GET /` and `GET /manifest.json`) with Hold-to-Talk mic recording, brain-shelf switching, and audio reply playback.
- **Task 5.3 (`main.py`, `README.md`, `tests/test_server.py`):** Wired `start_server`, `--serve`, and `--port` into `main.py` and documented Phase 5 in `README.md`.
- **Verification:**
  - Unit tests: `145 tests OK` (`1.56 s`).
  - Live preflight (`.\.adenv\Scripts\python.exe -m tools.preflight`):
    - `LATENCY BUDGET: hear 1.21 s · think 0.79 s · speak 1.01 s · total 3.01 s`
    - `10 pass · 0 fail · 0 warn`
