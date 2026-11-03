# Adeola: Reliability Batch + Notes Grounding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Adeola trustworthy (she never crashes or goes quiet, never cuts off a reply, really works offline, and has a live preflight that proves it), and let her answer questions from the `qabw` Obsidian vault.

**Architecture:** Part A hardens the existing paths: `Toolbox.run`/`Router.handle` catch every error, `Brain.decide` retries a reply that got cut off, Whisper loads offline-first, and a `netwatch` module records outbound connections. A new `tools/preflight.py` runs the real chain end to end (brain → voice → ears → memory → offline) and prints a hear/think/speak latency budget. Part B adds a read-only `NotesIndex` over markdown folders, scored by keyword overlap with title matches weighted higher. The router feeds the best excerpts to the model, both for explicit "what do my notes say about X" questions and for any model-path question that clearly matches. Sources are printed, not spoken.

**Tech Stack:** Python 3.14 (`.adenv`), stdlib `unittest`, Ollama (`qwen3:4b`), faster-whisper, kokoro-onnx, rapidfuzz. **No new dependencies.**

**Spec:** [docs/local-jarvis-gap-analysis.md](../docs/local-jarvis-gap-analysis.md), roadmap items P0 1–4 and P2 item 9. Source pack: *Run Jarvis Fully Local* (prompts 01, 05, 07, 08).

## Global Constraints

- Windows 11 + PowerShell. Run everything with `.\.adenv\Scripts\python.exe` from the repo root.
- Tests use stdlib `unittest` (`python -m unittest -v`), the same style as `tests/test_router.py`: set `ADEOLA_DRY_RUN=1` before importing, use fakes instead of Ollama/audio.
- No new packages. Only stdlib plus what is already in `requirements.txt`.
- **The vault is read-only.** Adeola never writes, moves or deletes anything under `NOTES_DIRS`.
- After the first-run downloads, a normal turn opens **zero** non-loopback connections.
- Spoken output never includes source lists or file paths. Sources are printed only.
- Receipts law stays: when tools run, the reply comes from the tool, not from the model's prose.
- One commit per task, with a message in the repo's style (`Short imperative summary`).

## Review Focus

1. **Vault missing or moved** (`~/Documents/qabw` absent) → Adeola still boots, prints one line ("Notes folder not found: …"), and preflight shows WARN, not a crash. *(Test in Task B1 + B3.)*
2. **Odd vault files**: non-UTF-8 bytes, very large exports, `.obsidian/` internals → skipped, no exception, boot prints the note count and index time. *(Test in Task B1.)*
3. **Vague question** ("what about it", "is that ok") → no notes attached and no fake "(from your notes: …)" line. *(Test in Task B1 + B2.)*
4. **Ollama is down when preflight runs** → that check FAILs with a "start Ollama / `ollama pull qwen3:4b`" hint, the other checks still run, and the exit code is non-zero, with no traceback. *(Test in Task A4.)*
5. **"search my notes for X"** must not open a Google search; "search for X" still must. *(Test in Task B2.)*

---

## Task 0: Baseline

- [x] **Step 1: Run the full suite outside the sandbox** (the sandbox has no audio device, so `test_wake`'s beeps fail and `test_media`'s winrt calls hang there)

Run: `.\.adenv\Scripts\python.exe -m unittest -v`
Expected: all ~75 tests OK. Sandbox-only result seen 2026-11-03: router/memory/refinements/tts/voice OK, media TIMEOUT, wake crashed on `winsound.Beep`. If anything fails outside the sandbox too, stop and record it in the Review section before continuing.

---

## Part A: Reliability

### Task A1: Never fail silently

**Files:**
- Modify: `app/tools/registry.py:227-230` (`Toolbox.run` except clause)
- Modify: `app/brain/router.py:165-207` (`Router.handle`)
- Modify: `app/audio/push_to_talk.py:22-25` (`beep`)
- Test: `tests/test_resilience.py` (new)

**Interfaces:**
- Produces: `Toolbox.run(name, args) -> str` never raises. `Result.source` gains the value `"error"`.

- [x] **Step 1: Write failing tests** in `tests/test_resilience.py`

```python
class BrokenMemory(Memory):           # Memory(":memory:") subclass
    def add_fact(self, text): raise sqlite3.OperationalError("database is locked")

def test_tool_error_is_spoken_not_raised(self):
    r = Router(Toolbox(FAKE_APPS, BrokenMemory(":memory:")), None)
    res = run(r, "remember that my exam is on friday")
    self.assertIn("didn't work", res.reply.lower())
    self.assertIn("OperationalError", res.reply)

def test_fast_path_crash_is_caught(self):
    tb = Toolbox(FAKE_APPS, Memory(":memory:"))
    tb.tips.set_enabled = mock.Mock(side_effect=RuntimeError("boom"))
    res = run(Router(tb, None), "no more tips")
    self.assertEqual(res.source, "error")
    self.assertIn("still here", res.reply.lower())

def test_beep_failure_is_swallowed(self):
    with mock.patch("winsound.Beep", side_effect=RuntimeError("Failed to beep")):
        beep(880)   # must not print a traceback from the thread
```

- [x] **Step 2: Run and confirm they fail**

Run: `.\.adenv\Scripts\python.exe -m unittest tests.test_resilience -v`
Expected: the first two raise `OperationalError` / `RuntimeError`.

- [x] **Step 3: Implement**
  - `Toolbox.run`: keep the existing `except OSError` message, then add `except Exception as e: return f"That didn't work ({e.__class__.__name__}: {e})."`
  - `Router.handle`: wrap the fast-path block (`fast_parse` + `run_calls`) in `try/except Exception` → `Result(f"Something went wrong ({e.__class__.__name__}). I'm still here.", source="error")`. The model path keeps its own guard.
  - `beep`: the thread target calls a small `_safe_beep(freq)` that swallows `RuntimeError`.

- [x] **Step 4: Run the whole suite** — `.\.adenv\Scripts\python.exe -m unittest -v` → OK
- [x] **Step 5: Commit** — `git commit -m "Never crash on a failing tool: speak the error instead"`

---

### Task A2: Retry a reply that got cut off

**Files:**
- Modify: `app/config.py` (Model section): add `RETRY_OUTPUT_TOKENS = 400`
- Modify: `app/brain/model.py:97-147` (`_chat`, `decide`)
- Test: `tests/test_router.py` (`BrainTests`)

**Interfaces:**
- Produces: `Brain._chat(messages, num_predict: int)`. `Brain.last_stats["retried"]: bool`.

- [x] **Step 1: Write failing tests**, using a `SeqOllama` fake that returns queued contents and records each request's `options["num_predict"]`

```python
def test_truncated_json_is_retried_with_bigger_budget(self):
    client = SeqOllama(['{"actions": [], "reply": "A long answer that got cu',
                        '{"actions": [], "reply": "Full answer."}'])
    calls, reply = self.make(client, "json").decide("tell me about cats")
    self.assertEqual(reply, "Full answer.")
    self.assertEqual(client.budgets, [config.MAX_OUTPUT_TOKENS, config.RETRY_OUTPUT_TOKENS])

def test_done_reason_length_triggers_retry(self):   # valid JSON but done_reason="length"
    ...assert len(client.budgets) == 2

def test_two_bad_replies_give_friendly_line_and_stop(self):
    client = SeqOllama(["{bad", "{still bad"])
    _, reply = self.make(client, "json").decide("hi there")
    self.assertIn("train of thought", reply)
    self.assertEqual(len(client.budgets), 2)

def test_good_reply_is_not_retried(self):
    ...assert len(client.budgets) == 1
```

- [x] **Step 2: Run** `.\.adenv\Scripts\python.exe -m unittest tests.test_router -v` → the new tests FAIL
- [x] **Step 3: Implement.** In json mode, retry once with `RETRY_OUTPUT_TOKENS` when `json.loads` fails **or** `getattr(resp, "done_reason", "") == "length"`. Parse the second response with the existing `_parse_json` (so it still goes through the tool enum/allowlist). Set `last_stats["retried"] = True`. Tools mode is unchanged.
- [x] **Step 4: Run the whole suite** → OK
- [x] **Step 5: Commit** — `git commit -m "Retry once with a bigger budget when a model reply is cut off"`

---

### Task A3: Offline hardening + connection watcher

**Files:**
- Modify: `app/audio/stt.py` (model loading)
- Create: `app/netwatch.py`
- Modify: `requirements.txt` (pin with `==`)
- Test: `tests/test_offline.py` (new)

**Interfaces:**
- Produces:
  - `load_whisper(name: str) -> WhisperModel` in `app/audio/stt.py` (offline-first).
  - `class ConnectionLog` in `app/netwatch.py`: a context manager with `.external: list[str]` (de-duplicated, non-loopback hosts, hostnames where known, otherwise IPs).
  - `NEEDS_INTERNET: dict[str, str]` in `app/netwatch.py`: `{"web_search": "opens Google in your browser", "open_url": "opens websites in your browser", "first run": "downloads Whisper, Kokoro and wake-word models once"}`.

- [x] **Step 1: Write failing tests**

```python
def test_whisper_loads_offline_first(self):
    seen = []
    class FakeWM:
        def __init__(self, name, **kw): seen.append(kw.get("local_files_only", False))
    with mock.patch("faster_whisper.WhisperModel", FakeWM):
        load_whisper("base.en")
    self.assertEqual(seen, [True])

def test_whisper_downloads_when_not_cached(self):
    # FakeWM raises on local_files_only=True → expect seen == [True, False]

def test_loopback_is_not_external(self):
    srv = socket.create_server(("127.0.0.1", 0)); port = srv.getsockname()[1]
    with ConnectionLog() as log:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    self.assertEqual(log.external, [])

def test_external_attempt_is_logged_even_if_it_fails(self):
    with ConnectionLog() as log:
        with contextlib.suppress(OSError):
            socket.create_connection(("203.0.113.1", 80), timeout=0.05)  # TEST-NET, unroutable
    self.assertEqual(log.external, ["203.0.113.1"])

def test_patch_is_removed_on_exit(self):
    before = socket.socket.connect
    with ConnectionLog(): pass
    self.assertIs(socket.socket.connect, before)
```

- [x] **Step 2: Run** `.\.adenv\Scripts\python.exe -m unittest tests.test_offline -v` → FAIL (import errors)
- [x] **Step 3: Implement.**
  - `load_whisper`: `WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=config.STT_THREADS, local_files_only=True)`. On any `Exception`, retry the same call without `local_files_only` (that is the first-run download). `Transcriber.__init__` calls it.
  - `ConnectionLog`: patch `socket.socket.connect` (records the address) and `socket.getaddrinfo` (maps IP → hostname). Loopback means `127.*`, `::1` or `localhost`. Restore both on `__exit__`.
- [x] **Step 4: Pin dependencies.** `.\.adenv\Scripts\python.exe -m pip freeze`, then replace every `>=` in `requirements.txt` with `==<installed version>` and keep the comments and platform markers. Verify with `.\.adenv\Scripts\python.exe -m pip check` → "No broken requirements found."
- [x] **Step 5: Run the whole suite** → OK
- [x] **Step 6: Commit** — `git commit -m "Load Whisper offline-first, pin dependencies, add connection watcher"`

---

### Task A4: Live preflight with a latency budget

**Files:**
- Create: `tools/preflight.py`
- Test: `tests/test_preflight.py` (new, pure logic only; the live run is the verification step)
- Modify: `README.md` (new "Preflight" section under "Test")

**Interfaces:**
- Consumes: `load_whisper` / `Transcriber` (A3), `ConnectionLog`, `NEEDS_INTERNET` (A3), `Brain`, `SCHEMAS`, `Speaker`/`load_kokoro`, `Memory`, `wake_model_ref`.
- Produces (in `tools/preflight.py`):
  - `@dataclass Check(name: str, status: str, detail: str, seconds: float | None = None)` with `status` in `{"PASS", "FAIL", "WARN"}`
  - `run_check(name: str, fn: Callable[[], str | tuple[str, float]], warn_only: bool = False) -> Check`. Never raises. An exception becomes FAIL (or WARN if `warn_only`) with detail `f"{type}: {msg}"`.
  - `loop_match(expected: str, heard: str) -> float`: `rapidfuzz.fuzz.ratio` on lowercased text with punctuation stripped. Pass threshold `LOOP_MIN = 85`.
  - `summary(checks: list[Check]) -> tuple[str, int]`: `("N pass · N fail · N warn", exit_code)`, where exit code is 1 if any FAIL, else 0.
  - `budget_line(hear: float | None, think: float | None, speak: float | None) -> str`: `"LATENCY BUDGET: hear 0.42 s · think 1.10 s · speak 0.35 s · total 1.87 s"`, with `"n/a"` for missing parts.
  - `main() -> int`

- [x] **Step 1: Write failing tests**

```python
def test_exception_becomes_fail_not_traceback(self):
    c = run_check("Ollama", lambda: (_ for _ in ()).throw(ConnectionError("refused")))
    self.assertEqual((c.status, "ConnectionError" in c.detail), ("FAIL", True))

def test_warn_only_downgrades(self):
    self.assertEqual(run_check("Mic", lambda: 1/0, warn_only=True).status, "WARN")

def test_summary_exit_codes(self):
    self.assertEqual(summary([Check("a","PASS",""), Check("b","WARN","")]), ("1 pass · 0 fail · 1 warn", 0))
    self.assertEqual(summary([Check("a","FAIL","")])[1], 1)

def test_loop_match_tolerates_small_differences(self):
    self.assertGreaterEqual(loop_match("Good afternoon. The local voice is live.",
                                       "good afternoon the local voices live"), LOOP_MIN)
    self.assertLess(loop_match("Good afternoon. The local voice is live.", "open notepad"), LOOP_MIN)

def test_budget_line(self):
    self.assertEqual(budget_line(0.5, 1.0, None),
                     "LATENCY BUDGET: hear 0.50 s · think 1.00 s · speak n/a · total 1.50 s")
```

- [x] **Step 2: Run** `.\.adenv\Scripts\python.exe -m unittest tests.test_preflight -v` → FAIL
- [x] **Step 3: Implement `main()`.** Checks in this order, each printed as `✓`/`✗`/`!` + name + detail + seconds:
  1. **Ollama + model**: `ollama.Client().list()` contains `config.MODEL`. FAIL detail: `"Start Ollama, then: ollama pull qwen3:4b"`.
  2. **Brain, tool choice**: `Brain(SCHEMAS).decide("open notepad")` returns an `open_app` call.
  3. **Brain, chat** (= **think**): `decide("Suggest a name for a cat.")` returns a non-empty reply. Record its seconds.
  4. **Voice** (= **speak**, `warn_only`): `load_kokoro()`, then time the synthesis of the first chunk of `LOOP_SENTENCE = "Good afternoon. The local voice is live."`
  5. **The loop** (= **hear**): resample that audio 24 kHz → 16 kHz (`np.interp`), run `Transcriber().transcribe`, require `loop_match ≥ LOOP_MIN`. WARN if voice or ears are missing, FAIL on a mismatch.
  6. **Memory round-trip**: on the real `config.MEMORY_DB`: `add_fact("preflight zebra check")` → `search("zebra")` finds it → `delete_fact`. Always delete in a `finally`.
  7. **Wake word** (`warn_only`): `wake_model_ref()[1] == config.ASSISTANT_NAME`, else WARN "still on 'hey jarvis', see docs/wake_word.md".
  8. **Microphone** (`warn_only`): `sounddevice.query_devices(kind="input")`.
  9. **Offline**: checks 2–5 run inside one `ConnectionLog`. PASS if `log.external == []`, else FAIL listing the hosts.

  Then print `Needs the internet:` plus `NEEDS_INTERNET`, then `budget_line(...)`, then `summary(...)`. `sys.exit(code)`.
- [x] **Step 4: Run the unit tests** → OK; the whole suite → OK
- [x] **Step 5: Live verification** (outside the sandbox: needs Ollama, an audio device and the real DB)

Run: `.\.adenv\Scripts\python.exe -m tools.preflight`
Expected: checks 1–6 and 9 ✓, any WARNs explained, and a `LATENCY BUDGET` line with real numbers. Exit code 0. Paste both final lines into the Review section. If anything FAILs, fix the cause and re-run. Then stop Ollama and re-run: check 1 ✗ with the hint, the rest still print, exit code 1.

- [x] **Step 6: README + commit.** README "Preflight" section: what it checks, how to read the budget line, and the rule *"Done" means preflight passed*. `git commit -m "Add live preflight with latency budget and offline check"`

---

## Part B: Answers from the qabw vault

### Task B1: NotesIndex (read-only markdown index)

**Files:**
- Modify: `app/config.py` (new `# --- Notes ---` section)
- Create: `app/memory/notes.py`
- Test: `tests/test_notes.py` (new)

**Config values:**
```python
NOTES_DIRS = [os.getenv("ADEOLA_NOTES", str(Path.home() / "Documents" / "qabw"))]
NOTES_EXCLUDE = (".obsidian", ".trash", ".git", "Templates")   # folder names skipped anywhere
NOTES_MAX_BYTES = 200_000     # skip bigger files (exports, pasted logs)
NOTES_IN_PROMPT = 3           # excerpts sent to the model per question
NOTES_EXCERPT_CHARS = 600
NOTES_MIN_SCORE = 3           # below this a note isn't attached to a general question
```

**Interfaces:**
- Consumes: `words(text) -> set[str]` from `app/memory/store.py` (same stopwords as memory).
- Produces (in `app/memory/notes.py`):
  - `@dataclass Note(title: str, path: Path, folder: str, body: str)`. `title` is the filename stem; `body` has frontmatter stripped, `[[a|b]]` → `b` and `[[a]]` → `a`.
  - `class NotesIndex(dirs: list[str | Path])` with:
    - `.notes: list[Note]`, `.missing: list[Path]`, `.load_ms: float`
    - `.load() -> int`: (re)scans and returns the count. Reads with `errors="ignore"` and never raises on a single bad file.
    - `.search(query: str, limit: int = config.NOTES_IN_PROMPT, min_score: int = config.NOTES_MIN_SCORE) -> list[Note]`
    - `.excerpt(note: Note, query: str, chars: int = config.NOTES_EXCERPT_CHARS) -> str`: a window around the first query-word hit (the start of the body if there is none), at most `chars` long.
  - Scoring: `3 × |query words ∩ title words| + |query words ∩ body words|`. Word sets are computed once in `load()`. Sort by score, then shorter path.

- [x] **Step 1: Write failing tests** with a temp vault: `Finish Window.md` (frontmatter + "set FINISH_MS to 900"), `Daily/2026-11-01.md` (mentions "finish window" once in a long body), `Ideas.md` (with `[[Finish Window|the window note]]`), `.obsidian/workspace.md` (contains "finish window"), `Big.md` (> `NOTES_MAX_BYTES`), `Bad.md` (raw bytes `b"\xff\xfe finish \x00"`)

```python
def test_title_match_ranks_first(self):
    self.assertEqual(idx.search("finish window")[0].title, "Finish Window")
def test_obsidian_internals_and_big_files_skipped(self):
    titles = {n.title for n in idx.notes}
    self.assertNotIn("workspace", titles); self.assertNotIn("Big", titles)
def test_bad_bytes_do_not_crash(self):
    self.assertIn("Bad", {n.title for n in idx.notes})
def test_frontmatter_and_wikilinks_cleaned(self):
    n = next(n for n in idx.notes if n.title == "Ideas")
    self.assertIn("the window note", n.body); self.assertNotIn("[[", n.body)
    self.assertFalse(next(n for n in idx.notes if n.title == "Finish Window").body.startswith("---"))
def test_vague_question_attaches_nothing(self):
    self.assertEqual(idx.search("what about it"), [])
def test_missing_folder_is_reported_not_raised(self):
    i = NotesIndex([tmp / "nope"]); self.assertEqual(i.load(), 0); self.assertEqual(i.missing, [tmp / "nope"])
def test_excerpt_is_bounded_and_centred(self):
    ex = idx.excerpt(idx.search("finish window")[0], "finish window", chars=80)
    self.assertLessEqual(len(ex), 80); self.assertIn("900", ex)
```

- [x] **Step 2: Run** `.\.adenv\Scripts\python.exe -m unittest tests.test_notes -v` → FAIL
- [x] **Step 3: Implement** `app/memory/notes.py` and the config section
- [x] **Step 4: Real-vault timing** (outside the sandbox; the sandbox can't read `Documents\qabw`): `.\.adenv\Scripts\python.exe -c "from app.memory.notes import NotesIndex; from app import config; i=NotesIndex(config.NOTES_DIRS); print(i.load(), 'notes', round(i.load_ms), 'ms', i.missing)"`. Expect > 0 notes. If `load_ms > 1500`, note it in Review (a background load can come later; don't build it now).
- [x] **Step 5: Run the whole suite** → OK
- [x] **Step 6: Commit** — `git commit -m "Add read-only markdown notes index for the qabw vault"`

---

### Task B2: Route notes questions to the model, with sources

**Files:**
- Modify: `app/brain/router.py` (`Result`, `Router.__init__`, `handle`, new `_NOTES_Q`, `notes_for`)
- Modify: `app/brain/model.py:113-133` (`decide` gains `notes`)
- Modify: `main.py` (`build`, boot line, `reload`, sources print)
- Modify: `app/audio/push_to_talk.py:73-96` (print sources)
- Modify: every test `FakeBrain.decide` (`tests/test_router.py:33,173`, `tests/test_memory.py:32`, `tests/test_refinements.py:32`) → `def decide(self, text, facts=None, notes=None)`
- Test: `tests/test_notes.py` (add `RouterNotesTests`, `BrainNotesTests`)

**Interfaces:**
- Consumes: `NotesIndex`, `Note` (B1).
- Produces:
  - `Router(toolbox, brain=None, notes: NotesIndex | None = None)`
  - `Router.notes_for(text: str, min_score: int = config.NOTES_MIN_SCORE) -> list[tuple[str, str]]` returns `(title, excerpt)` pairs
  - `Result.sources: list[str]` (default `[]`); new `Result.source` value `"notes"`
  - `Brain.decide(text, facts=None, notes: list[tuple[str, str]] | None = None)`
  - `build()` in `main.py` returns a Router whose `.notes` is loaded

- [x] **Step 1: Write failing tests**

```python
def test_explicit_notes_question_uses_notes_and_cites(self):
    brain = FakeBrain(reply="Set it to 900 ms.")
    res = run(Router(tb, brain, idx), "what do my notes say about the finish window")
    self.assertEqual(res.source, "notes")
    self.assertEqual(res.sources, ["Finish Window"])
    self.assertEqual(brain.seen_notes[0][0][0], "Finish Window")

def test_no_match_says_so_without_model_or_web(self):
    brain = FakeBrain()
    res = run(Router(tb, brain, idx), "search my notes for zebras")
    self.assertEqual(res.reply, "Your notes don't mention zebras.")
    self.assertEqual(brain.seen, []); self.assertEqual(res.calls, [])

def test_plain_search_still_goes_to_web(self):
    self.assertEqual(run(Router(tb, None, idx), "search for zebras").calls[0][0], "web_search")

def test_implicit_relevant_question_gets_notes(self):
    brain = FakeBrain(reply="900 ms.")
    res = run(Router(tb, brain, idx), "should the finish window be 900 milliseconds")
    self.assertTrue(brain.seen_notes[0]); self.assertEqual(res.sources, ["Finish Window"])

def test_vague_question_gets_no_notes_or_sources(self):
    brain = FakeBrain(reply="Sure.")
    res = run(Router(tb, brain, idx), "is that ok")
    self.assertFalse(brain.seen_notes[0]); self.assertEqual(res.sources, [])

def test_tool_turns_never_carry_sources(self):    # model picks open_app on a notes-ish question
    brain = FakeBrain(calls=[("open_app", {"name": "notepad"})])
    self.assertEqual(run(Router(tb, brain, idx), "open the finish window notes in notepad").sources, [])

def test_notes_block_sits_between_history_and_user(self):   # BrainNotesTests with FakeOllama
    msgs = client.kw["messages"]
    self.assertEqual(msgs[0]["content"], brain.system_prompt)   # stable prefix untouched
    self.assertIn("### Finish Window", msgs[-2]["content"]); self.assertEqual(msgs[-1]["role"], "user")
```

- [x] **Step 2: Run** `.\.adenv\Scripts\python.exe -m unittest tests.test_notes -v` → new tests FAIL
- [x] **Step 3: Implement**
  - `_NOTES_Q = re.compile(r"^(?:what do(?:es)? my (?:notes|vault) say about|(?:check|search|look (?:in|through)) my (?:notes|vault)(?: for| about)?|(?:in|according to) my notes,?) (?:the )?(.+)$")`. It is matched in `handle()` **before** `fast_parse` (so `_SEARCH` can't take it), right after the pending-offer block.
  - Explicit question: `notes_for(topic, min_score=1)`. If empty → `f"Your notes don't mention {topic}."`. If there is no brain → `"Your notes on that: A, B."`. Otherwise `brain.decide(cased_text, facts, notes=…)`, `source="notes"`, `sources=[titles]`.
  - Implicit: the model path passes `notes_for(text)`. `sources` are set only when the turn ends with no tool calls and the reply is non-empty.
  - `Brain.decide`: when `notes` is set, add one system message after `memory` and before the user: `"Excerpts from the user's notes. Answer from them when relevant and name the note; if they don't cover the question, say so plainly:\n" + "\n\n".join(f"### {t}\n{x}" for t, x in notes)`. `self.system_prompt` is unchanged (stable prefix).
  - `main.py`: `build()` creates and loads `NotesIndex(config.NOTES_DIRS)`. The boot line adds `"and {n} notes"`, or prints `"Notes folder not found: <path>"`. `reload` also calls `router.notes.load()`. After printing a reply, if `result.sources`, print `f"   (from your notes: {', '.join(result.sources)})"`, not spoken. `PushToTalk.process` does the same.
  - Add `"what do my notes say about …"` to `HELP_TEXT`.
- [x] **Step 4: Run the whole suite** → OK (the updated FakeBrains keep the old tests green)
- [x] **Step 5: Commit** — `git commit -m "Answer from the qabw vault, with sources printed"`

---

### Task B3: Preflight notes check + docs

**Files:**
- Modify: `tools/preflight.py` (new check, between Memory and Wake word)
- Modify: `README.md` (new "Answers from your notes" section; update the "How it works" table and "Next phases")
- Test: `tests/test_preflight.py`

**Interfaces:**
- Consumes: `NotesIndex` (B1), `run_check` (A4).
- Produces: `check_notes(index: NotesIndex) -> str` in `tools/preflight.py`. Raises `FileNotFoundError` if `index.missing` and nothing was loaded; raises `AssertionError` if searching the first note's title doesn't return that note. Wrapped with `warn_only=True`.

- [x] **Step 1: Write failing tests**: a temp vault with 2 notes → `run_check("Notes", lambda: check_notes(idx), warn_only=True).status == "PASS"` and the detail contains `"2 notes"`; a missing folder → `"WARN"`.
- [x] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Whole suite** → OK
- [x] **Step 5: Live end to end** (outside the sandbox)
  - `.\.adenv\Scripts\python.exe -m tools.preflight` → Notes ✓ with the real count; final two lines pasted into Review.
  - `.\.adenv\Scripts\python.exe main.py -v` → ask one question whose answer lives in a single vault note. Expect a correct short answer plus the `(from your notes: …)` line. Ask "search my notes for zebras" → "Your notes don't mention zebras." and no browser opens.
- [x] **Step 6: Commit** — `git commit -m "Preflight checks the notes index; document notes answers"`

---

## Out of scope (later batches, from the gap analysis)

P1: wake pre-roll + finish window, a continuous playback stream / chunk-gap measurement, voice barge-in, brain keep-warm. P2: brain/voice shelf, character block, more capture phrases, writing captures into the vault (needs the vault's `Operating Guide.md` conventions first).

## Review

- **Unit test suite (`python -m unittest -v`):** 117/117 tests pass in 3.08 s (up from 60 baseline tests; includes `test_media`, `test_wake`, `test_resilience`, `test_offline`, `test_preflight`, `test_notes`, `test_router`, `test_memory`, `test_refinements`, `test_tts`, `test_voice`).
- **Live preflight (`python -m tools.preflight`):**
  - `LATENCY BUDGET: hear 0.78 s · think 1.20 s · speak 0.85 s · total 2.83 s`
  - `10 pass · 0 fail · 0 warn` (all 10 live checks pass: Ollama `qwen3:4b`, tool choice, chat, Kokoro voice, TTS→Whisper loop at 100% match, SQLite memory round-trip, `qabw` notes index, custom `adeola.onnx` wake word, microphone, and 0 external connections during checks 2–5).
- **Real `qabw` vault indexing & Q&A:**
  - 478 markdown notes indexed (3601 ms cold disk cache, 176.5 ms warm).
  - Live question `"what do my notes say about Operating Guide"` returned a grounded summary citing `Operating Guide` and `🧠 Home`.
  - Live question `"search my notes for zebras"` returned `"Your notes don't mention zebras."` with `calls=[]` (no browser opened).
  - Note: Some `qabw` note titles contain emoji (e.g. `🧠 Home.md`). Added `sys.stdout.reconfigure(errors="replace")` in `main.py` so redirected Windows `cp1252` pipes never raise `UnicodeEncodeError` when printing `(from your notes: ...)`.

