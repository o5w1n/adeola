"""Two-tier command routing.

1. Fast path: regex patterns for common commands. Instant, no model involved.
2. Model path: anything the patterns can't handle goes to the local LLM,
   which may only choose from the approved tools in the Toolbox.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from app import config
from app.tools.registry import Toolbox

HELP_TEXT = ("I can open apps and websites, search the web, tell you the time "
             "and lock your PC. Try: 'open Brave', 'open notepad and calculator', "
             "'search for Kotlin coroutines' or 'lock my PC'.")

_POLITE = re.compile(r"^(?:(?:please|can you|could you|would you|hey)\s+)+|\s+(?:please|for me|now)$")
_OPEN = re.compile(r"^(?:open|launch|start|run|fire up|bring up|pull up)\s+(.+)$")
_SEARCH = re.compile(
    r"^(?:search|google|look up)(?:\s+(?:the web|online|the internet))?(?:\s+for)?\s+(.+?)"
    r"(?:\s+on\s+(?:google|the web|the internet|brave))?$")
_URL = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?$")
_TIME = re.compile(r"^(?:what(?:'s| is) (?:the (?:time|date)(?: today)?|today's date)"
                   r"|what time is it|what day is (?:it|today)|time|date)$")
_LOCK = re.compile(r"^lock(?:\s+(?:my|the))?\s+(?:pc|computer|screen|laptop)$")
_HELP = re.compile(r"^(?:help|what can you do)$")
_GREETING = re.compile(r"^(?:hi|hello|hey|yo|sup|what'?s up|whats up|how are you(?: doing)?|"
                       r"how'?s it going|good (?:morning|afternoon|evening))$")
GREETING_TEXT = "Hey! What can I do for you?"
_SPLIT = re.compile(r"\s*(?:,|\band\b|\bthen\b)\s*")


@dataclass
class Result:
    reply: str
    calls: list[tuple[str, dict]] = field(default_factory=list)
    source: str = "fast"     # "fast", "llm", "chat" or "none"
    ms: float = 0.0


def clean(text: str) -> str:
    t = " ".join(text.lower().strip().split()).strip(" .!?")
    for name in config.WAKE_NAMES:
        t = re.sub(rf"^(?:hey\s+|ok\s+)?{name}\b[\s,:]*", "", t)
    prev = None
    while prev != t:   # strip stacked politeness: "can you please ... for me"
        prev, t = t, _POLITE.sub("", t).strip(" ,.!?")
    return t


class Router:
    def __init__(self, toolbox: Toolbox, brain=None):
        self.toolbox = toolbox
        self.brain = brain

    def fast_parse(self, text: str) -> list[tuple[str, dict]] | str | None:
        """Return tool calls, a direct reply string, or None if unsure."""
        if _HELP.match(text):
            return HELP_TEXT
        if _GREETING.match(text):
            return GREETING_TEXT
        if _TIME.match(text):
            return [("get_time", {})]
        if _LOCK.match(text):
            return [("lock_pc", {})]
        if m := _SEARCH.match(text):
            return [("web_search", {"query": m.group(1)})]
        if m := _OPEN.match(text):
            calls = []
            for target in filter(None, _SPLIT.split(m.group(1))):
                if _URL.match(target):
                    calls.append(("open_url", {"url": target}))
                elif len(target.split()) <= 4 and self.toolbox.apps.can_open(target):
                    calls.append(("open_app", {"name": target}))
                else:
                    return None  # vague target ("the thing I use for music"): ask the model
            return calls or None
        return None

    def run_calls(self, calls: list[tuple[str, dict]]) -> str:
        return " ".join(self.toolbox.run(name, args) for name, args in calls)

    def handle(self, raw: str) -> Result:
        start = time.perf_counter()
        text = clean(raw)
        if not text:
            return Result("Yes?", source="fast")

        parsed = self.fast_parse(text)
        if isinstance(parsed, str):
            res = Result(parsed, source="fast")
        elif parsed:
            res = Result(self.run_calls(parsed), parsed, "fast")
        elif self.brain is None:
            res = Result("I didn't catch that, and the model is offline.", source="none")
        else:
            try:
                calls, reply = self.brain.decide(text)
            except Exception as e:  # Ollama not running, model missing, etc.
                res = Result(f"I couldn't reach the local model ({e.__class__.__name__}). "
                             "Is Ollama running?", source="none")
            else:
                if calls:
                    res = Result(self.run_calls(calls), calls, "llm")
                else:
                    res = Result(reply or "Sorry, I'm not sure how to help with that.",
                                 source="chat")
                self.brain.remember(text, res.reply)

        res.ms = (time.perf_counter() - start) * 1000
        return res
