"""Trace analysis and spoken introspection for Adeola's local TraceStore."""

from __future__ import annotations

from datetime import datetime

_MISS_PREFIXES = (
    "I couldn't find",
    "Sorry, I'm not sure",
    "Sorry, I lost my train of thought",
    "I didn't catch that",
    "Something went wrong",
)
_ACTION_PREFIXES = ("open ", "launch ", "start ", "run ", "switch to ", "go to ")


def _is_miss(trace) -> bool:
    if trace.source in ("error", "none"):
        return True
    if any(trace.reply.startswith(p) for p in _MISS_PREFIXES):
        return True
    if trace.clean_text.startswith(_ACTION_PREFIXES) and not trace.calls:
        return True
    return False


class TraceAnalyzer:
    def __init__(self, memory):
        self.memory = memory

    def summary_report(self, auto_brain: bool = True, current_brain: str = "") -> str:
        if self.memory is None or not hasattr(self.memory, "recent_traces"):
            return "Trace logging is switched off because memory is disabled."
        traces = self.memory.recent_traces(limit=100)
        mode_str = ("auto brain routing" if auto_brain
                    else f"my {current_brain} brain" if current_brain else "a fixed brain")
        if not traces:
            return f"No turns logged yet. I'm currently running with {mode_str}."
        n = len(traces)
        n_fast = sum(1 for t in traces if t.source == "fast")
        fast_pct = int(round((n_fast / n) * 100))
        avg_ms = sum(t.ms for t in traces) / n
        turn_word = "turn" if n == 1 else "turns"
        return (
            f"Over the last {n} {turn_word}, {fast_pct}% hit the instant fast path "
            f"(averaging {avg_ms:.0f} ms) with {mode_str}."
        )

    def friction_report(self) -> str:
        if self.memory is None or not hasattr(self.memory, "recent_traces"):
            return "Trace logging is switched off because memory is disabled."
        # recent_traces returns newest first; reverse to chronological order
        traces = list(reversed(self.memory.recent_traces(limit=100)))
        pairs: list[str] = []
        for i in range(len(traces) - 1):
            curr, nxt = traces[i], traces[i + 1]
            if _is_miss(curr) and not _is_miss(nxt):
                try:
                    dt = (
                        datetime.fromisoformat(nxt.ts) - datetime.fromisoformat(curr.ts)
                    ).total_seconds()
                except Exception:
                    dt = 0.0
                if 0.0 <= dt <= 60.0:
                    pair = f"'{curr.clean_text}' -> '{nxt.clean_text}'"
                    if pair not in pairs:
                        pairs.append(pair)
        if not pairs:
            return "No recent friction or misheard commands found."
        shown = ", ".join(pairs[-3:])
        return f"Recent corrections I noticed: {shown}."
