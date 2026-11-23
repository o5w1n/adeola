"""In-process live event bus for Adeola's presence orb and telemetry HUD."""

from __future__ import annotations

import queue
import threading
import time
from typing import Any


def _json_safe(value: Any) -> Any:
    """Convert numpy/non-JSON scalars (e.g. float32) into plain Python primitives."""
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except Exception:
            pass
    return value


class EventBus:
    """Thread-safe pub/sub bus broadcasting live pipeline states and audio levels to SSE clients."""

    def __init__(self, min_level_interval: float = 0.05):
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._min_level_interval = min_level_interval
        self._last_level_ts = 0.0
        self._state: dict[str, Any] = {
            "state": "idle",       # idle | wake | listening | thinking | tool | speaking | offline
            "level": 0.0,          # 0.0 .. 1.0 audio amplitude envelope
            "wake_score": 0.0,     # 0.0 .. 1.0 latest wake detector score
            "hear_ms": 0.0,        # latest Whisper STT ms
            "think_ms": 0.0,       # latest Router/Brain ms
            "speak_ms": 0.0,       # latest Kokoro TTS first-chunk ms
            "last_turn": None,     # dict of most recent completed turn
            "updated_at": time.time(),
        }

    def subscribe(self, maxsize: int = 120) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def emit(self, kind: str, **payload: Any) -> None:
        now = time.monotonic()
        with self._lock:
            if kind == "level":
                if (
                    self._min_level_interval > 0
                    and (now - self._last_level_ts) < self._min_level_interval
                    and payload.get("state", self._state["state"]) == self._state["state"]
                ):
                    return
                self._last_level_ts = now

            if "state" in payload and payload["state"]:
                self._state["state"] = str(payload["state"])
            if "level" in payload and payload["level"] is not None:
                self._state["level"] = round(max(0.0, min(1.0, float(payload["level"]))), 3)
            if "wake_score" in payload and payload["wake_score"] is not None:
                self._state["wake_score"] = round(max(0.0, min(1.0, float(payload["wake_score"]))), 3)
            if "hear_ms" in payload and payload["hear_ms"] is not None:
                self._state["hear_ms"] = round(float(payload["hear_ms"]), 1)
            if "think_ms" in payload and payload["think_ms"] is not None:
                self._state["think_ms"] = round(float(payload["think_ms"]), 1)
            if "speak_ms" in payload and payload["speak_ms"] is not None:
                self._state["speak_ms"] = round(float(payload["speak_ms"]), 1)

            if kind == "turn":
                self._state["think_ms"] = round(float(payload.get("ms", 0.0)), 1)
                conf = payload.get("confidence")
                self._state["last_turn"] = {
                    "text": payload.get("text", ""),
                    "reply": payload.get("reply", ""),
                    "source": payload.get("source", "fast"),
                    "ms": round(float(payload.get("ms", 0.0)), 1),
                    "brain": payload.get("brain", "fast"),
                    "calls": payload.get("calls", []),
                    "sources": payload.get("sources", []),
                    "steps": payload.get("steps", 1),
                    "confidence": round(max(0.0, min(1.0, float(conf))), 2) if conf is not None else None,
                    "ts": time.strftime("%H:%M:%S"),
                }

            self._state["updated_at"] = time.time()
            safe_payload = {k: _json_safe(v) for k, v in payload.items()}
            event = {
                **safe_payload,
                "type": kind,
                "state": self._state["state"],
                "level": self._state["level"],
                "wake_score": self._state["wake_score"],
                "hear_ms": self._state["hear_ms"],
                "think_ms": self._state["think_ms"],
                "speak_ms": self._state["speak_ms"],
            }
            subs = list(self._subscribers)

        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass


BUS = EventBus()

