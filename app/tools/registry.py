"""The approved tools. The model can only ever pick from this list."""

from __future__ import annotations

import sys
import urllib.parse
import webbrowser
from datetime import datetime

from app import config
from app.tools.applications import AppIndex, launch, normalize


class Toolbox:
    def __init__(self, apps: AppIndex):
        self.apps = apps
        self._funcs = {
            "open_app": self.open_app,
            "open_url": self.open_url,
            "web_search": self.web_search,
            "get_time": self.get_time,
            "lock_pc": self.lock_pc,
        }

    # -- tools ---------------------------------------------------------------
    def open_app(self, name: str) -> str:
        app, guess = self.apps.resolve(name)
        if app:
            launch(app.target)
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
        return f"Opening {urllib.parse.urlparse(url).netloc or url}."

    def web_search(self, query: str) -> str:
        self._open_in_browser(config.SEARCH_URL + urllib.parse.quote_plus(query))
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
        missing = [k for k in spec["parameters"].get("required", []) if not clean.get(k)]
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
    _tool("get_time", "Tell the current time and date."),
    _tool("lock_pc", "Lock the computer screen."),
]
