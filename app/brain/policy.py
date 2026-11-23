"""VRAM-aware automatic brain routing policy for Adeola.

Classifies queries into 'coder', 'smart', or 'quick' and recommends a brain switch
only when it improves capability without thrashing the 4 GB GPU memory ceiling.
"""

from __future__ import annotations

import re
import time

from app import config

_CODER_WORDS = {
    "code", "coding", "python", "rust", "javascript", "typescript", "java",
    "kotlin", "sql", "html", "css", "bash", "powershell", "function", "class",
    "method", "bug", "error", "traceback", "exception", "regex", "git", "commit",
    "refactor", "script", "compiler", "debug", "debugging", "algorithm", "syntax",
}
_SMART_WORDS = {
    "why", "compare", "difference", "explain", "design", "architecture",
    "plan", "summarize", "analyze", "analysis", "tradeoff", "tradeoffs",
    "pros", "cons", "strategy", "evaluate",
}
_SMART_PHRASES = (
    "how does",
    "how do",
    "in detail",
    "step by step",
    "what is the difference",
)


def classify_task(text: str, has_notes: bool = False) -> str:
    """Classify a query into 'coder', 'smart', or 'quick'."""
    lower = text.lower()
    tokens = set(re.findall(r"[a-z0-9_]+", lower))
    if tokens & _CODER_WORDS:
        return "coder"
    if (
        has_notes
        or (tokens & _SMART_WORDS)
        or any(p in lower for p in _SMART_PHRASES)
        or len(tokens) >= 12
    ):
        return "smart"
    return "quick"


class BrainPolicy:
    def __init__(self, pinned: bool = False):
        self.pinned: bool = pinned
        self.last_auto_switch: float = 0.0

    @property
    def auto_active(self) -> bool:
        """Auto routing actually happens only when it's enabled in config and no brain is pinned."""
        return config.AUTO_BRAIN and not self.pinned

    def recommend(
        self,
        text: str,
        current_brain: str,
        has_notes: bool = False,
        now: float | None = None,
    ) -> str | None:
        """Return target brain key ('coder' or 'smart') if an automatic switch is warranted."""
        if not self.auto_active:
            return None
        target = classify_task(text, has_notes=has_notes)
        if target not in config.BRAINS or target == current_brain:
            return None
        # VRAM Thrash Guard: never auto-downgrade a warm 'smart' or 'coder' brain to 'quick'
        if target == "quick":
            return None
        ts = time.monotonic() if now is None else now
        cooldown = 0.0 if config.DRY_RUN else config.AUTO_SWITCH_COOLDOWN_SECONDS
        if self.last_auto_switch > 0 and (ts - self.last_auto_switch) < cooldown:
            return None
        self.last_auto_switch = ts
        return target

