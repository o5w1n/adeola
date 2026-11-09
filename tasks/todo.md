# Adeola OpenJarvis Spine — Implementation Plan & Todo

> **Spec:** [`docs/superpowers/specs/2026-11-09-openjarvis-spine-design.md`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/docs/superpowers/specs/2026-11-09-openjarvis-spine-design.md)  
> **Detailed Plan:** [`docs/superpowers/plans/2026-11-09-openjarvis-spine.md`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/docs/superpowers/plans/2026-11-09-openjarvis-spine.md)

**Goal:** Evolve Adeola from a single-shot command dispatcher into an OpenJarvis-style local personal AI with:
1. **Task 1 — Observation Tool Classification & `search_notes` Tool (`app/tools/registry.py`, `tests/test_notes.py`)**
2. **Task 2 — Bounded 2-Step Observe-Then-Answer Agent Loop (`app/brain/model.py`, `app/brain/router.py`, `tests/test_router.py`)**
3. **Task 3 — VRAM-Aware Auto-Brain Policy (`app/config.py`, `app/brain/policy.py`, `app/brain/router.py`, `tests/test_router.py`)**
4. **Task 4 — Local SQLite Trace Store & Spoken Trace Analyzer (`app/memory/store.py`, `app/brain/traces.py`, `app/brain/router.py`, `app/audio/stt.py`, `tests/test_memory.py`, `tests/test_router.py`)**

---

### Task 1: Observation Tool Classification & `search_notes` Tool (`app/tools/registry.py`, `tests/test_notes.py`)
- [x] **Step 1:** Write failing tests in `tests/test_notes.py` for `Toolbox.search_notes`, `ACTION_TOOLS`, and `OBSERVATION_TOOLS`
- [x] **Step 2:** Run `.\.adenv\Scripts\python.exe -m unittest tests.test_notes -v` and confirm failure
- [x] **Step 3:** Implement `ACTION_TOOLS`, `OBSERVATION_TOOLS`, and `Toolbox.search_notes` in `app/tools/registry.py`
- [x] **Step 4:** Run `.\.adenv\Scripts\python.exe -m unittest tests.test_notes -v` → OK

---

### Task 2: Bounded 2-Step Observe-Then-Answer Loop (`app/brain/model.py`, `app/brain/router.py`, `tests/test_router.py`)
- [x] **Step 1:** Write failing tests in `tests/test_router.py` (`TwoStepAgentLoopTests`) for 2-step synthesis, fallback on error, and 1-step action receipt preservation
- [x] **Step 2:** Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` and confirm failure
- [x] **Step 3:** Implement `Brain.synthesize` in `app/brain/model.py` and 2-step observation loop in `Router.handle` (`app/brain/router.py`)
- [x] **Step 4:** Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` → OK

---

### Task 3: VRAM-Aware Auto-Brain Routing Policy (`app/config.py`, `app/brain/policy.py`, `app/brain/router.py`, `tests/test_router.py`)
- [x] **Step 1:** Write failing tests in `tests/test_router.py` (`AutoBrainPolicyTests`) for task classification, VRAM thrash guard, manual pinning, `"use auto brain"`, and failed-switch fallback
- [x] **Step 2:** Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` and confirm failure
- [x] **Step 3:** Create `app/brain/policy.py`, add `AUTO_BRAIN` config in `app/config.py`, and wire `BrainPolicy` into `app/brain/router.py`
- [x] **Step 4:** Run `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` → OK

---

### Task 4: Local SQLite Trace Store & Spoken Trace Analyzer (`app/memory/store.py`, `app/brain/traces.py`, `app/brain/router.py`, `app/audio/stt.py`, `tests/test_memory.py`, `tests/test_router.py`)
- [x] **Step 1:** Write failing tests in `tests/test_memory.py` and `tests/test_router.py` (`TraceStoreAndIntrospectionTests`) for trace logging, summary report, and friction detection
- [x] **Step 2:** Run `.\.adenv\Scripts\python.exe -m unittest tests.test_memory tests.test_router -v` and confirm failure
- [x] **Step 3:** Implement `traces` table in `app/memory/store.py`, `TraceAnalyzer` in `app/brain/traces.py`, fast-path introspection commands in `app/brain/router.py`, and Whisper hints in `app/audio/stt.py`
- [x] **Step 4:** Run full test suite & `tools.preflight` verification


---

## Review

- **Task 1 (`app/tools/registry.py`, `tests/test_notes.py`):** Added `ACTION_TOOLS`, `OBSERVATION_TOOLS`, and `Toolbox.search_notes(query: str = "")` (plus `"search_notes"` schema).
- **Task 2 (`app/brain/model.py`, `app/brain/router.py`, `tests/test_router.py`):** Added `Brain.synthesize(text, observations)` and 2-step observe-then-answer synthesis in `Router.handle` (`source="agent"`, `steps=2`), with automatic fallback to deterministic tool outputs on empty/invalid JSON or synthesis error.
- **Task 3 (`app/config.py`, `app/brain/policy.py`, `app/brain/router.py`, `tests/test_router.py`):** Added `AUTO_BRAIN = True`, `AUTO_SWITCH_COOLDOWN_SECONDS = 60.0`, and `BrainPolicy` (`classify_task`, `recommend`, VRAM thrash guard that never downgrades warm `smart`/`coder` to `quick`, manual pinning on `"switch to your X brain"`, and reset on `"use auto brain"` / `"go local"`).
- **Task 4 (`app/memory/store.py`, `app/brain/traces.py`, `app/brain/router.py`, `app/audio/stt.py`, `tests/test_memory.py`, `tests/test_router.py`):** Added `traces` SQLite table (`Trace`, `add_trace`, `recent_traces`), `TraceAnalyzer` (`summary_report`, `friction_report`), automatic trace recording across all `Router.handle` paths (including `_NOTES_Q` and `pending_offer`), fast-path introspection commands (`"how have you been running"`, `"show traces"`, `"what did you mishear"`), and Whisper vocabulary hints.
- **Final Review & Verification:**
  - Whole-branch review by `pro` subagent completed; all 3 Critical/Important findings fixed via TDD (`RED -> GREEN`).
  - Full test suite: `163 tests OK`.
