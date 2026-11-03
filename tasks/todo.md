# Adeola P2: Brain & Voice Shelf, Character & Honesty, Capture Phrases — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Adeola's P2 roadmap from the *Run Jarvis Fully Local* gap analysis: a spoken Brain & Voice shelf (`"switch to your quick brain"`, `"which brain are you on"`, `"use your calm voice"`), a crisp `< 300`-word character & honesty block with rotating fast-path greetings and offline/cost honesty replies, and expanded natural capture phrases (`"note to self: ..."`, `"log this: ..."`, `"don't let me forget ..."`).

**Architecture:** We deliver P2 in three focused tasks:
1. **Task P2.1 — Brain & Voice Shelf (`app/config.py`, `app/brain/model.py`, `app/audio/tts.py`, `app/brain/router.py`, `main.py`):**
   - `config.BRAINS = {"quick": "qwen3:1.7b", "smart": MODEL, "coder": "qwen2.5-coder:3b"}` and `config.VOICES = {"default": VOICE_BLEND, "warm": "af_heart*0.8 + bf_emma*0.2", "calm": "bf_emma*0.7 + af_heart*0.3", "bright": "af_bella*0.6 + af_heart*0.4"}`.
   - `Brain.brain_name()` and `Brain.switch_model(name_or_tag)` in `app/brain/model.py`, warming the newly selected model and rolling back cleanly with the exact `ollama pull <model>` command if loading fails.
   - `Speaker.voice_name()` and `Speaker.switch_voice(name_or_blend)` in `app/audio/tts.py`.
   - Fast-path routing in `app/brain/router.py` for `"which brain are you on"`, `"switch to your quick brain"`, `"which voice are you using"`, and `"use your calm voice"`, plus wiring `router.speaker = speaker` in `main.py`.
2. **Task P2.2 — Character Block, Rotating Fast-Path Replies & Honesty Lines (`app/brain/model.py`, `app/brain/router.py`):**
   - Upgrade `BASE_PROMPT` in `app/brain/model.py` into a `< 300`-word persona block enforcing the Receipts Law, Local & Free Law, and Internet Honesty Law.
   - Add `GREETING_REPLIES` rotation on `Router` (starting with `GREETING_TEXT = "Hey! What can I do for you?"` on the first greeting, then cycling through natural variants on subsequent greetings).
   - Add fast-path honesty patterns (`_HONESTY_LOCAL`, `_HONESTY_INTERNET`) in `Router.fast_parse` so `"do you cost anything"`, `"are you local"`, `"do you run offline"`, and `"what needs the internet"` answer directly and truthfully without touching the LLM.
3. **Task P2.3 — Expanded Capture Phrases & Documentation (`app/brain/router.py`, `README.md`):**
   - Expand `_REMEMBER` in `app/brain/router.py` to support `"note to self: ..."`, `"log this: ..."`, `"log that ..."`, `"save a note: ..."`, `"jot down ..."`, and `"don't let me forget ..."`.
   - Update `README.md` and `HELP_TEXT` to document the Brain & Voice shelf, local honesty queries, and expanded capture phrases.

**Tech Stack:** Python 3.14 (`.adenv`), stdlib `unittest`. **No new dependencies.**

**Spec:** [docs/local-jarvis-gap-analysis.md](../docs/local-jarvis-gap-analysis.md), roadmap P2 items 10–12 (item 9, Notes Grounding, shipped in Batch 1). Source pack: *Run Jarvis Fully Local* (prompts 04, 05, 06).

## Global Constraints

- Windows 11 + PowerShell. Run everything with `.\.adenv\Scripts\python.exe` from the repo root.
- Tests use stdlib `unittest` (`python -m unittest -v`), with `os.environ.setdefault("ADEOLA_DRY_RUN", "1")` before importing `app`.
- No new packages.
- Preserve all 124 existing unit tests.
- "Done" means `.\.adenv\Scripts\python.exe -m unittest -v` and `.\.adenv\Scripts\python.exe -m tools.preflight` both pass.

## Review Focus

1. **Spoken brain shelf switching & rollback** (`"switch to your quick brain"`, `"which brain are you on"`) → switches `Brain.model`, updates `/no_think` suffix appropriately for Qwen3 vs non-Qwen3 models, calls `warm_up()`, and rolls back to the previous model with `Run: ollama pull <model>` if `warm_up()` raises. *(Test in Task P2.1.)*
2. **Spoken voice shelf switching** (`"use your calm voice"`, `"which voice are you using"`) → switches `Speaker` voice blend via `config.VOICES` and reports the active shelf name; handles `speaker=None` gracefully when running in text-only mode. *(Test in Task P2.1.)*
3. **Character block budget & honesty laws** → `BASE_PROMPT` stays strictly under 300 words, keeps a stable prefix, and fast-path honesty queries (`"do you cost anything"`, `"are you local"`, `"what needs the internet"`) return accurate local/offline answers without calling the LLM. *(Test in Task P2.2.)*
4. **Rotating fast-path greetings** → first greeting on a `Router` returns `"Hey! What can I do for you?"`, and consecutive greetings rotate through `GREETING_REPLIES`. *(Test in Task P2.2.)*
5. **Expanded capture phrases** (`"note to self: buy milk"`, `"log this: rotated the API key"`, `"don't let me forget that my exam is Friday"`) → captured on the fast path via `remember` tool with original casing preserved. *(Test in Task P2.3.)*

---

### Task P2.1: Brain & Voice Shelf

**Files:**
- Modify: `app/config.py`
- Modify: `app/brain/model.py`
- Modify: `app/audio/tts.py`
- Modify: `app/brain/router.py`
- Modify: `main.py`
- Modify: `tests/test_router.py`, `tests/test_tts.py`

**Interfaces:**
- Produces in `app/config.py`:
  - `BRAINS = {"quick": "qwen3:1.7b", "smart": MODEL, "coder": "qwen2.5-coder:3b"}`
  - `VOICES = {"default": VOICE_BLEND, "warm": "af_heart*0.8 + bf_emma*0.2", "calm": "bf_emma*0.7 + af_heart*0.3", "bright": "af_bella*0.6 + af_heart*0.4"}`
- Produces in `app/brain/model.py`:
  - `Brain._build_system_prompt(self, model: str) -> str`
  - `Brain.brain_name(self) -> str`: returns matching shelf key in `config.BRAINS` for `self.model`, or `self.model` if custom.
  - `Brain.switch_model(self, name_or_tag: str) -> tuple[bool, str]`: resolves `name_or_tag` in `config.BRAINS` (or treats a string containing `:` as an explicit model tag), updates `self.model` + `self.system_prompt` + `self._think_supported = True`, calls `self.warm_up()`, and on exception restores the previous state and returns `(False, f"Couldn't load {target} ({e}). Run: ollama pull {target}")`.
- Produces in `app/audio/tts.py`:
  - `Speaker.voice_name(self) -> str`: returns matching shelf key in `config.VOICES` for `self.blend`, or `self.blend` if custom.
  - `Speaker.switch_voice(self, name_or_blend: str) -> tuple[bool, str]`: resolves `name_or_blend` in `config.VOICES` (or raw blend string containing `*` or `+` or known voice id), calls `self.set_voice(blend)`, and returns `(True, f"Switched to my {label} voice.")` or `(False, ...)` on `ValueError`.
- Produces in `app/brain/router.py` & `main.py`:
  - `Router.__init__(self, toolbox: Toolbox, brain=None, notes: NotesIndex | None = None, speaker=None)` (`self.speaker = speaker`).
  - In `main.py`: set `router.speaker = speaker` after `start_speaker`.
  - Fast-path patterns `_WHICH_BRAIN`, `_SWITCH_BRAIN`, `_WHICH_VOICE`, `_SWITCH_VOICE` in `Router.fast_parse`.

- [x] **Step 1: Write failing tests in `tests/test_router.py` and `tests/test_tts.py`**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router tests.test_tts -v` and confirm failure**
- [x] **Step 3: Implement `BRAINS`, `VOICES`, `Brain.switch_model`, `Speaker.switch_voice`, and router shelf commands**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router tests.test_tts -v` → OK**

---

### Task P2.2: Character Block, Rotating Fast-Path Replies & Local Honesty Lines

**Files:**
- Modify: `app/brain/model.py`
- Modify: `app/brain/router.py`
- Modify: `tests/test_router.py`

**Interfaces:**
- Produces in `app/brain/model.py`:
  - `PERSONA` / `BASE_PROMPT` under 300 words (`len(BASE_PROMPT.split()) <= 300`) with Adeola's character and the 3 laws (Receipts, Local & Free, Internet Honesty).
- Produces in `app/brain/router.py`:
  - `GREETING_REPLIES = (GREETING_TEXT, "Hi! Ready when you are.", "Hey there — what are we working on?")` cycled by `Router._greeting_idx`.
  - `_HONESTY_LOCAL` and `_HONESTY_INTERNET` in `Router.fast_parse`.

- [x] **Step 1: Write failing tests in `tests/test_router.py`**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` and confirm failure**
- [x] **Step 3: Implement `BASE_PROMPT`, `GREETING_REPLIES` rotation, and honesty fast-path patterns**
- [x] **Step 4: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` → OK**

---

### Task P2.3: Expanded Capture Phrases & Documentation

**Files:**
- Modify: `app/brain/router.py`
- Modify: `README.md`
- Modify: `tests/test_memory.py`

**Interfaces:**
- Produces in `app/brain/router.py`:
  - Expanded `_REMEMBER` matching `"note to self: ..."`, `"note to self ..."`, `"log this: ..."`, `"log that ..."`, `"save a note: ..."`, `"jot down ..."`, `"don't let me forget (that) ..."`.
  - Updated `HELP_TEXT` mentioning brain/voice switching.
- Produces in `README.md`:
  - Documentation for the Brain & Voice shelf (`switch to your quick brain`, `which brain are you on`, `use your calm voice`), honesty questions, and expanded capture phrases (`note to self`, `log this`, `don't let me forget`).

- [x] **Step 1: Write failing tests in `tests/test_memory.py`**
- [x] **Step 2: Run `.\.adenv\Scripts\python.exe -m unittest tests.test_memory -v` and confirm failure**
- [x] **Step 3: Implement expanded `_REMEMBER`, update `HELP_TEXT` and `README.md`**
- [x] **Step 4: Run full unit test suite & live preflight**

---

## Review

- **Task P2.1 — Brain & Voice Shelf (`app/config.py`, `app/brain/model.py`, `app/audio/tts.py`, `app/brain/router.py`, `main.py`):**
  - Added `config.BRAINS = {"quick": "qwen3:1.7b", "smart": MODEL, "coder": "qwen2.5-coder:3b"}` and `config.VOICES = {"default": VOICE_BLEND, "warm": "af_heart*0.8 + bf_emma*0.2", "calm": "bf_emma*0.7 + af_heart*0.3", "bright": "af_bella*0.6 + af_heart*0.4"}`.
  - Implemented `Brain._build_system_prompt`, `Brain.brain_name()`, and `Brain.switch_model(name_or_tag)` with automatic `/no_think` handling, live `warm_up()`, and full state rollback (`model`, `system_prompt`, `_think_supported`) + `Run: ollama pull <model>` guidance on failure.
  - Implemented `Speaker.voice_name()` and `Speaker.switch_voice(name_or_blend)` and wired `router.speaker = speaker` in `main.py` plus fast-path shelf commands (`_WHICH_BRAIN`, `_SWITCH_BRAIN`, `_WHICH_VOICE`, `_SWITCH_VOICE`).
- **Task P2.2 — Character Block, Rotating Fast-Path Replies & Local Honesty Lines (`app/brain/model.py`, `app/brain/router.py`):**
  - Upgraded `BASE_PROMPT` (`< 300` words, ~105 words) with Adeola's character block and the three laws (Receipts Law, Local & Free Law, Internet Honesty Law).
  - Added `GREETING_REPLIES` rotation per `Router` instance starting with `GREETING_TEXT = "Hey! What can I do for you?"`.
  - Added fast-path honesty handlers (`_HONESTY_LOCAL`, `_HONESTY_INTERNET`) answering local/cost/offline/internet questions without calling the LLM.
- **Task P2.3 — Expanded Capture Phrases & Documentation (`app/brain/router.py`, `README.md`):**
  - Expanded `_REMEMBER` to match `"note to self: ..."`, `"log this: ..."`, `"log that ..."`, `"save a note: ..."`, `"jot down ..."`, and `"don't let me forget (that) ..."` while preserving original casing.
  - Updated `HELP_TEXT` and `README.md` with Brain & Voice shelf, local honesty, and expanded capture phrases.
- **Verification:**
  - `.\.adenv\Scripts\python.exe -m unittest -v`: **131 tests OK** (`1.042s`)
  - `.\.adenv\Scripts\python.exe -m tools.preflight`:
    - `LATENCY BUDGET: hear 0.88 s · think 0.63 s · speak 2.23 s · total 3.74 s`
    - `10 pass · 0 fail · 0 warn`
