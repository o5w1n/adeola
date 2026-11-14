# Adeola — Gemini Remote Brain, Eval Harness & JARVIS OS Plan

**Baseline Commit:** `8614a9e` (`feat(hud): add live EventBus, SSE telemetry, and WebGL plasma orb / radar dashboard`)

**Goal:** Overcome the 6 GB VRAM ceiling by wiring a provider-agnostic remote brain (**Gemini** first, OpenAI-compatible so Groq/OpenRouter/Claude drop in later), building an **Eval Harness** to benchmark local vs. cloud, adding a **Tier 3 Agent Harness** for multi-step work, and layering in the **JARVIS OS** stages (Skills, Vault outputs, HUD skill buttons).

---

## Phase 1: Eval Harness (Baseline & Scoreboard)
- [x] **1.1** Extend [`tests/eval_commands.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/tests/eval_commands.py) via [`tests/eval_harness.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/tests/eval_harness.py) to score across 3 tiers (T1 fast path, T2 single-turn tool/chat, T3 multi-step/observation+synthesis).
- [x] **1.2** Record per-run metrics: pass/fail, route source, latency (p50/p95 ms), schema/JSON compliance, and provider/model tag; save JSON report (`data/evals/<brain>.json`).
- [ ] **1.3** Run live baseline eval on local `qwen3:4b` (when Ollama is running) and `gemini` (once `GEMINI_API_KEY` is set).

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
- [x] **4.2** Write skill/agent outputs to `qabw/Adeola/outputs/` (with `data/outputs/` fallback) via `save_vault_output`.
- [x] **4.3** Add `GET /api/skills` + `POST /api/skills/run` in [`app/server.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/server.py) and `Groq` pill + `SKILLS & ACTIONS` buttons in [`app/ui/hud.html`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/ui/hud.html).

---

## Notes
- `tests/test_media.py` (`test_next_and_now_playing`) hung in the sandbox environment during pre-commit verification; all other 166 unit tests (`test_server`, `test_offline`, `test_preflight`, `test_tts`, `test_voice`, `test_wake`, `test_refinements`, `test_resilience`, `test_router`, `test_memory`, `test_notes`) passed.
