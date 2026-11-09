# Adeola Conversational Versatility & Flexibility Pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 4 live-session friction points: (1) eliminate the `"Adeola"` → `"Yes?"` playback race so follow-up capture waits until `"Yes?"` has actually finished playing, (2) silence child GUI/Electron (`VS Code`) stdout/stderr and strip inherited `VSCODE_*`/`ELECTRON_*` env vars so launching apps never pollutes Adeola's console, (3) make `Router` and `Whisper` tolerant of natural spoken phrasing (`"switch to Brave"`, `"switch to quick"`, domain vocabulary hints in `hint_prompt()`), and (4) make `Brain` + `Router` conversationally versatile so casual banter, reactions (`"aigh, go off"`, `"thanks"`, `"bet"`), and open-ended chat get natural replies instead of spurious `open_app` tool calls.

**Architecture:**
1. **Task V1 — Two-Turn `"Yes?"` Playback Sync & Quiet Timeout (`app/audio/wake.py`, `app/audio/tts.py`, `tests/test_wake.py`):**
   - In `WakeWord._handle`, wait for `self.speaker` using `self.speaker.wait_until_done()` (when available, or polling `self.speaker.speaking` after a brief startup check) plus a short acoustic settle pause (`config.FOLLOWUP_SETTLE_SECONDS = 0.12`, `0.0` in `DRY_RUN`) BEFORE draining `self._blocks` and entering `CAPTURE`.
   - Give follow-up capture a more forgiving `FOLLOWUP_SPEECH_TIMEOUT = 4.5` seconds so the user isn't rushed after `"Yes?"`.
2. **Task V2 — Detached Silent App & Project Launching (`app/tools/applications.py`, `app/tools/projects.py`, `tests/test_router.py`):**
   - Add `clean_launch_env() -> dict[str, str]` in `app/tools/applications.py` stripping `ELECTRON_RUN_AS_NODE`, `NODE_OPTIONS`, and all `VSCODE_*` environment variables that cause child VS Code / Electron instances to dump `[main ...] [node-compile-cache]` logs into the parent terminal.
   - Update `launch(target)` in `app/tools/applications.py` and `open_project(p)` in `app/tools/projects.py` to spawn via `subprocess.Popen` with `env=clean_launch_env()`, `stdin=subprocess.DEVNULL`, `stdout=subprocess.DEVNULL`, `stderr=subprocess.DEVNULL`, and `CREATE_NO_WINDOW | DETACHED_PROCESS` on Windows (`cmd.exe /c start "" <target>` for `launch(target)`).
3. **Task V3 — Whisper Domain Vocabulary Hints & Flexible Spoken Routing (`app/audio/stt.py`, `app/brain/router.py`, `tests/test_router.py`, `tests/test_offline.py`):**
   - Expand `hint_prompt()` in `app/audio/stt.py` to include brain-shelf names (`quick brain, smart brain, coder brain, local brain, Claude`), voice names (`calm voice, warm voice, bright voice`), notes/memory phrases (`what do my notes say, note to self, remember that`), and common apps so Whisper `base.en` doesn't mishear `"local brain"` as `"lookout, Brave"`.
   - In `app/brain/router.py`:
     - Broaden `_SWITCH_BRAIN` to also match shorthand `"switch to quick"`, `"switch to smart"`, `"switch to coder"`, `"switch to local"`, andfuzzy near-misses like `"switch to your local..."` / `"switch to your lookout..."` when `"brain"` or a shelf keyword is present.
     - Allow `"switch to <app>"` / `"go to <app>"` (and Whisper word-order inversions like `"visual switch to code"`) to open known apps on the fast path when `<app>` is not a brain/voice keyword.
4. **Task V4 — Conversational Versatility & Anti-False-Tool-Call Guard (`app/brain/model.py`, `app/brain/router.py`, `tests/test_router.py`):**
   - Update `BASE_PROMPT` and `Brain._build_system_prompt` in `app/brain/model.py`:
     - Explicitly instruct the model: only call a tool when the user's *current* message asks to perform an action; never repeat a previous turn's `open_app` on casual reactions, slang, banter, or follow-up chat.
     - Include both a tool JSON example AND a conversational chat JSON example (`{"actions": [], "reply": "..."}`) in `_build_system_prompt`.
   - Add a fast/conversational guard in `app/brain/router.py`:
     - Handle common conversational acknowledgements/reactions (`"thanks"`, `"thank you"`, `"appreciate it"`, `"nice"`, `"cool"`, `"sweet"`, `"awesome"`, `"great"`, `"good job"`, `"bet"`, `"aight"`, `"alright"`, `"got it"`) on the fast path with rotating warm replies (`"Anytime."`, `"You got it."`, `"Happy to help."`).
     - Add `_is_casual_banter(text)` in `Router.handle` so if a message has no action/intent words (e.g. `"aigh, go off"`, `"aigh, go iff"`, `"haha"`, `"wild"`, `"for real"`) and the LLM tries to return `open_app` / `open_url` / `lock_pc` copied from history, `Router` strips the spurious tool call and asks for or returns a conversational reply instead.

---

### Task V1: Two-Turn `"Yes?"` Playback Sync & Follow-Up Window (`app/config.py`, `app/audio/wake.py`, `tests/test_wake.py`)

- [x] **Step 1: Write failing test in `tests/test_wake.py` verifying `WakeWord._handle` calls `speaker.wait_until_done()` before draining blocks and starting follow-up capture**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_wake -v` and confirm failure**
- [x] **Step 3: Implement `wait_until_done` sync and `FOLLOWUP_SPEECH_TIMEOUT` in `app/config.py` and `app/audio/wake.py`**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_wake -v` → OK**

---

### Task V2: Silent Detached App & Project Launching (`app/tools/applications.py`, `app/tools/projects.py`, `tests/test_router.py`)

- [x] **Step 1: Write failing tests in `tests/test_router.py` for `clean_launch_env()` and detached `launch()` / `open_project()` with `DEVNULL` streams**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` and confirm failure**
- [x] **Step 3: Implement `clean_launch_env()`, detached `launch()`, and detached `open_project()`**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router tests.test_memory -v` → OK**

---

### Task V3: Whisper Domain Vocabulary & Flexible Spoken Routing (`app/audio/stt.py`, `app/brain/router.py`, `tests/test_router.py`, `tests/test_offline.py`)

- [x] **Step 1: Write failing tests in `tests/test_router.py` and `tests/test_offline.py` for expanded `hint_prompt()`, shorthand brain switching (`"switch to quick"`), `"switch to <app>"`, and Whisper inversion recovery (`"visual switch to code"`)**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router tests.test_offline -v` and confirm failure**
- [x] **Step 3: Implement expanded `hint_prompt()` in `app/audio/stt.py` and flexible spoken fast-path patterns in `app/brain/router.py`**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router tests.test_offline -v` → OK**

---

### Task V4: Conversational Versatility & Spurious Tool-Call Guard (`app/brain/model.py`, `app/brain/router.py`, `tests/test_router.py`)

- [x] **Step 1: Write failing tests in `tests/test_router.py` for fast acknowledgements (`"thanks"`, `"appreciate it"`), conversational prompt examples, and blocking spurious `open_app` calls on banter (`"aigh, go off"`, `"aigh, go iff"`)**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` and confirm failure**
- [x] **Step 3: Implement prompt improvements in `app/brain/model.py` and conversational fast-path + spurious-tool guard in `app/brain/router.py`**
- [x] **Step 4: Run full unit test suite, `tests.eval_commands`, & live preflight**

---

## Review

- **Task V1 (`app/config.py`, `app/audio/wake.py`, `tests/test_wake.py`):** Added `FOLLOWUP_SPEECH_TIMEOUT = 4.5` and `FOLLOWUP_SETTLE_SECONDS = 0.15`; updated `WakeWord._handle` to call `self.speaker.wait_until_done()`, wait for playback + settle pause, and drain mic blocks before opening follow-up `CAPTURE`.
- **Task V2 (`app/tools/applications.py`, `app/tools/projects.py`, `tests/test_router.py`):** Added `clean_launch_env()` stripping `ELECTRON_RUN_AS_NODE`, `NODE_OPTIONS`, `CHROME_DESKTOP`, `GDK_PIXBUF_MODULE_FILE`, and `VSCODE_*`; updated `launch()` and `open_project()` to spawn detached processes via `subprocess.Popen` with `DEVNULL` streams.
- **Task V3 (`app/audio/stt.py`, `app/brain/router.py`, `tests/test_offline.py`, `tests/test_router.py`):** Expanded Whisper `hint_prompt()` with brain/voice/notes vocabulary; added shorthand brain switching (`"switch to quick"`), `"switch to your lookout, Brave"` → `_go_local()`, `"switch to <app>"`, and Whisper word-order inversion recovery (`"Visual Switch to Code"`).
- **Task V4 (`app/brain/model.py`, `app/brain/router.py`, `tests/test_router.py`):** Added `Conversation vs. tools` rule to `BASE_PROMPT` (`195` words) and both `Example (action)` + `Example (chat)` in `_build_system_prompt`; added `_THANKS_ACK` + `ACK_REPLIES` on the fast path and `Router._is_spurious_action` to block false `open_app` tool calls on casual banter (`"aigh, go off"`, `"haha that is wild"`).
- **Verification:**
  - Unit tests: `151 tests OK` (`5.29 s`).
  - Live preflight (`.\.adenv\Scripts\python.exe -m tools.preflight`):
    - `LATENCY BUDGET: hear 1.98 s · think 3.97 s · speak 2.41 s · total 8.35 s`
    - `10 pass · 0 fail · 0 warn`
