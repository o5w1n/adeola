# Adeola Visual Interface (`/hud`) — Implementation Plan & Todo

> **Decisions & Propositions:** [`sys.md`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/sys.md)

**Goal:** Build Adeola's browser visual interface at `/hud` combining:
1. **Pure Presence Mode (Default):** WebGL translucent plasma orb on pure black (`#000000`) reacting in real time to Adeola's pipeline state (`idle`, `wake`, `listening`, `thinking`, `tool`, `speaking`, `offline`) and audio amplitude, with zero screen clutter, type-anywhere input pill, and `Tab` / hover toggle.
2. **Detailed Radar Dashboard Mode (Toggle):** One continuous screen where the orb shrinks into the central 6-axis pipeline radar (`Wake · Hear · Think · Tools · Speak · Memory`) while dark sci-fi telemetry panels (`Fast-Path Efficiency`, `Pipeline Signals`, `Latency Trends`, `Route Distribution`, `Brain & Policy`, `TraceAnalyzer Insight Strip`, and `Overview / Conversation / Memory & Notes / Traces` views) slide in around it.

---

### Task 1: In-Process Live Event Bus & Pipeline Hooks (`app/events.py`, `app/audio/`, `app/brain/router.py`)
- [x] **Step 1:** Write unit tests in `tests/test_server.py` for `EventBus` state/level publishing, subscriber queues, and pipeline state emission.
- [x] **Step 2:** Create `app/events.py` with thread-safe `EventBus` (`emit`, `snapshot`, `subscribe`, `unsubscribe`) and default `BUS` singleton.
- [x] **Step 3:** Wire lightweight `BUS.emit` calls into `WakeWord` (`wake`, `listening`, `thinking`, `idle`, `wake_score`, mic `level`), `PushToTalk` (`listening`, `thinking`, `hear_ms`), `Router.handle` (`thinking`, `tool`, `turn`), and `Speaker` (`speaking`, `speak_ms`, voice `level`, `idle`).

### Task 2: HTTP Telemetry, SSE Stream, and `/hud` Endpoint (`app/server.py`)
- [x] **Step 1:** Write unit tests in `tests/test_server.py` for `GET /hud`, `GET /api/telemetry`, and `GET /api/events` (SSE format & auth).
- [x] **Step 2:** Implement `/api/telemetry` (aggregated trace metrics, sparkline, route mix, radar axes, `TraceAnalyzer` insight & friction, recent traces, facts, and notes), `/api/events` streaming SSE handler, and `/hud` route in `app/server.py`.

### Task 3: WebGL Plasma Orb & Slide-In Radar Dashboard UI (`app/ui/hud.html`)
- [x] **Step 1:** Build `app/ui/hud.html` with:
  - Custom WebGL fragment shader for the 3D plasma sphere matching `docs/interface/refs/presence-reference.mp4` across all states (`idle`, `wake`, `listening`, `thinking`, `tool`, `speaking`, `offline`).
  - Smooth shrink-and-slide transition (`Tab` key or corner button) into the 6-axis orbital radar dashboard matching `docs/interface/refs/dashboard-reference.jpg`.
  - Hand-drawn Canvas sparkline, multi-series trend chart, and route-distribution donut chart (zero external dependencies, 100% offline).
  - Interactive tabs (`Overview`, `Conversation`, `Memory & Vault`, `Traces`), brain switcher pills, state simulator bar, and type-anywhere command input.

### Task 4: Verification
- [x] **Step 1:** Run unit tests (`130 tests OK`, including all 14 server/HUD/SSE tests).

---

## Review

- **Live Event Bus ([`app/events.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/events.py)):** Thread-safe `EventBus` and `BUS` singleton wired into [`WakeWord`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/audio/wake.py), [`PushToTalk`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/audio/push_to_talk.py), [`Speaker`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/audio/tts.py), and [`Router.handle`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/brain/router.py).
- **Server Endpoints ([`app/server.py`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/server.py)):** Added `GET /hud` (and `/orb`), `GET /api/telemetry`, and live Server-Sent Events at `GET /api/events` while keeping `/` intact for the Android companion.
- **Visual HUD ([`app/ui/hud.html`](file:///c:/Users/O%20M%20E%20N/Downloads/adeola/app/ui/hud.html)):** Pure-black WebGL plasma sphere presence with shrink-and-slide transition into the 6-axis Elius-style radar dashboard.
