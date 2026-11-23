"""Two-tier command routing.

1. Fast path: regex patterns for common commands. Instant, no model involved.
2. Model path: anything the patterns can't handle goes to the local LLM,
   which may only choose from the approved tools in the Toolbox.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from app import config
from app.brain.harness import AgentHarness
from app.brain.policy import BrainPolicy
from app.brain.remote import RemoteBrain, cloud_blocked_tools
from app.brain.switching import BrainSwitching
from app.brain.traces import TraceAnalyzer
from app.events import BUS
from app.memory.notes import NotesIndex, is_recency_query
from app.tools.registry import OBSERVATION_TOOLS, Toolbox

HELP_TEXT = ("I can open apps, websites and your projects, control music and volume, search the web, remember things "
             "for you, answer from your notes, switch brains and voices, tell you the time and lock your PC. Try: "
             "'open Brave', 'open my Quail project', 'remember my exam is on Friday', 'what do you remember', "
             "'what do my notes say about …', 'switch to your quick brain', 'use your calm voice' or "
             "'what was I doing earlier'. Say 'no more tips' to stop shortcut tips.")

_POLITE = re.compile(r"^(?:(?:please|can you|could you|would you|hey)\s+)+|\s+(?:please|for me|now)$",
                     re.IGNORECASE)
_OPEN = re.compile(r"^(?:open|launch|start|run|fire up|bring up|pull up|switch to|go to)\s+(.+)$")
_OPEN_INVERTED = re.compile(
    r"^([\w]+)\s+(?:open|launch|switch to|go to)\s+(?:your\s+|my\s+|the\s+)?(.+)$"
)
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
GREETING_REPLIES = (
    GREETING_TEXT,
    "Hi! Ready when you are.",
    "Hey there — what are we working on?",
)
_UNSURE = "Sorry, I'm not sure how to help with that."
_THANKS_ACK = re.compile(
    r"^(?:thanks|thank you|thx|ty|appreciate it|much appreciated|"
    r"nice|nice one|cool|sweet|awesome|great|good job|well done|"
    r"bet|aight|aigh|alright|got it|makes sense|fair enough|dope|solid)"
    r"(?:[\s,]+(?:adeola|bro|man|fam|go off|go iff))?$"
)
ACK_REPLIES = (
    "Anytime — what's next?",
    "You got it.",
    "Always ready. What are we doing next?",
)
_ACTION_HINTS = {
    "open", "launch", "start", "run", "fire", "bring", "pull", "switch", "go",
    "play", "pause", "resume", "stop", "skip", "next", "previous", "volume",
    "mute", "unmute", "louder", "quieter", "search", "google", "look", "find",
    "time", "date", "day", "lock", "remember", "note", "save", "log", "forget",
    "recall", "project", "projects", "work", "write", "code", "coding", "browse",
    "browser", "internet", "web", "music", "song", "listen", "watch", "video",
    "terminal", "command", "files", "folder", "notes", "calculator", "calc",
    "settings", "notepad", "brave", "spotify", "chrome", "studio",
}
_DESKTOP_ACTION_TOOLS = {"open_app", "open_url", "open_project", "lock_pc", "web_search"}
_HONESTY_LOCAL = re.compile(
    r"^(?:do you (?:cost anything|run locally|run offline|use the cloud|need (?:the )?internet|send data)"
    r"|are you (?:local|offline|free|private)|where do you run|how much do you cost)$"
)
_HONESTY_INTERNET = re.compile(
    r"^(?:what (?:needs|uses|requires) (?:the )?internet|what can(?:'t| not) you do offline)$"
)
_SPLIT = re.compile(r"\s*(?:,|\band\b|\bthen\b)\s*")

# -- memory and projects --
_CONFIRM_FORGET_ALL = re.compile(r"^yes,? forget (?:everything|it all|all of it)$")
_ADD_PROJECT = re.compile(r"^(?:add|save|remember) (?:my |a |the )?project (.+?) (?:at|in|=) (.+)$",
                          re.IGNORECASE)
_REMEMBER = re.compile(
    r"^(?:remember|note to self|make a note|save a note|note|log this|log|jot down|"
    r"keep in mind|don't (?:let me )?forget)(?:(?:\s+that)?\s*:|\s+that)?[\s,]+(.+)$",
    re.IGNORECASE,
)
_RECALL = re.compile(r"^(?:what do you (?:remember|know)(?: about)?|what did i (?:tell you|say)(?: about)?|"
                     r"do you remember)(?:\s+(.*))?$")
_WORKING_ON = re.compile(r"^what am i (?:working on|doing)(?: .*)?$")
_FORGET = re.compile(r"^forget (?:about )?(.+)$")
_RECENT = re.compile(r"^what (?:was i|have i been|did i) (?:doing|working on|do|open)(?: .*)?$")
_LIST_PROJECTS = re.compile(r"^(?:(?:list|show)(?: me)? (?:all )?(?:my |the )?projects|"
                            r"what projects (?:do you know|do i have)|my projects)$")
_INTERRUPT = re.compile(
    r"^(?:wait|hold on|hang on|quiet|be quiet|shut up|enough|cancel|never mind|nevermind|stop talking|stop speaking)$"
)
_MEDIA = re.compile(
    r"^(?:pause|stop|resume|unpause|play|continue)(?: (?:the |my |this )?"
    r"(?:music|song|track|video|spotify|youtube|it|playing|playback))?$"
    r"|^(?:next|skip|previous|last)(?: (?:song|track|one|video))?$|^skip (?:this|it)(?: song| track)?$"
    r"|^(?:go )?back (?:a |one )?(?:song|track)$|^play (?:the )?(?:next|previous|last) (?:song|track|one)$"
    r"|^what(?:'s| is) (?:playing|this song)(?: right now| now)?$|^what song is (?:this|playing)$"
    r"|^who (?:sings|is singing) this(?: song)?$"
    r"|^(?:volume|turn it|turn the volume|turn the music|turn the sound) (?:up|down)$"
    r"|^(?:louder|quieter|softer)$|^(?:mute|unmute)(?: (?:the )?(?:sound|volume|music|it|pc))?$"
    r"|^(?:set )?(?:the )?volume (?:to )?\d{1,3}(?: ?%| percent)?$")
_KEEP = re.compile(r"^(?:yes|yeah|yep|yup|sure|keep(?: it| that)?|ok(?:ay)?|please do|remember it)$")
_SKIP = re.compile(r"^(?:no|nope|nah|skip(?: it| that)?|don't|no thanks|never mind)$")
_TIPS_OFF = re.compile(r"^(?:no more tips|stop (?:the |giving me )?tips|turn (?:off )?tips off|"
                       r"turn off (?:the )?tips|disable tips)$")
_TIPS_ON = re.compile(r"^(?:turn (?:on )?tips on|turn on (?:the )?tips|enable tips|give me tips again)$")
_GO_LOCAL = re.compile(
    r"^(?:go local|switch to (?:local|lookout)|"
    r"(?:switch|change|go) (?:back )?to (?:your |my |the )?(?:local|lookout|offline)"
    r"(?:[\s,]+(?:brain|model|mode|brave))?|"
    r"use (?:your |my |the )?(?:local|lookout|offline)(?:[\s,]+(?:brain|model|mode|brave))?)$")
_GO_CLOUD = re.compile(
    r"^(?:go cloud|switch to (?:your |the )?(cloud|claude|gemini|groq|openrouter)(?:[\s,]+(?:brain|model))?"
    r"|(?:switch|change) to (?:your |the )?(cloud|claude|gemini|groq|openrouter) (?:brain|model)"
    r"|use (?:your |the )?(cloud|claude|gemini|groq|openrouter)(?:[\s,]+(?:brain|model))?)$")
_WHICH_BRAIN = re.compile(
    r"^(?:(?:which|what) (?:brain|model|llm) (?:are you (?:on|using|running)|is (?:this|active|loaded))"
    r"|what(?:'s| is) your (?:current )?(?:brain|model)|list (?:your |the )?(?:brains|models))$")
_SWITCH_BRAIN = re.compile(
    r"^(?:(?:switch|change) (?:to )?(?:your |the |my )?([\w.:-]+) (?:brain|model)"
    r"|use (?:your |the |my )?([\w.:-]+) (?:brain|model)"
    r"|(?:switch|change) (?:your |the |my )?(?:brain|model) to ([\w.:-]+)"
    r"|(?:switch|change) (?:to )?(?:your |the |my )?(quick|smart|coder|fast|coding|local|lookout|offline)"
    r"|use (?:your |the |my )?(quick|smart|coder|fast|coding))$")
_AUTO_BRAIN = re.compile(
    r"^(?:(?:use|switch to|turn on|enable) (?:your |the )?)?auto(?:matic)?(?:\s+(?:brain|model|routing|mode))?$"
)
_TRACE_SUMMARY = re.compile(
    r"^(?:how have you been running|show (?:my |your |the )?traces|trace summary|performance stats|system stats)$"
)
_TRACE_FRICTION = re.compile(
    r"^(?:what (?:have you|did you) (?:miss|missed|mishear|misheard)|show friction|friction report)$"
)
_WHICH_VOICE = re.compile(
    r"^(?:(?:which|what) voice (?:are you (?:on|using)|is (?:this|active))"
    r"|what(?:'s| is) your (?:current )?voice|list (?:your |the )?voices)$")
_SWITCH_VOICE = re.compile(
    r"^(?:(?:switch|change) (?:to )?(?:your |the |my )?([\w-]+) voice"
    r"|use (?:your |the |my )?([\w-]+) voice"
    r"|(?:switch|change) (?:your |the |my )?voice to ([\w-]+))$")
_OPEN_PROJECT = re.compile(r"^(?:open|launch|start|load|work on|let's work on)(?: up)? "
                           r"(?:my |the )?(?:project )?(.+?)(?: project)$|^open project (.+)$")
_NOTES_Q = re.compile(
    r"^(?:what do(?:es)? my (?:notes|vault|brain vault) say about|"
    r"(?:check|search|look (?:in|through)) my (?:notes|vault|brain vault)(?: for| about)?|"
    r"(?:in|according to) my (?:notes|vault|brain vault),?) (?:the )?(.+)$"
)
_RECENT_NOTES_Q = re.compile(
    r"^(?:what (?:was|is|were|are) (?:the )?(?:last|latest|newest|most recent) notes?(?: (?:that (?:was|were) )?(?:added|saved|created|updated|written))?(?: (?:to|in) (?:my )?(?:brain |obsidian )?(?:vault|notes))?"
    r"|(?:show|list|what are) (?:my )?(?:latest|newest|most recent|recent) notes?(?: in (?:my )?(?:brain |obsidian )?vault)?)$"
)
_TIER3_AGENT = re.compile(r"^(?:deep|research|agent)\s*[:,-]\s*(.+)$", re.IGNORECASE)


@dataclass
class Result:
    reply: str
    calls: list[tuple[str, dict]] = field(default_factory=list)
    source: str = "fast"     # "fast", "llm", "agent", "chat", "notes", "error" or "none"
    ms: float = 0.0
    sources: list[str] = field(default_factory=list)
    steps: int = 1
    brain: str = "fast"
    observations: list[tuple[str, dict, str]] = field(default_factory=list)
    confidence: float = 1.0


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


class Router(BrainSwitching):
    def __init__(self, toolbox: Toolbox, brain=None, notes: NotesIndex | None = None, speaker=None):
        self.toolbox = toolbox
        self.brain = brain
        self._local_brain = brain
        self._remote_brains: dict[str, RemoteBrain] = {}
        self.notes = notes
        self.speaker = speaker
        self.policy = BrainPolicy()
        self.harness = AgentHarness(toolbox)
        self._greeting_idx = 0
        self._ack_idx = 0

    def fast_parse(self, text: str, cased: str = "") -> list[tuple[str, dict]] | str | None:
        """Return tool calls, a direct reply string, or None if unsure.
        `cased` is the same command with its original capitals, for things that get stored."""
        cased = cased or text
        if _INTERRUPT.match(text):
            return "Okay."
        if _MEDIA.match(text):
            return [("media", {"command": text})]
        if _TIPS_OFF.match(text):
            return self.toolbox.tips.set_enabled(False)
        if _TIPS_ON.match(text):
            return self.toolbox.tips.set_enabled(True)
        if _GO_LOCAL.match(text):
            return self._go_local()
        if m := _GO_CLOUD.match(text):
            target = next((g for g in m.groups() if g), config.DEFAULT_CLOUD_BRAIN)
            return self._switch_cloud(target)
        if _AUTO_BRAIN.match(text):
            return self._enable_auto_brain()
        if _TRACE_SUMMARY.match(text):
            return self._trace_summary()
        if _TRACE_FRICTION.match(text):
            return self._trace_friction()
        if _WHICH_BRAIN.match(text):
            return self._which_brain()
        if m := _SWITCH_BRAIN.match(text):
            return self._switch_brain(next(g for g in m.groups() if g))
        if _WHICH_VOICE.match(text):
            return self._which_voice()
        if m := _SWITCH_VOICE.match(text):
            return self._switch_voice(m.group(1) or m.group(2) or m.group(3))
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
        if _HONESTY_LOCAL.match(text):
            return self._honesty_local()
        if _HONESTY_INTERNET.match(text):
            return self._honesty_internet()
        if _GREETING.match(text):
            reply = GREETING_REPLIES[self._greeting_idx % len(GREETING_REPLIES)]
            self._greeting_idx += 1
            return reply
        if _THANKS_ACK.match(text):
            return self._next_ack()
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
        if len(text.split()) <= 6 and (m := _OPEN_INVERTED.match(text)):
            tail = m.group(2).strip()
            combined = f"{m.group(1)} {tail}".strip()
            if len(tail.split()) <= 4 and self.toolbox.apps.can_open(tail):
                return [("open_app", {"name": tail})]
            if len(combined.split()) <= 4 and self.toolbox.apps.can_open(combined):
                return [("open_app", {"name": combined})]
        return None

    def _add_project(self, name: str, path: str) -> str:
        if self.toolbox.projects is None:
            return "Projects aren't set up."
        try:
            p = self.toolbox.projects.add(name, path)
        except FileNotFoundError as e:
            return f"{e}. Check the path and try again."
        return f"Added your {p.name} project. It'll open in {p.app.title().replace('Vs ', 'VS ')}."

    def _trace_summary(self) -> str:
        name = ""
        if self.brain is not None:
            name = (self.brain.brain_name() if hasattr(self.brain, "brain_name")
                    else getattr(self.brain, "model", config.MODEL))
        return TraceAnalyzer(self.toolbox.memory).summary_report(
            auto_brain=not self.policy.pinned, current_brain=name
        )

    def _trace_friction(self) -> str:
        return TraceAnalyzer(self.toolbox.memory).friction_report()

    def _brain_error_reply(self, exc: Exception) -> str:
        if self._is_cloud_brain():
            return (
                f"I couldn't reach the {self.brain.brain_name()} cloud brain ({exc.__class__.__name__}). "
                "Check your internet or API key, or say 'go local'."
            )
        return f"I couldn't reach the local model ({exc.__class__.__name__}). Is Ollama running?"

    def _prompt_facts(self, text: str) -> list[str]:
        if self._is_cloud_brain() and not config.CLOUD_SHARES_MEMORY:
            return []
        return self.facts_for(text)

    def _prompt_notes(self, text: str, min_score: int = config.NOTES_MIN_SCORE) -> list[tuple[str, str]]:
        if self._is_cloud_brain() and not config.CLOUD_SHARES_NOTES:
            return []
        return self.notes_for(text, min_score=min_score)

    def facts_for(self, text: str) -> list[str]:
        mem = self.toolbox.memory
        if mem is None or config.MEMORY_IN_PROMPT <= 0:
            return []
        return [f.text for f in mem.search(text, limit=config.MEMORY_IN_PROMPT)]

    def notes_for(self, text: str, min_score: int = config.NOTES_MIN_SCORE) -> list[tuple[str, str]]:
        if self.notes is None or config.NOTES_IN_PROMPT <= 0:
            return []
        if is_recency_query(text) and hasattr(self.notes, "recent"):
            matches = self.notes.recent(limit=config.NOTES_IN_PROMPT)
            out = []
            for i, n in enumerate(matches):
                dt = (datetime.fromtimestamp(n.mtime).strftime("%Y-%m-%d %H:%M")
                      if getattr(n, "mtime", 0) else "recent")
                out.append((n.title, f"(Most recent #{i + 1}, updated {dt}) {self.notes.excerpt(n, text)}"))
            return out
        matches = self.notes.search(text, limit=config.NOTES_IN_PROMPT, min_score=min_score)
        return [(n.title, self.notes.excerpt(n, text)) for n in matches]

    def run_calls(self, calls: list[tuple[str, dict]]) -> str:
        outputs = []
        for name, args in calls:
            BUS.emit("state", state="tool", tool=name, level=0.45)
            outputs.append(self.toolbox.run(name, args))
        return " ".join(filter(None, outputs))

    def _is_spurious_action(self, text: str, calls: list[tuple[str, dict]]) -> bool:
        if not any(name in _DESKTOP_ACTION_TOOLS for name, _ in calls):
            return False
        lower_text = text.lower()
        words = set(re.findall(r"[a-z0-9]+", lower_text))
        if words & _ACTION_HINTS:
            return False
        for _, args in calls:
            for val in (args or {}).values():
                for tok in re.findall(r"[a-z0-9]+", str(val).lower()):
                    if len(tok) >= 3 and tok in lower_text:
                        return False
        return True

    def _blocked_tools(self) -> set[str]:
        """Tools whose output must not reach the active brain (non-empty only on a cloud brain)."""
        return cloud_blocked_tools() if self._is_cloud_brain() else set()

    def _next_ack(self) -> str:
        reply = ACK_REPLIES[self._ack_idx % len(ACK_REPLIES)]
        self._ack_idx += 1
        return reply

    def handle(self, raw: str) -> Result:
        start = time.perf_counter()
        text = clean(raw)
        if not text:
            return Result("Yes?", source="fast")
        BUS.emit("state", state="thinking", text=text, level=0.35)
        try:
            res = self._special_route(raw, text)
        except Exception as e:
            res = Result(f"Something went wrong ({e.__class__.__name__}). I'm still here.",
                         source="error")
        if res is None:
            res = self._model_turn(raw, text)
        self._finish(raw, text, res, start)
        return res

    def _special_route(self, raw: str, text: str) -> Result | None:
        """Pending offers, notes questions, agent goals and the fast path. None means ask the model."""
        # A pending "want me to remember that?" is answered by the very next command only.
        if self.toolbox.pending_offer is not None:
            if _KEEP.match(text) or _SKIP.match(text):
                return Result(self.toolbox.answer_offer(keep=bool(_KEEP.match(text))), source="fast")
            self.toolbox.pending_offer = None
        if _RECENT_NOTES_Q.match(text):
            return self._notes_turn(raw, text, self.notes_for(text, min_score=1), recent=True)
        if m := _NOTES_Q.match(text):
            topic = m.group(1)
            return self._notes_turn(raw, text, self.notes_for(topic, min_score=1), topic=topic)
        if (m := _TIER3_AGENT.match(text)) and self.brain is not None:
            return self._agent_turn(text, m.group(1).strip())
        parsed = self.fast_parse(text, clean(raw, keep_case=True))
        if isinstance(parsed, str):
            return Result(parsed, source="fast")
        if parsed:
            return Result(self.run_calls(parsed), parsed, "fast")
        return None

    def _notes_turn(self, raw: str, text: str, matched: list[tuple[str, str]],
                    recent: bool = False, topic: str = "") -> Result:
        """Answer "what do my notes say about X" (topic) or "what's my latest note" (recent)."""
        if not matched:
            return Result("You don't have any notes in your vault yet." if recent
                          else f"Your notes don't mention {topic}.", source="notes")
        sources = [t for t, _ in matched]
        titles = ", ".join(sources)
        listing = (f"Your most recent note is {matched[0][0]} (recent: {titles})." if recent
                   else f"Your notes on that: {titles}.")
        if self.brain is None:
            return Result(listing, source="notes", sources=sources)
        if self._is_cloud_brain() and not config.CLOUD_SHARES_NOTES:
            why = ("Cloud note sharing is off, so I answered locally" if recent
                   else "Cloud note sharing is off, so I didn't send them to the cloud")
            return Result(f"{listing} {why} — say 'go local' to summarize them.",
                          source="notes", sources=sources, brain="local")
        cased = clean(raw, keep_case=True)
        try:
            calls, reply = self.brain.decide(cased, self._prompt_facts(text), notes=matched)
        except Exception as e:
            return Result(self._brain_error_reply(e), source="none")
        if not recent:
            reply_text = reply or _UNSURE
            self.brain.remember(text, reply_text)
            return Result(reply_text, calls, source="notes", sources=sources)
        hit_private = any(n in self._blocked_tools() for n, _ in calls)
        if not reply and calls and hasattr(self.brain, "synthesize"):
            obs = [(n, a, self.toolbox.run(n, a)) for n, a in calls]
            if hit_private:
                reply = " ".join(filter(None, (out for _, _, out in obs)))
            else:
                try:
                    reply = self.brain.synthesize(cased, obs).strip()
                except Exception:
                    reply = ""
        reply_text = reply or f"The most recent note in your vault is {matched[0][0]}."
        if not hit_private:
            self.brain.remember(text, reply_text)
        return Result(reply_text, calls, source="notes", sources=sources)

    def _agent_turn(self, text: str, goal: str) -> Result:
        """Tier 3: a bounded multi-step tool loop for `agent:` / `research:` / `deep:` goals."""
        matched = self._prompt_notes(goal)
        try:
            h_res = self.harness.run(self.brain, goal, facts=self._prompt_facts(goal), notes=matched)
        except Exception as e:
            return Result(self._brain_error_reply(e), source="none", confidence=0.0)
        if not any(n in self._blocked_tools() for n, _, _ in h_res.observations):
            self.brain.remember(text, h_res.reply)
        return Result(h_res.reply, h_res.calls, source="agent", sources=[t for t, _ in matched],
                      steps=h_res.steps, observations=h_res.observations, confidence=h_res.confidence)

    def _model_turn(self, raw: str, text: str) -> Result:
        """Anything the fast path couldn't handle: the model picks tools or answers in chat."""
        if self.brain is None:
            return Result("I didn't catch that, and the model is offline.", source="none")
        matched = self._prompt_notes(text)
        self._maybe_auto_switch(text, has_notes=bool(matched))
        cased = clean(raw, keep_case=True)
        try:
            calls, reply = self.brain.decide(cased, self._prompt_facts(text), notes=matched)
        except Exception as e:  # Ollama not running, model missing, cloud unreachable, etc.
            return Result(self._brain_error_reply(e), source="none")
        note_sources = [t for t, _ in matched] if reply else []
        res = self._act_on_decision(text, cased, calls, reply, note_sources)
        # A turn that ran a private tool on a cloud brain stays out of that brain's history.
        if not any(name in self._blocked_tools() for name, _ in res.calls):
            self.brain.remember(text, res.reply)
        return res

    def _act_on_decision(self, text: str, cased: str, calls: list[tuple[str, dict]], reply: str,
                         note_sources: list[str]) -> Result:
        if not calls:
            return Result(reply or _UNSURE, source="chat", sources=note_sources)
        if all(n == "offer_to_remember" for n, _ in calls):
            # answer the question, then ask about keeping the fact
            offer = self.run_calls(calls)
            return Result(" ".join(filter(None, [reply, offer])) or _UNSURE, calls, "chat",
                          sources=note_sources)
        real = [c for c in calls if c[0] != "offer_to_remember"]
        if self._is_spurious_action(text, real):
            return Result(reply or self._next_ack(), [], source="chat", sources=note_sources)
        if any(name in OBSERVATION_TOOLS for name, _ in real) and hasattr(self.brain, "synthesize"):
            return self._observe_and_synthesize(cased, real, note_sources)
        return Result(self.run_calls(real), real, "llm")

    def _observe_and_synthesize(self, cased: str, real: list[tuple[str, dict]],
                                note_sources: list[str]) -> Result:
        """Run read-only tools, then let the brain phrase the answer from their output."""
        observations = [(name, args, self.toolbox.run(name, args)) for name, args in real]
        raw_output = Result(" ".join(filter(None, (out for _, _, out in observations))), real,
                            source="llm", steps=1, observations=observations)
        if any(name in self._blocked_tools() for name, _ in real):
            return raw_output
        try:
            synth = self.brain.synthesize(cased, observations).strip()
        except Exception:
            synth = ""
        if not synth:
            return raw_output
        return Result(synth, real, source="agent", sources=note_sources, steps=2,
                      observations=observations)

    def _finish(self, raw: str, text: str, res: Result, start: float) -> None:
        """Stamp timing and confidence, record the trace and tell the HUD the turn is over."""
        res.ms = (time.perf_counter() - start) * 1000
        if res.source == "none":
            res.confidence = 0.0
        elif res.source != "fast" and self.brain is not None:
            if res.brain == "fast":
                res.brain = getattr(self.brain, "model", config.MODEL)
            if getattr(self.brain, "last_stats", {}).get("parse_error"):
                res.confidence = min(res.confidence, 0.4)
            elif res.source != "agent":
                res.confidence = min(res.confidence, 0.9)
        if not (_TRACE_SUMMARY.match(text) or _TRACE_FRICTION.match(text)):
            self._record_trace(raw, text, res)
        next_state = "speaking" if (self.speaker and getattr(self.speaker, "speaking", False)) else "idle"
        BUS.emit(
            "turn",
            state=next_state,
            text=text,
            reply=res.reply,
            source=res.source,
            ms=round(res.ms, 1),
            brain=res.brain,
            is_cloud=self._is_cloud_brain(),
            calls=[{"tool": n, "args": a} for n, a in res.calls],
            sources=res.sources,
            steps=res.steps,
            confidence=res.confidence,
        )

    def _record_trace(self, raw: str, clean_text: str, res: Result) -> None:
        mem = self.toolbox.memory
        if mem is None or config.DRY_RUN and not hasattr(mem, "add_trace"):
            return
        if not hasattr(mem, "add_trace"):
            return
        stats = getattr(self.brain, "last_stats", {}) if self.brain is not None else {}
        try:
            mem.add_trace(
                raw_text=raw,
                clean_text=clean_text,
                source=res.source,
                brain=res.brain,
                calls=res.calls,
                observations=res.observations,
                reply=res.reply,
                ms=res.ms,
                tok_per_s=float(stats.get("tok_per_s", 0.0)) if res.source != "fast" else 0.0,
                retried=bool(stats.get("retried", False)) if res.source != "fast" else False,
                steps=res.steps,
            )
        except Exception:
            pass
