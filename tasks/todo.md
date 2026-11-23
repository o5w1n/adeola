# Adeola — Gemini Remote Brain, Eval Harness & JARVIS OS Plan

**Baseline Commit:** `8614a9e` (`feat(hud): add live EventBus, SSE telemetry, and WebGL plasma orb / radar dashboard`)

**Goal:** Overcome the 6 GB VRAM ceiling by wiring a provider-agnostic remote brain (**Gemini** first, OpenAI-compatible so Groq/OpenRouter/Claude drop in later), building an **Eval Harness** to benchmark local vs. cloud, adding a **Tier 3 Agent Harness** for multi-step work, and layering in the **JARVIS OS** stages (Skills, Vault outputs, HUD skill buttons).

---

## Phase 1: Eval Harness (Baseline & Scoreboard)
- [x] **1.1** Extend [`tests/eval_commands.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/tests/eval_commands.py) via [`tests/eval_harness.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/tests/eval_harness.py) to score across 3 tiers (T1 fast path, T2 single-turn tool/chat, T3 multi-step/observation+synthesis).
- [x] **1.2** Record per-run metrics: pass/fail, route source, latency (p50/p95 ms), schema/JSON compliance, and provider/model tag; save JSON report (`data/evals/<brain>.json`).
- [~] **1.3** Run live baseline eval on local `qwen3:4b` (when Ollama is running) and `gemini` (once `GEMINI_API_KEY` is set). Local done (`data/evals/smart-pre2.json`, `smart-post2.json`); cloud still open, see 5.6.

## Phase 2: Provider-Agnostic Remote Brain (`Gemini` first)
- [x] **2.1** Add `CLOUD_BRAINS` presets in [`app/config.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/config.py) (`gemini` using `GEMINI_API_KEY` + OpenAI-compatible endpoint, plus `groq` / `openrouter` / `claude` slots).
- [x] **2.2** Implement `RemoteBrain` ([`app/brain/remote.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/brain/remote.py)) matching `Brain`'s interface (`decide`, `synthesize`, `warm_up`, `brain_name`) via OpenAI-compatible HTTP `/chat/completions` and Anthropic `/messages` using stdlib `urllib`.
- [x] **2.3** Replace the `_switch_cloud` stub in [`app/brain/router.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/brain/router.py) so `"switch to gemini"` / `"go cloud"` switches live to `RemoteBrain` and `"go local"` restores the local Ollama `Brain`.
- [x] **2.4** Add `Gemini` pill to [`app/ui/hud.html`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/ui/hud.html) brain switcher.
- [x] **2.5** Add mocked unit tests in [`tests/test_remote_brain.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/tests/test_remote_brain.py) and update [`tests/test_router.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/tests/test_router.py) (`61 tests OK`).

## Phase 3: Tier 3 Agent Harness & Confidence Routing
- [x] **3.1** Build bounded multi-step `AgentHarness` ([`app/brain/harness.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/brain/harness.py)): plan → execute allow-listed tools → observe → synthesize.
- [x] **3.2** Add confidence scoring to `Result` (`1.0` fast path, `0.85–0.9` agent/LLM, `0.4` parse error, `0.0` offline) and `deep:` / `research:` / `agent:` Tier 3 trigger in [`app/brain/router.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/brain/router.py).
- [x] **3.3** Emit `harness` progress events and `confidence` in `turn` events on `BUS`.
- [x] **3.4** Add `.env` loader in [`app/config.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/config.py) and clear `HTTPError` / missing-key errors in [`app/brain/remote.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/brain/remote.py).

## Phase 4: JARVIS OS Stages (Skills + Vault Outputs + HUD)
- [x] **4.1** Add `SKILL.md` loader & trace-based "done twice → suggest skill" helper ([`app/skills/loader.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/skills/loader.py)) plus starter skills (`morning-brief`, `project-status`).
- [x] **4.2** Write skill/agent outputs via `save_vault_output`. Default is `data/outputs/` (the vault stays read-only); set `ADEOLA_VAULT_OUT` to opt into writing to the vault.
- [x] **4.3** Add `GET /api/skills` + `POST /api/skills/run` in [`app/server.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/server.py) and `Groq` pill + `SKILLS & ACTIONS` buttons in [`app/ui/hud.html`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/ui/hud.html).

## Phase 5: Review follow-ups (Claude + Gemini review, 2026-11-21)

Done: server LAN/token hardening (`c4746cb`), skill outputs off the vault (`9eb2203`), cloud privacy gating + truthful cloud status (`06a27ca`), eval failure gating + real Tier 3 cases (`599ee45`).

Open, roughly in priority order:
- [x] **5.1** Router split, no behaviour change (done 2026-11-22): brain/voice switching into `app/brain/switching.py`; one shared `_model_turn()` for the three near-duplicate decide → synthesize → remember paths in `handle()`. `tests/test_router.py` is the safety net.
- [x] **5.2** Tier 3 stops after the first tool (done 2026-11-23): added explicit Step 1 multi-tool planning prompt and stricter Step 2+ unanswered-part check in `app/brain/harness.py`. Same-sitting `qwen3:4b` Tier 3 eval went from 3/6 (`smart-5.2-before.json`) to 6/6 (`smart-5.2-after.json`), all finishing in 2 steps with 0 failures.
- [x] **5.3** T2 misses in the local eval (done 2026-11-23): tightened `BASE_PROMPT` (`Conversation vs. tools` + `Memory` / `offer_to_remember`) in `app/brain/model.py`, expanded `_ACTION_HINTS` to stop `_is_spurious_action` false positives on system/security intents, and added `take me to` + `_REMEMBER_TRAILING` fast paths in `app/brain/router.py`. Same-sitting `qwen3:4b` full eval went from 35/41 (`smart-5.3-before.json`) to **41/41 (100%)** (`smart-5.3-after.json`).
- [x] **5.4** (done 2026-11-23: `warning` BUS event + `[warning]` line under `-v`; HUD holds it in the insight strip for 60 s) Silent failures: `_maybe_auto_switch` and `_record_trace` in `router.py` swallow every exception. Emit a BUS event (and log under `-v`) so the HUD can show failed auto-switches.
- [x] **5.5** `AUTO_BRAIN = True` on a 6 GB GPU (done 2026-11-23): added `check_brain_swap()` (`warn_only=True`, warns above 4.0 s or if `coder` isn't pulled) to `tools/preflight.py` + 4 unit tests in `tests/test_preflight.py`. Measured after pulling `qwen2.5-coder:3b`: the two models don't fit on the 6 GB GPU together (only `qwen3:4b`, 3.18 GB, stays resident), so every switch is a full reload. Steady state is smart→coder 5.3 s and coder→smart 5.8 s; the coder model's first-ever load was 52.7 s. A normal model turn is ~0.8 s.
- [x] **5.5b** `AUTO_BRAIN` default (done 2026-11-23): off; `ADEOLA_AUTO_BRAIN=1` turns it on. "use auto brain", the trace summary and the HUD badge now report the real state instead of "auto" whenever nothing is pinned.
- [ ] **5.6** Cloud evals: check the live model IDs in `app/config.py` (`gemini-3.8-flash`, `qwen/qwen3.8-27b`). The old `gemini.json` run made zero successful API calls (30 s timeouts). Re-run `gemini` / `groq` with `--delay`, in the same sitting as a local run.
- [ ] **5.7** Skills: the router doesn't match skill `triggers` from voice/text, the HUD ignores `data.suggested`, and `/api/skills` + `/api/skills/run` have little test coverage.
- [ ] **5.8** HUD ignores `confidence` on `turn` events.
- [ ] **5.9** Cloud privacy, low: `list_projects` output (project names) still goes to the cloud brain. Decide whether it belongs in the blocked set.
- [ ] **5.10** Tailscale access path (`client_access` = `tailscale`) is unit-tested only. Verify from the phone over Tailscale with the printed `?token=` link.
- [ ] **5.11** README refresh: the intro still says "a local text assistant", and the "How it works" table is missing remote brain, harness, skills, events, HUD, cloud sharing flags and the server access rules (`ADEOLA_ALLOW_LAN`, `data/server_token`).
- [ ] **5.12** Low: `app/ui/hud.html` is about 1,900 lines in one file. Split JS/CSS into static files served by `server.py` if it keeps growing.

Eval rule: before/after comparisons are only valid from the same sitting. GPU/Ollama state drifted about 120 ms and flipped one T1 case between two runs of identical code.

---

## Notes
- `tests/test_media.py` (`test_next_and_now_playing`) hung in the sandbox environment during pre-commit verification; all other 166 unit tests (`test_server`, `test_offline`, `test_preflight`, `test_tts`, `test_voice`, `test_wake`, `test_refinements`, `test_resilience`, `test_router`, `test_memory`, `test_notes`) passed.
