# Adeola P3: Prompt Pack Polish (Two-Turn Wake, Live Markdown Captures, Stream & Fallback Voice, Cloud/Local Switch) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the remaining *Run Jarvis Fully Local* prompt-pack polish items for Adeola: two-turn standalone wake-word follow-up (`"Adeola"` → `"Yes?"` → command) with rotating missed-speech replies, live-indexed markdown captures in `data/captures/`, continuous `sd.OutputStream` playback with a Windows SAPI voice fallback, and spoken `"go local"` / `"switch to Claude"` cloud-key honesty + `"One moment."` model-preload feedback.

**Architecture:** We deliver P3 in four focused tasks:
1. **Task P3.1 — Two-Turn Standalone Wake Word & Rotating Missed-Speech Replies (`app/audio/wake.py`, `app/audio/push_to_talk.py`, `tests/test_wake.py`, `tests/test_voice.py`):**
   - When the user says only the wake word (`"Adeola"`) and pauses, `Router.handle` returns `"Yes?"`. Instead of returning to `IDLE`, `WakeWord._handle` waits for `"Yes?"` playback to finish and immediately opens a single follow-up `CAPTURE` window so the user can speak their command without repeating `"Adeola"`.
   - Add `MISSED_REPLIES` in `app/audio/push_to_talk.py` (`("Sorry, I didn't catch that.", "I missed that — could you say it again?", "Didn't quite catch that.")`) so consecutive empty transcriptions rotate naturally while keeping `"Sorry, I didn't catch that."` first.
2. **Task P3.2 — Live-Indexed Markdown Captures in `data/captures/` (`app/config.py`, `app/memory/notes.py`, `app/tools/registry.py`, `main.py`, `tests/test_notes.py`, `tests/test_memory.py`):**
   - Add `CAPTURES_DIR = DATA_DIR / "captures"` in `app/config.py`.
   - Add `NotesIndex.add_capture(text: str) -> Note | None` ("One Door for New Notes"): writes a dated markdown file (`YYYY-MM-DD-<slug>.md`) into `captures_dir` and appends/updates the `Note` in `self.notes` and `self._words` immediately so it is searchable on the very next turn without `reload`, while keeping `qabw` (`NOTES_DIRS`) 100% read-only.
   - Pass `notes` into `Toolbox` so `Toolbox.remember(note)` writes both to SQLite (`adeola.db`) and to `notes.add_capture(note)` when `notes` is present.
3. **Task P3.3 — Continuous `sd.OutputStream` Playback & Windows SAPI Voice Fallback (`app/audio/tts.py`, `tests/test_tts.py`):**
   - Upgrade `SoundDevicePlayer` to stream audio chunks through a persistent `sd.OutputStream` (reused while sample rate matches) instead of reopening `sd.play()` per chunk, with clean interruption on `stop()`.
   - Add `WindowsSapiEngine` in `app/audio/tts.py` (using Windows `System.Speech.Synthesis.SpeechSynthesizer` to synthesize 16-bit PCM WAV into a `numpy.float32` array) and use it as an automatic fallback in `load_kokoro()` if Kokoro ONNX fails to load, so Adeola never goes silent.
4. **Task P3.4 — Spoken `"go local"` / `"switch to Claude"` Honesty & `"One moment."` Preload Notice (`app/config.py`, `app/brain/model.py`, `app/brain/router.py`, `README.md`, `tests/test_router.py`):**
   - Add `CLOUD_BRAINS = {"claude": {"model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY"}}` in `app/config.py`.
   - Handle `"switch to Claude"` / `"go cloud"` on the fast path in `Router`: if `ANTHROPIC_API_KEY` is missing or `"PUT-YOUR-KEY-HERE"`, reply `"The cloud brain has no API key set (ANTHROPIC_API_KEY), so I'm staying on my local brain."` without ever asking for the key in chat.
   - Handle `"go local"` / `"switch to your local brain"` on the fast path to ensure Adeola is on her local `smart` (`config.MODEL`) brain and local voice.
   - Speak `"One moment."` via `self.speaker.say("One moment.")` before `self.brain.switch_model(...)` when switching to a different model while voice is active.

**Tech Stack:** Python 3.14 (`.adenv`), stdlib `unittest`, `sounddevice`, `numpy`. **No new dependencies.**

**Spec:** [docs/local-jarvis-gap-analysis.md](../docs/local-jarvis-gap-analysis.md) scorecard items 02, 03, 04, 05, 06.

## Global Constraints

- Windows 11 + PowerShell. Run everything with `.\.adenv\Scripts\python.exe` from the repo root.
- Tests use stdlib `unittest` (`python -m unittest -v`), with `os.environ.setdefault("ADEOLA_DRY_RUN", "1")` before importing `app`.
- Never write to `qabw` (`~/Documents/qabw`); markdown captures must only ever go into `config.CAPTURES_DIR` (`data/captures/`) or a test temp directory.
- No new packages.
- Preserve all 131 existing unit tests.
- "Done" means `.\.adenv\Scripts\python.exe -m unittest -v` and `.\.adenv\Scripts\python.exe -m tools.preflight` both pass.

## Review Focus

1. **Two-turn wake-word follow-up** (`"Adeola"` → `"Yes?"` → `"open Brave"`) → after a wake-only utterance returns `"Yes?"`, `WakeWord` automatically enters `CAPTURE` once for the follow-up command without requiring the wake word again, and does not loop infinitely if the user stays silent during the follow-up. *(Test in Task P3.1.)*
2. **Live markdown capture indexing** (`"note to self: the finish window is 700 milliseconds"`) → writes a real `.md` file inside `captures_dir` and makes it immediately retrievable via `notes.search("finish window")` and `"what do my notes say about the finish window"` without calling `notes.load()`, while never writing to `qabw`. *(Test in Task P3.2.)*
3. **Continuous output stream & SAPI fallback** → `SoundDevicePlayer` reuses a single `sd.OutputStream` across chunks of the same sample rate and aborts cleanly on `stop()`; if Kokoro fails at boot, `load_kokoro()` falls back to `WindowsSapiEngine` so `Speaker` still produces valid `(audio, rate)` float32 arrays. *(Test in Task P3.3.)*
4. **Cloud brain no-key honesty & `"go local"`** → `"switch to Claude"` / `"go cloud"` checks `ANTHROPIC_API_KEY` and replies `"The cloud brain has no API key set (ANTHROPIC_API_KEY), so I'm staying on my local brain."` when unset, never prompting for a key in chat; `"go local"` switches back to the local `smart` brain. *(Test in Task P3.4.)*
5. **Preload `"One moment."` notice** → when `router.speaker` is present and the user switches to a different brain, `router.speaker.say("One moment.")` is called before warming the new model. *(Test in Task P3.4.)*

---

### Task P3.1: Two-Turn Standalone Wake Word & Rotating Missed-Speech Replies

**Files:**
- Modify: `app/audio/wake.py`
- Modify: `app/audio/push_to_talk.py`
- Modify: `tests/test_wake.py`, `tests/test_voice.py`

**Interfaces:**
- Produces in `app/audio/wake.py`:
  - `WakeWord._awaiting_followup: bool = False`
  - `WakeWord._start_capture(self, include_preroll: bool = True, beep_ tone: bool = True) -> None`
  - In `WakeWord._handle(self, audio: np.ndarray)`: if `reply == "Yes?"` and `not self._awaiting_followup`, wait for `self.speaker` to finish speaking (if `self.speaker` is set), set `self._awaiting_followup = True`, and enter `CAPTURE` directly (clearing queued blocks from while Adeola was speaking `"Yes?"`) so the next utterance is captured as the command. Reset `self._awaiting_followup = False` on any non-`"Yes?"` reply or timeout in `_capture`.
- Produces in `app/audio/push_to_talk.py`:
  - `MISSED_REPLIES = ("Sorry, I didn't catch that.", "I missed that — could you say it again?", "Didn't quite catch that.")`
  - `PushToTalk._missed_idx: int = 0` cycled when `not text` in `PushToTalk.process`.

- [x] **Step 1: Write failing tests in `tests/test_wake.py` and `tests/test_voice.py`**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_wake tests.test_voice -v` and confirm failure**
- [x] **Step 3: Implement two-turn wake follow-up in `app/audio/wake.py` and `MISSED_REPLIES` in `app/audio/push_to_talk.py`**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_wake tests.test_voice -v` → OK**

---

### Task P3.2: Live-Indexed Markdown Captures in `data/captures/`

**Files:**
- Modify: `app/config.py`
- Modify: `app/memory/notes.py`
- Modify: `app/tools/registry.py`
- Modify: `main.py`
- Modify: `tests/test_notes.py`, `tests/test_memory.py`

**Interfaces:**
- Produces in `app/config.py`:
  - `CAPTURES_DIR = DATA_DIR / "captures"`
- Produces in `app/memory/notes.py`:
  - `NotesIndex.__init__(self, dirs: list[str | Path] | None = None, captures_dir: Path | None = None)`: stores `self.captures_dir = Path(captures_dir).expanduser() if captures_dir else (config.CAPTURES_DIR if dirs is None else None)`. During `load()`, if `self.captures_dir` exists and is not already in `self.dirs`, scans `self.captures_dir` too (never adding `self.captures_dir` to `self.missing` if it doesn't exist yet).
  - `NotesIndex.add_capture(self, text: str) -> Note | None`: creates `self.captures_dir` if set (unless `config.DRY_RUN` and `self.captures_dir == config.CAPTURES_DIR`), builds a clean title from the first up to 6 words of `text`, writes `# <title>\n\nDate: <YYYY-MM-DD>\n\n<text>\n` to `<captures_dir>/<YYYY-MM-DD>-<slug>.md` when a writable `captures_dir` is active, adds/updates the `Note(title=title, path=fpath, folder="captures", body=...)` and `(words(note.title), words(note.body))` in `self.notes` and `self._words` immediately, and returns the `Note`.
- Produces in `app/tools/registry.py` & `main.py`:
  - `Toolbox.__init__(self, apps: AppIndex, memory=None, projects=None, notes=None)` (`self.notes = notes`).
  - `Toolbox.remember(self, note: str) -> str`: after `self.memory.add_fact(note)`, if `self.notes is not None`, calls `self.notes.add_capture(note)`.
  - In `main.py`: `build()` passes `notes` into `Toolbox(apps, memory, projects, notes)`.

- [x] **Step 1: Write failing tests in `tests/test_notes.py` and `tests/test_memory.py`**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_notes tests.test_memory -v` and confirm failure**
- [x] **Step 3: Implement `CAPTURES_DIR`, `NotesIndex.add_capture`, and `Toolbox.remember` live note indexing**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_notes tests.test_memory -v` → OK**

---

### Task P3.3: Continuous `sd.OutputStream` Playback & Windows SAPI Voice Fallback

**Files:**
- Modify: `app/audio/tts.py`
- Modify: `tests/test_tts.py`

**Interfaces:**
- Produces in `app/audio/tts.py`:
  - `SoundDevicePlayer`: manages a continuous `sd.OutputStream(samplerate=rate, channels=1, dtype="float32")` opened on first `play(audio, rate)` (and reopened only if `rate` changes or after `stop()`), feeding chunks on a background playback worker or lock-guarded write loop so consecutive chunks at the same rate share one open audio stream without clicks/reopen gaps.
  - `WindowsSapiEngine`: fallback TTS engine implementing `get_voices() -> list[str]`, `get_voice_style(name: str) -> np.ndarray`, and `create(text: str, voice=None, speed: float = 1.0, lang: str = "en-us") -> tuple[np.ndarray, int]` using Windows SAPI (`win32com` / PowerShell `System.Speech.Synthesis.SpeechSynthesizer` to a temp WAV or memory stream, returning `(float32_audio, sample_rate)`).
  - `load_kokoro(fallback: bool = True)`: attempts `Kokoro(...)`; if loading raises an exception and `fallback=True`, prints a warning and returns `WindowsSapiEngine()`.

- [x] **Step 1: Write failing tests in `tests/test_tts.py`**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_tts -v` and confirm failure**
- [x] **Step 3: Implement continuous `SoundDevicePlayer` stream and `WindowsSapiEngine` fallback in `app/audio/tts.py`**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_tts -v` → OK**

---

### Task P3.4: Spoken `"go local"` / `"switch to Claude"` Honesty & `"One moment."` Preload Notice

**Files:**
- Modify: `app/config.py`
- Modify: `app/brain/router.py`
- Modify: `README.md`
- Modify: `tests/test_router.py`

**Interfaces:**
- Produces in `app/config.py`:
  - `CLOUD_BRAINS = {"claude": {"model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY"}}`
- Produces in `app/brain/router.py`:
  - `_GO_LOCAL` matching `"go local"`, `"switch to local"`, `"switch to your local brain"`, `"use your local brain"`, `"back to your local brain"`.
  - `_GO_CLOUD` matching `"go cloud"`, `"switch to claude"`, `"switch to your cloud brain"`, `"use claude"`.
  - In `Router._switch_brain(self, target: str)`:
    - If `target.lower()` is in `config.CLOUD_BRAINS` or `"cloud"`: checks `os.getenv(api_key_env, "")`; if empty or `"PUT-YOUR-KEY-HERE"`, returns `"The cloud brain has no API key set (ANTHROPIC_API_KEY), so I'm staying on my local brain."`.
    - When switching to a different local model and `self.speaker is not None`, calls `self.speaker.say("One moment.")` before `self.brain.switch_model(target)`.
  - In `Router._go_local(self) -> str`: switches brain back to `"smart"` (if not already on `"smart"`) and voice back to `"default"` (if `self.speaker` is active), returning `"All local: smart brain (qwen3:4b)."`

- [x] **Step 1: Write failing tests in `tests/test_router.py`**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` and confirm failure**
- [x] **Step 3: Implement `CLOUD_BRAINS`, `_GO_LOCAL`, `_GO_CLOUD`, `"One moment."` preload notice, and update `README.md`**
- [x] **Step 4: Run full unit test suite & live preflight**

---

## Review

- **Task P3.1 (`app/audio/wake.py`, `app/audio/push_to_talk.py`, `tests/test_wake.py`, `tests/test_voice.py`):** Implemented two-turn standalone wake-word follow-up (`"Adeola"` → `"Yes?"` → single follow-up `CAPTURE` window after `speaker` finishes speaking) and rotating `MISSED_REPLIES` on empty transcriptions.
- **Task P3.2 (`app/config.py`, `app/memory/notes.py`, `app/tools/registry.py`, `main.py`, `tests/test_notes.py`):** Added `CAPTURES_DIR = DATA_DIR / "captures"`, `NotesIndex.add_capture(text)`, and wired `Toolbox.remember` so captured notes are written as dated `.md` files and indexed live in `NotesIndex` without ever writing to `qabw`.
- **Task P3.3 (`app/audio/tts.py`, `tests/test_tts.py`):** Upgraded `SoundDevicePlayer` to stream audio chunks through a persistent `sd.OutputStream` per sample rate on a background worker thread, and added `WindowsSapiEngine` fallback in `load_kokoro()`.
- **Task P3.4 (`app/config.py`, `app/brain/router.py`, `README.md`, `tests/test_router.py`):** Added `CLOUD_BRAINS`, fast-path `"switch to Claude"` / `"go cloud"` API-key honesty, `"go local"` fast-path reset, and `"One moment."` spoken preload notice before switching to a different local model.
- **Verification:**
  - Unit tests: `133 tests OK` (`12.75 s`).
  - Live preflight (`.\.adenv\Scripts\python.exe -m tools.preflight`):
    - `LATENCY BUDGET: hear 0.91 s · think 0.58 s · speak 2.72 s · total 4.20 s`
    - `10 pass · 0 fail · 0 warn`
