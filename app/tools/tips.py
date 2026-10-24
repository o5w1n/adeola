"""Fading tips: Adeola teaches you shortcuts so you need her less.

Inspired by "help that fades": the first time you open an app she mentions one useful
shortcut. Later opens get the next tip on a spaced schedule, each tip is retired after
being taught TIP_REPEATS times, and once every tip for an app is learned she stops.
Say "no more tips" to switch them off, "turn tips on" to bring them back.
"""

from __future__ import annotations

from app import config

# Keyed by the lowercase app name Windows shows. Short, so they work spoken aloud.
TIPS: dict[str, list[str]] = {
    "brave": ["Ctrl+L jumps straight to the address bar.",
              "Ctrl+Shift+T brings back a tab you closed by mistake.",
              "Ctrl+Tab moves to the next tab."],
    "google chrome": ["Ctrl+L jumps straight to the address bar.",
                      "Ctrl+Shift+T brings back a tab you closed by mistake."],
    "microsoft edge": ["Ctrl+L jumps straight to the address bar.",
                       "Ctrl+Shift+T brings back a tab you closed by mistake."],
    "visual studio code": ["Ctrl+Shift+P opens the command palette, where every command lives.",
                           "Ctrl+P finds any file in the project by name.",
                           "Ctrl+backtick opens the terminal."],
    "android studio": ["Press Shift twice to search for anything.",
                       "Shift+F10 runs your app.",
                       "Ctrl+Alt+L reformats your code."],
    "file explorer": ["Windows+E opens File Explorer instantly, no need to ask me.",
                      "Alt+Up goes to the parent folder."],
    "task manager": ["Ctrl+Shift+Escape opens Task Manager instantly, no need to ask me."],
    "settings": ["Windows+I opens Settings instantly, no need to ask me."],
    "notepad": ["Ctrl+Shift+S saves a copy under a new name."],
    "calculator": ["You can type numbers on your keyboard instead of clicking."],
    "terminal": ["Ctrl+Shift+T opens a new tab."],
}

# Which opens of an app get a tip: the 1st, 3rd, 6th, 10th, ... (gaps grow).
_SCHEDULE = {1, 3, 6, 10, 15, 21, 28, 36}


class Tips:
    def __init__(self, memory=None):
        self.memory = memory

    @property
    def enabled(self) -> bool:
        return bool(self.memory) and config.TIPS and not self.memory.get_counter("tips_off")

    def set_enabled(self, on: bool) -> str:
        if self.memory is None:
            return "Tips need my memory, which is switched off."
        self.memory.set_counter("tips_off", 0 if on else 1)
        return "Tips are back on." if on else "Okay, no more tips."

    def after_open(self, app_name: str) -> str | None:
        """Call each time an app is opened. Returns a tip to add to the reply, or None."""
        tips = TIPS.get(app_name.lower())
        if not tips or not self.enabled:
            return None
        opens = self.memory.bump(f"open:{app_name.lower()}")
        if opens not in _SCHEDULE:
            return None
        for i, tip in enumerate(tips):
            key = f"tip:{app_name.lower()}:{i}"
            if self.memory.get_counter(key) < config.TIP_REPEATS:
                self.memory.bump(key)
                return f"Tip: {tip}"
        return None   # all learned
