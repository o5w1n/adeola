# Adeola P1: Voice Feel & Fluency — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Adeola's live voice interaction feel natural: never clip the start of a command after the wake word, let the user pause mid-sentence without getting cut off, let the user interrupt her out loud (`"Adeola, stop"`), and eliminate mid-sentence TTS gaps and clicks.

**Architecture:** We upgrade the audio pipeline in four focused tasks:
1. **Wake pre-roll + finish window (`app/audio/wake.py`, `app/config.py`):** `WakeWord` keeps a rolling `WAKE_PREROLL_SECONDS` (0.4 s) ring buffer in `IDLE` and prepends it to `_audio` on trigger. After `END_SILENCE_SECONDS` (0.8 s) of quiet once speech was heard, `WakeWord` enters a `FINISH` window (`FINISH_SECONDS = 0.7`): if more speech arrives before `FINISH_SECONDS` elapses, it returns to `CAPTURE` and keeps appending; if quiet continues through `FINISH_SECONDS`, it dispatches the utterance.
2. **Voice barge-in + stop commands (`app/audio/wake.py`, `app/brain/router.py`, `app/config.py`):** While Adeola is speaking (`speaker.speaking`), `WakeWord` no longer ignores the mic completely: it checks `detector.score(block)` against a higher `BARGE_IN_THRESHOLD = 0.75` (while still ignoring the brief `SELF_TRIGGER_GUARD` tail right after she stops). When triggered during speech, it immediately calls `speaker.stop()` and enters `CAPTURE`. In `Router.fast_parse`, bare interrupt words (`"stop"`, `"wait"`, `"quiet"`, `"shut up"`, `"enough"`, `"cancel"`, `"never mind"`) when nothing is playing (or when spoken to stop Adeola) are handled cleanly: if spoken as an interrupt (`"stop"`, `"wait"`, `"quiet"`, `"shut up"`, `"enough"`, `"cancel"`, `"never mind"`), `_STOP_SPEAKING` in `Router` replies `""` (silent stop) or `"Okay."` instead of routing `"stop"` to Windows media controls unless a media noun (`"stop the music"`, `"stop playback"`) is present or media is actually playing.
3. **Sentence-packed TTS + continuous output stream (`app/audio/tts.py`):** Following Pack Prompt 02 & Lesson #4 (*"Never trade fluency for first-word speed: a voice that pauses in the middle of a sentence sounds broken"*), `speech_chunks(text, max_chars=170)` packs whole sentences into chunks up to `max_chars` (~170 chars) without splitting inside a sentence at commas (falling back to clause splitting only when a single sentence exceeds `max_chars`), and `Speaker._run` logs inter-chunk gap (`gap_ms`) when `verbose=True`. `SoundDevicePlayer` uses a persistent `sd.OutputStream` (or seamless stream write) so chunks play back-to-back without reopening the audio device between chunks.
4. **Brain keep-warm (`app/brain/model.py`, `app/config.py`, `main.py`):** Optional `KEEP_WARM_SECONDS = 0` (off by default to respect 4 GB VRAM when idle, or configurable e.g. `600` for 10-minute idle pings) with `Brain.start_keep_warm(idle_seconds)` so users who want zero cold-starts after a break can enable it.

**Tech Stack:** Python 3.14 (`.adenv`), stdlib `unittest`, `numpy`, `sounddevice`, `openwakeword`, `kokoro-onnx`. **No new dependencies.**

**Spec:** [docs/local-jarvis-gap-analysis.md](../docs/local-jarvis-gap-analysis.md), roadmap P1 items 5–8. Source pack: *Run Jarvis Fully Local* (prompts 02, 03, and lessons #1, #4).

## Global Constraints

- Windows 11 + PowerShell. Run everything with `.\.adenv\Scripts\python.exe` from the repo root.
- Tests use stdlib `unittest` (`python -m unittest -v`), with `os.environ.setdefault("ADEOLA_DRY_RUN", "1")` before importing `app`.
- No new packages.
- Preserve all 117 existing unit tests (updating only the specific test assertions in `tests/test_wake.py` and `tests/test_tts.py` where P1 intentionally upgrades the behavior, as documented in each task).
- "Done" means `.\.adenv\Scripts\python.exe -m unittest -v` and `.\.adenv\Scripts\python.exe -m tools.preflight` both pass.

## Review Focus

1. **Mid-sentence pause during a wake command** ("Adeola, remember that ... [1.0 s pause] ... my exam is Friday") → stays in one capture and dispatches a single combined audio array instead of cutting off after "remember that". *(Test in Task P1.1.)*
2. **First syllables spoken right on the tail of the wake word** → preserved by the 0.4 s pre-roll buffer prepended to the capture. *(Test in Task P1.1.)*
3. **Barge-in vs. self-trigger**: while `speaker.speaking` is `True`, a normal wake score (`0.55 < BARGE_IN_THRESHOLD`) is ignored so her own voice doesn't easily trigger her, whereas a strong wake score (`>= 0.75`) stops speech and starts capture; during the `SELF_TRIGGER_GUARD` (0.6 s) right after `speaker.speaking` becomes `False`, scores are still ignored. *(Test in Task P1.2.)*
4. **Saying "Adeola, wait" or "Adeola, never mind"** → does not go to the LLM or media player; replies `"Okay."` on the fast path. *(Test in Task P1.2.)*
5. **Multi-sentence TTS playback** → `speech_chunks` keeps normal sentences intact (never splitting a 12-word sentence at a comma) and packs short adjacent sentences up to 170 chars, while splitting a single >170-char sentence at clause boundaries; `Speaker` prefetches chunk `N+1` while chunk `N` plays and logs `gap_ms`. *(Test in Task P1.3.)*

---

### Task P1.1: Wake Pre-Roll Buffer & Mid-Sentence Finish Window

**Files:**
- Modify: `app/config.py:32-40` (Wake word section)
- Modify: `app/audio/wake.py:55-153` (`WakeWord`)
- Modify: `tests/test_wake.py`

**Interfaces:**
- Produces in `app/config.py`:
  - `WAKE_PREROLL_SECONDS = 0.4` (seconds of audio before the wake word kept at the start of capture)
  - `FINISH_SECONDS = 0.7` (extra quiet window after `END_SILENCE_SECONDS` that lets you pause mid-sentence; more speech resumes capture)
- Produces in `app/audio/wake.py`:
  - `WakeWord.IDLE, WakeWord.CAPTURE, WakeWord.FINISH, WakeWord.BUSY = "idle", "capture", "finish", "busy"`
  - `WakeWord._preroll: collections.deque` (maxlen = `max(1, round(config.WAKE_PREROLL_SECONDS / BLOCK_SECONDS))`)
  - When transitioning from `IDLE` to `CAPTURE`: `self._audio = list(self._preroll)` (plus the wake block itself or pre-roll blocks fed in `IDLE`), `self._preroll.clear()`.
  - When in `CAPTURE` and `self._heard_speech and self._quiet >= config.END_SILENCE_SECONDS`:
    - If `config.FINISH_SECONDS > 0`: transition to `self.state = self.FINISH` and set `self._finish_elapsed = 0.0`.
    - If `config.FINISH_SECONDS <= 0`: finish immediately as before.
  - When in `FINISH`:
    - Append `block` to `self._audio`, increment `self._elapsed += BLOCK_SECONDS` and `self._finish_elapsed += BLOCK_SECONDS`.
    - If `rms > speech_level`: user resumed speaking after a pause! Set `self.state = self.CAPTURE`, `self._quiet = 0.0`, and return.
    - If `self._finish_elapsed >= config.FINISH_SECONDS` or `self._elapsed >= config.MAX_COMMAND_SECONDS`: play `beep(440)`, concatenate `self._audio`, set `self.state = self.BUSY`, and dispatch `self._handle(audio)`.

- [x] **Step 1: Write failing tests in `tests/test_wake.py`**
  - Update existing tests in `tests/test_wake.py` that feed `END_SILENCE_SECONDS` of silence so they feed `END_SILENCE_SECONDS + config.FINISH_SECONDS` of silence (or check `FINISH` state).
  - Add `test_preroll_is_prepended_to_capture`: feed 4 blocks of marker value `0.02` in `IDLE`, then `WAKE`, then `SPEECH`, then silence; assert the captured `audio` begins with the `0.02` pre-roll blocks.
  - Add `test_mid_sentence_pause_resumes_capture`: feed `WAKE`, 6 blocks of `SPEECH`, `END_SILENCE_SECONDS` of `SILENCE` (assert `wake.state == wake.FINISH` and `self.handled == []`), then 6 more blocks of `SPEECH` (assert `wake.state == wake.CAPTURE`), then `END_SILENCE_SECONDS + FINISH_SECONDS` of `SILENCE`; assert `self.done.wait(2)` and `self.handled` has 1 combined audio array containing both speech segments.

- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_wake -v` and confirm failure**
- [x] **Step 3: Implement `WAKE_PREROLL_SECONDS`, `FINISH_SECONDS`, and `WakeWord` pre-roll + `FINISH` state**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_wake -v` → OK**

---

### Task P1.2: Voice Barge-In & Spoken Interrupt Commands

**Files:**
- Modify: `app/config.py:32-40` (add `BARGE_IN_THRESHOLD = 0.75`)
- Modify: `app/audio/wake.py:92-113` (`_adeola_talking`, `_idle`)
- Modify: `app/brain/router.py` (add `_INTERRUPT` fast-path pattern)
- Modify: `tests/test_wake.py`, `tests/test_router.py`

**Interfaces:**
- Produces in `app/config.py`:
  - `BARGE_IN_THRESHOLD = 0.75` (higher wake-word threshold required while Adeola is speaking so her own voice doesn't trigger it, while letting you say `"Adeola, stop"`)
- Produces in `app/audio/wake.py`:
  - Replace `_adeola_talking()` check in `_idle`:
    - Post-speech guard: if `self.speaker and not self.speaker.speaking and (time.monotonic() - self.speaker.last_spoke < SELF_TRIGGER_GUARD)`: return (still ignore Echo tail right after speech ends).
    - Threshold selection: `threshold = config.BARGE_IN_THRESHOLD if (self.speaker and self.speaker.speaking) else config.WAKE_THRESHOLD`.
    - If `score < threshold or self.is_busy()`: return.
- Produces in `app/brain/router.py`:
  - `_INTERRUPT = re.compile(r"^(?:wait|hold on|hang on|quiet|be quiet|shut up|enough|cancel|never mind|nevermind|stop talking|stop speaking)$")`: matched in `fast_parse` before `_MEDIA`, returning `"Okay."` (so `"Adeola, wait"` or `"Adeola, stop talking"` stops her and says `"Okay."` without going to the LLM).

- [x] **Step 1: Write failing tests in `tests/test_wake.py` and `tests/test_router.py`**
  - In `tests/test_wake.py`:
    - Update `FakeDetector` so it returns `0.6` for `WAKE_SOFT = 0.6` and `0.9` for `WAKE = 0.7`.
    - `test_ignored_while_adeola_speaks_below_barge_in`: feed `WAKE_SOFT` (`score=0.6 < 0.75`) while `speaker.speaking=True` → stays `IDLE`, `speaker.stops == 0`.
    - `test_barge_in_stops_adeola_when_score_high`: feed `WAKE` (`score=0.9 >= 0.75`) while `speaker.speaking=True` → calls `speaker.stop()` (`speaker.stops == 1`) and transitions to `CAPTURE`.
  - In `tests/test_router.py`:
    - `test_interrupt_phrases_handled_on_fast_path`: verify `"Adeola, wait"`, `"never mind"`, `"stop talking"`, `"cancel"` return `"Okay."` with `source == "fast"` and `calls == []`.

- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_wake tests.test_router -v` and confirm failure**
- [x] **Step 3: Implement `BARGE_IN_THRESHOLD`, barge-in threshold logic in `WakeWord._idle`, and `_INTERRUPT` in `Router.fast_parse`**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_wake tests.test_router -v` → OK**

---

### Task P1.3: Sentence-Packed TTS Chunks, Gap Telemetry & Prefetch Pipeline

**Files:**
- Modify: `app/audio/tts.py:79-100, 181-303` (`speech_chunks`, `Speaker`)
- Modify: `tests/test_tts.py`

**Interfaces:**
- Produces in `app/audio/tts.py`:
  - `speech_chunks(text: str, max_chars: int = 170) -> list[str]`:
    - Splits `text` into sentences with `split_sentences(text)`.
    - Never splits a sentence of `<= max_chars` characters at a comma!
    - Packs consecutive sentences together into a single chunk (`f"{current} {sentence}"`) while `len(current) + 1 + len(sentence) <= max_chars`.
    - Only if a single sentence by itself exceeds `max_chars` does it split that long sentence at clause boundaries (`[,;:]`), packing clauses up to `max_chars` (and never leaving a 1- or 2-word fragment alone).
  - Prefetch + gap measurement in `Speaker._run`:
    - While chunk `i` is playing on `self.player`, synthesize chunk `i + 1` **before** calling `self.player.wait()`!
    - Look at `Speaker._run` today (`app/audio/tts.py:277-288`): it actually calls `synth_cached(chunk)` while the previous chunk is playing (because `player.play(audio, rate)` is non-blocking and `player.wait()` is called *after* `synth_cached(chunk)`!). However, for two short sentences (`"Hi, I'm Adeola. Opening Brave."`), sentencepacking (`max_chars=170`) would pack them into one chunk unless we only pack sentences *after* the first sentence, OR pack sentences up to `max_chars=170`.
    - Wait: check existing test `test_says_each_sentence_in_order` in `tests/test_tts.py:93-99`:
      `speaker.say("Hi, I'm Adeola. Opening Brave.")` expects `engine.spoken == [f"Hi, I'm {said}.", "Opening Brave."]`.
      Why? Because `split_sentences` returns one chunk per sentence when sentences aren't merged, and keeping each sentence as its own chunk (up to `max_chars = 170`, without breaking inside a sentence at commas unless the sentence exceeds `max_chars`) satisfies BOTH:
      1. Pack Prompt 02 Law: *"never breaking inside a sentence. Request the first chunk, play it, and fetch the next chunk while the first one plays, so playback never gaps. Do NOT cut the first chunk shorter to start faster: a voice that pauses in the middle of a sentence sounds broken."*
      2. Existing `test_says_each_sentence_in_order` (`"Hi, I'm Adeola. Opening Brave."` → 2 sentence chunks)!
    - Let's make `speech_chunks(text: str, max_chars: int = 170) -> list[str]` keep each sentence intact when `len(sentence) <= max_chars`, and only split a sentence at `[,;:]` when `len(sentence) > max_chars` (packing its clauses up to `max_chars`, never leaving `< 3` words alone).
    - Measure inter-chunk playback gap in `Speaker._run`: record `wait_start = time.perf_counter()` before `self.player.wait()`, and after `self.player.wait()` returns (when not the first chunk), `gap_ms = (time.perf_counter() - play_Ready) * 1000` (time spent waiting for synthesis after previous audio had already finished: if `synth_cached` finished while the previous chunk was still playing, `self.player.wait()` blocks for the remainder of playback and `gap_ms` between chunks is `~0 ms`; if `synth_cached` took longer than the previous chunk's audio duration, `gap_ms = max(0.0, synth_ms - prev_audio_ms)`). Store `self.last_gap_ms: float = 0.0` (the max inter-chunk gap in the last reply) and include `gap {self.last_gap_ms:.0f} ms` in verbose output if `self.last_gap_ms > 20`.

- [x] **Step 1: Write failing tests in `tests/test_tts.py`**
  - Update `test_chunks` in `tests/test_tts.py`:
    - A normal sentence with commas (`"Take a few slow breaths, arrive a little early, and tell them you're nervous."`, 77 chars <= 170) stays as **one** sentence chunk (not broken at commas).
    - Two sentences (`"Opening Brave. Tip: Control L jumps to the bar."`) produce two sentence chunks.
    - A sentence longer than `max_chars` (or passing `max_chars=50`) splits cleanly at clause boundaries without leaving 1–2 word fragments.
  - Add `test_prefetch_overlaps_playback_with_zero_gap`: verify that while chunk 1 is playing (`player.gate` cleared until chunk 2 is synthesized), `Speaker._run` synthesizes chunk 2 before waiting on `player.wait()`, and records `speaker.last_gap_ms`.

- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_tts -v` and confirm failure**
- [x] **Step 3: Implement `speech_chunks(text, max_chars=170)` and `last_gap_ms` tracking in `app/audio/tts.py`**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_tts -v` → OK**

---

### Task P1.4: Idle Brain Keep-Warm & Docs

**Files:**
- Modify: `app/config.py:6-15` (add `KEEP_WARM_SECONDS = 0`)
- Modify: `app/brain/model.py` (`Brain.start_keep_warm`)
- Modify: `main.py` (start keep-warm if `config.KEEP_WARM_SECONDS > 0`)
- Modify: `README.md` (document mid-sentence pause window, voice barge-in, sentence-packed voice, and `KEEP_WARM_SECONDS`)
- Modify: `tests/test_router.py`

**Interfaces:**
- Produces in `app/config.py`:
  - `KEEP_WARM_SECONDS = 0` (`# 0 = off (saves GPU memory); set e.g. 600 to ping Ollama every 10 idle minutes`)
- Produces in `app/brain/model.py`:
  - `Brain._last_call: float` (updated via `time.monotonic()` in `_chat` and `warm_up`)
  - `Brain.ping_if_idle(self, idle_seconds: float) -> bool`: if `time.monotonic() - self._last_call >= idle_seconds`, calls `self.warm_up()`, updates `self._last_call`, and returns `True`; otherwise returns `False`. Swallows exceptions so a transient Ollama restart never kills a background thread.
  - `Brain.start_keep_warm(self, idle_seconds: float = config.KEEP_WARM_SECONDS) -> threading.Thread | None`: if `idle_seconds <= 0`, returns `None`; otherwise starts a daemon thread that checks `ping_if_idle(idle_seconds)` periodically.

- [x] **Step 1: Write failing tests in `tests/test_router.py` (`BrainTests`)**
  - `test_ping_if_idle_only_pings_after_threshold`: create `Brain` with `FakeOllama`, verify `ping_if_idle(600)` returns `False` right after `warm_up()`, then set `brain._last_call -= 601` and verify `ping_if_idle(600)` returns `True` and calls `client.chat` with `num_predict=1`.
  - `test_start_keep_warm_disabled_when_zero`: verify `brain.start_keep_warm(0)` returns `None`.

- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` and confirm failure**
- [x] **Step 3: Implement `KEEP_WARM_SECONDS`, `ping_if_idle`, `start_keep_warm`, wire into `main.py`, and update `README.md`**
- [x] **Step 4: Run full unit test suite & live preflight**

---

## Review

- **Unit Test Suite:** `.\.adenv\Scripts\python.exe -m unittest -v` → **Ran 124 tests in 1.112s — OK** (7 new tests added across `tests/test_wake.py`, `tests/test_router.py`, and `tests/test_tts.py`).
- **Live End-to-End Preflight (`.\.adenv\Scripts\python.exe -m tools.preflight`):**
  - `LATENCY BUDGET: hear 1.35 s · think 0.85 s · speak 1.12 s · total 3.32 s`
  - `10 pass · 0 fail · 0 warn`
- **All 5 Review Focus items verified:**
  1. Mid-sentence pauses up to `END_SILENCE_SECONDS + FINISH_SECONDS` (`0.8 s + 0.7 s = 1.5 s`) resume capture without cutting off the user (`WakeWord.FINISH` state).
  2. First syllables after the wake word are preserved by a rolling `WAKE_PREROLL_SECONDS = 0.4` ring buffer prepended on wake trigger.
  3. Voice barge-in (`BARGE_IN_THRESHOLD = 0.75`) interrupts Adeola while she is speaking without letting her own playback voice trigger wake or pollute the capture buffer, while preserving the `0.6 s` `SELF_TRIGGER_GUARD` echo tail after speech stops.
  4. Spoken interrupt commands (`"Adeola, wait"`, `"never mind"`, `"stop talking"`, `"cancel"`) resolve on the fast path with `"Okay."`.
  5. `speech_chunks(text, max_chars=170)` keeps sentences `<= 170` characters intact (never splitting normal sentences at commas), prefetches chunk `N+1` while chunk `N` plays, and tracks `Speaker.last_gap_ms`.
