"""The approved tools. The model can only ever pick from this list."""

from __future__ import annotations

import sys
import urllib.parse
import webbrowser
from datetime import datetime

from app import config
from app.memory.store import to_second_person
from app.tools.applications import AppIndex, launch, normalize
from app.tools.projects import open_project as _open_project_folder

MEMORY_OFF = "My memory is switched off."


class Toolbox:
    def __init__(self, apps: AppIndex, memory=None, projects=None):
        self.apps = apps
        self.memory = memory        # app.memory.store.Memory, or None
        self.projects = projects    # app.tools.projects.Projects, or None
        self._funcs = {
            "open_app": self.open_app,
            "open_url": self.open_url,
            "web_search": self.web_search,
            "get_time": self.get_time,
            "lock_pc": self.lock_pc,
            "open_project": self.open_project,
            "list_projects": self.list_projects,
            "remember": self.remember,
            "recall": self.recall,
            "forget": self.forget,
            "recent_activity": self.recent_activity,
        }
        self._pending_forget_all = False

    # -- tools ---------------------------------------------------------------
    def open_app(self, name: str) -> str:
        app, guess = self.apps.resolve(name)
        if app:
            launch(app.target)
            self._log("app", app.name)
            return f"Opening {app.name}."
        site = config.SITE_SHORTCUTS.get(normalize(name))
        if site:
            return self.open_url(site)
        hint = f" Did you mean {guess}?" if guess else ""
        return f"I couldn't find an app called {name}.{hint}"

    def open_url(self, url: str) -> str:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self._open_in_browser(url)
        site = urllib.parse.urlparse(url).netloc or url
        self._log("site", site)
        return f"Opening {site}."

    def web_search(self, query: str) -> str:
        self._open_in_browser(config.SEARCH_URL + urllib.parse.quote_plus(query))
        self._log("search", query)
        return f"Searching for {query}."

    def get_time(self) -> str:
        now = datetime.now()
        return f"It's {now:%H:%M} on {now:%A}, {now.day} {now:%B}."

    def lock_pc(self) -> str:
        if config.DRY_RUN:
            print("   [dry run] would lock the PC")
        elif sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
        else:
            return "Locking only works on Windows."
        return "Locking your PC."

    # -- projects --------------------------------------------------------------
    def open_project(self, name: str) -> str:
        if self.projects is None:
            return "I don't have any projects set up."
        p = self.projects.find(name)
        if p is None:
            known = ", ".join(x.name for x in self.projects.all()[:6])
            hint = f" I know: {known}." if known else (
                " Add one by typing: add project NAME at C:\\path\\to\\folder")
            return f"I couldn't find a project called {name}.{hint}"
        reply = _open_project_folder(p)
        self._log("project", p.name)
        return reply

    def list_projects(self) -> str:
        items = self.projects.all() if self.projects else []
        if not items:
            return "I don't know any projects yet. Type: add project NAME at C:\\path\\to\\folder"
        return f"Your projects: {', '.join(p.name for p in items)}."

    # -- memory ----------------------------------------------------------------
    def remember(self, note: str) -> str:
        if self.memory is None:
            return MEMORY_OFF
        fact = self.memory.add_fact(note)
        return f"Got it. I'll remember that {to_second_person(fact.text[0].lower() + fact.text[1:])}."

    def recall(self, query: str = "") -> str:
        if self.memory is None:
            return MEMORY_OFF
        query = query.strip()
        everything = query.lower() in ("", "everything", "all", "anything", "me", "about me")
        facts = self.memory.facts()[-8:] if everything else self.memory.search(query, limit=3)
        if not facts:
            return ("You haven't asked me to remember anything yet." if everything
                    else f"I don't remember anything about {query}.")
        said = [to_second_person(f.text) for f in facts]
        if len(said) == 1:
            return f"You told me: {said[0]}."
        return "You told me: " + "; ".join(said) + "."

    def forget(self, note: str) -> str:
        if self.memory is None:
            return MEMORY_OFF
        note = note.strip()
        if note.lower() in ("everything", "all", "everything you know", "all of it"):
            self._pending_forget_all = True
            n = len(self.memory.facts())
            return (f"That would erase all {n} things I remember. "
                    "Say 'yes, forget everything' to confirm.")
        if note.lower() in ("that", "it", "this", "the last thing", "last"):
            facts = self.memory.facts()[-1:]
        else:
            facts = self.memory.search(note, limit=1)
        if not facts:
            return f"I don't remember anything about {note}."
        self.memory.delete_fact(facts[0].id)
        return f"Forgotten: {to_second_person(facts[0].text)}."

    def confirm_forget_all(self) -> str:
        if self.memory is None:
            return MEMORY_OFF
        if not self._pending_forget_all:
            return "Say 'forget everything' first if you want me to erase my memory."
        self._pending_forget_all = False
        n = self.memory.clear_facts()
        return f"Done. I've forgotten all {n} things you told me."

    def recent_activity(self) -> str:
        if self.memory is None:
            return MEMORY_OFF
        rows = self.memory.recent(hours=24)
        if not rows:
            return "I haven't done anything for you in the last day."
        verbs = {"project": "opened your {} project", "app": "opened {}",
                 "site": "went to {}", "search": "searched for {}"}
        parts, seen = [], set()
        for ts, kind, detail in reversed(rows):           # newest first, no repeats
            if kind in verbs and (kind, detail.lower()) not in seen:
                seen.add((kind, detail.lower()))
                parts.append(f"{verbs[kind].format(detail)} at {ts[11:16]}")
            if len(parts) == 5:
                break
        if not parts:
            return "I haven't opened anything for you in the last day."
        return "Most recently, you " + "; ".join(parts) + "."

    def _log(self, kind: str, detail: str) -> None:
        if self.memory is not None and not config.DRY_RUN:
            self.memory.log(kind, detail)

    @staticmethod
    def _open_in_browser(url: str) -> None:
        if config.DRY_RUN:
            print(f"   [dry run] would open: {url}")
        else:
            webbrowser.open(url)

    # -- dispatch ------------------------------------------------------------
    def run(self, name: str, args: dict) -> str:
        func = self._funcs.get(name)
        if func is None:
            return f"I don't have a tool called {name}."
        spec = next(s["function"] for s in SCHEMAS if s["function"]["name"] == name)
        allowed = spec["parameters"]["properties"]
        clean = {k: str(v) for k, v in (args or {}).items() if k in allowed}
        required = [k for k in spec["parameters"].get("required", []) if k not in OPTIONAL_ARGS]
        missing = [k for k in required if not clean.get(k)]
        if missing:
            return f"I need a {missing[0]} to do that."
        try:
            return func(**clean)
        except OSError as e:
            return f"That didn't work: {e}"


def _tool(tool_name: str, description: str, /, **params: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": "string", "description": v} for k, v in params.items()},
                "required": list(params),
            },
        },
    }


SCHEMAS = [
    _tool("open_app", "Open an installed application on the PC.",
          name="App name as shown in the Windows Start menu, e.g. 'Brave', 'Visual Studio Code'."),
    _tool("open_url", "Open a specific website in the browser, e.g. 'youtube.com'.",
          url="The web address, e.g. 'github.com'."),
    _tool("web_search", "Search the web, only when the user asks to search or look something "
          "up, or needs current information. Answer general-knowledge questions yourself.",
          query="What to search for."),
    _tool("get_time", "Tell the current time, date or day. Only when the user explicitly "
          "asks what time, date or day it is, never for greetings like 'what's up'."),
    _tool("lock_pc", "Lock the computer screen."),
    _tool("open_project", "Open one of the user's code projects in its editor "
          "(Android Studio or VS Code). Use for 'open my X project', 'let's work on X'.",
          name="The project name, e.g. 'Quail'."),
    _tool("list_projects", "List the user's projects."),
    _tool("remember", "Save something the user wants you to remember about them or their work.",
          note="What to remember, in the user's words, e.g. 'I'm working on Quail this week'."),
    _tool("recall", "Look up what the user asked you to remember. Use for questions about "
          "the user's own plans, projects, preferences or anything they told you before.",
          query="What to look up, e.g. 'working on' or 'exam'. Empty for everything."),
    _tool("forget", "Delete one thing you were asked to remember.",
          note="Which memory to delete, e.g. 'my exam date'."),
    _tool("recent_activity", "Say what the user was doing recently: apps, projects and sites "
          "you opened for them. Use for 'what was I doing earlier?'."),
]

# Arguments the model may leave empty.
OPTIONAL_ARGS = {"query"}
