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

HELP_TEXT = ("I can open apps, websites and your projects, search the web, remember things "
             "for you, tell you the time and lock your PC. Try: 'open Brave', 'open my Quail "
             "project', 'remember my exam is on Friday', 'what do you remember' or "
             "'what was I doing earlier'. Say 'no more tips' to stop shortcut tips.")

_POLITE = re.compile(r"^(?:(?:please|can you|could you|would you|hey)\s+)+|\s+(?:please|for me|now)$",
                     re.IGNORECASE)
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

# -- memory and projects --
_CONFIRM_FORGET_ALL = re.compile(r"^yes,? forget (?:everything|it all|all of it)$")
_ADD_PROJECT = re.compile(r"^(?:add|save|remember) (?:my |a |the )?project (.+?) (?:at|in|=) (.+)$",
                          re.IGNORECASE)
_REMEMBER = re.compile(r"^(?:remember|note|make a note|keep in mind|don't forget)(?: that|:)?[\s,]+(.+)$",
                       re.IGNORECASE)
_RECALL = re.compile(r"^(?:what do you (?:remember|know)(?: about)?|what did i (?:tell you|say)(?: about)?|"
                     r"do you remember)(?:\s+(.*))?$")
_WORKING_ON = re.compile(r"^what am i (?:working on|doing)(?: .*)?$")
_FORGET = re.compile(r"^forget (?:about )?(.+)$")
_RECENT = re.compile(r"^what (?:was i|have i been|did i) (?:doing|working on|do|open)(?: .*)?$")
_LIST_PROJECTS = re.compile(r"^(?:(?:list|show)(?: me)? (?:all )?(?:my |the )?projects|"
                            r"what projects (?:do you know|do i have)|my projects)$")
_KEEP = re.compile(r"^(?:yes|yeah|yep|yup|sure|keep(?: it| that)?|ok(?:ay)?|please do|remember it)$")
_SKIP = re.compile(r"^(?:no|nope|nah|skip(?: it| that)?|don't|no thanks|never mind)$")
_TIPS_OFF = re.compile(r"^(?:no more tips|stop (?:the |giving me )?tips|turn (?:off )?tips off|"
                       r"turn off (?:the )?tips|disable tips)$")
_TIPS_ON = re.compile(r"^(?:turn (?:on )?tips on|turn on (?:the )?tips|enable tips|give me tips again)$")
_OPEN_PROJECT = re.compile(r"^(?:open|launch|start|load|work on|let's work on)(?: up)? "
                           r"(?:my |the )?(?:project )?(.+?)(?: project)$|^open project (.+)$")


@dataclass
class Result:
    reply: str
    calls: list[tuple[str, dict]] = field(default_factory=list)
    source: str = "fast"     # "fast", "llm", "chat" or "none"
    ms: float = 0.0


def clean(text: str, keep_case: bool = False) -> str:
    t = " ".join(text.strip().split()).strip(" .!?")
    if not keep_case:
        t = t.lower()
    for name in config.WAKE_NAMES:
        t = re.sub(rf"^(?:hey\s+|ok\s+)?{name}\b[\s,:]*", "", t, flags=re.IGNORECASE)
    prev = None
    while prev != t:   # strip stacked politeness: "can you please ... for me"
        prev, t = t, _POLITE.sub("", t).strip(" ,.!?")
    return t


class Router:
    def __init__(self, toolbox: Toolbox, brain=None):
        self.toolbox = toolbox
        self.brain = brain

    def fast_parse(self, text: str, cased: str = "") -> list[tuple[str, dict]] | str | None:
        """Return tool calls, a direct reply string, or None if unsure.
        `cased` is the same command with its original capitals, for things that get stored."""
        cased = cased or text
        if _TIPS_OFF.match(text):
            return self.toolbox.tips.set_enabled(False)
        if _TIPS_ON.match(text):
            return self.toolbox.tips.set_enabled(True)
        if _CONFIRM_FORGET_ALL.match(text):
            return self.toolbox.confirm_forget_all()
        if m := _ADD_PROJECT.match(cased):
            return self._add_project(m.group(1), m.group(2))
        if m := _REMEMBER.match(cased):
            return [("remember", {"note": m.group(1)})]
        if m := _RECALL.match(text):
            return [("recall", {"query": (m.group(1) or "").removeprefix("my ")})]
        if _WORKING_ON.match(text):
            return [("recall", {"query": "working on"})]
        if m := _FORGET.match(text):
            return [("forget", {"note": m.group(1)})]
        if _RECENT.match(text):
            return [("recent_activity", {})]
        if _LIST_PROJECTS.match(text):
            return [("list_projects", {})]
        if (m := _OPEN_PROJECT.match(text)) and self.toolbox.projects is not None:
            name = m.group(1) or m.group(2)
            if self.toolbox.projects.find(name):
                return [("open_project", {"name": name})]
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

    def _add_project(self, name: str, path: str) -> str:
        if self.toolbox.projects is None:
            return "Projects aren't set up."
        try:
            p = self.toolbox.projects.add(name, path)
        except FileNotFoundError as e:
            return f"{e}. Check the path and try again."
        return f"Added your {p.name} project. It'll open in {p.app.title().replace('Vs ', 'VS ')}."

    def facts_for(self, text: str) -> list[str]:
        mem = self.toolbox.memory
        if mem is None or config.MEMORY_IN_PROMPT <= 0:
            return []
        return [f.text for f in mem.search(text, limit=config.MEMORY_IN_PROMPT)]

    def run_calls(self, calls: list[tuple[str, dict]]) -> str:
        return " ".join(filter(None, (self.toolbox.run(name, args) for name, args in calls)))

    def handle(self, raw: str) -> Result:
        start = time.perf_counter()
        text = clean(raw)
        if not text:
            return Result("Yes?", source="fast")

        # A pending "want me to remember that?" is answered by the very next command only.
        if self.toolbox.pending_offer is not None:
            if _KEEP.match(text) or _SKIP.match(text):
                res = Result(self.toolbox.answer_offer(keep=bool(_KEEP.match(text))), source="fast")
                res.ms = (time.perf_counter() - start) * 1000
                return res
            self.toolbox.pending_offer = None

        parsed = self.fast_parse(text, clean(raw, keep_case=True))
        if isinstance(parsed, str):
            res = Result(parsed, source="fast")
        elif parsed:
            res = Result(self.run_calls(parsed), parsed, "fast")
        elif self.brain is None:
            res = Result("I didn't catch that, and the model is offline.", source="none")
        else:
            try:
                calls, reply = self.brain.decide(clean(raw, keep_case=True), self.facts_for(text))
            except Exception as e:  # Ollama not running, model missing, etc.
                res = Result(f"I couldn't reach the local model ({e.__class__.__name__}). "
                             "Is Ollama running?", source="none")
            else:
                if calls and all(n == "offer_to_remember" for n, _ in calls):
                    # answer the question, then ask about keeping the fact
                    offer = self.run_calls(calls)
                    res = Result(" ".join(filter(None, [reply, offer])) or
                                 "Sorry, I'm not sure how to help with that.", calls, "chat")
                elif calls:
                    real = [c for c in calls if c[0] != "offer_to_remember"]
                    res = Result(self.run_calls(real), real, "llm")
                else:
                    res = Result(reply or "Sorry, I'm not sure how to help with that.",
                                 source="chat")
                self.brain.remember(text, res.reply)

        res.ms = (time.perf_counter() - start) * 1000
        return res
