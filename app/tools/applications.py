"""Find and launch installed Windows apps by the name you'd say out loud."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process, utils

from app import config

_JUNK_WORDS = ("uninstall", "readme", "release notes", "help", "documentation",
               "website", "license", "manual", "changelog")
_FILLER_PREFIXES = ("the ", "my ")
_FILLER_SUFFIXES = (" app", " application", " program", " browser")


@dataclass
class App:
    name: str     # display name, e.g. "Visual Studio Code"
    target: str   # what we hand to Windows: exe, shortcut path or shell:AppsFolder id


def normalize(name: str) -> str:
    q = " ".join(name.lower().strip(" .!?'\"").split())
    for p in _FILLER_PREFIXES:
        if q.startswith(p) and len(q) > len(p):
            q = q[len(p):]
    for s in _FILLER_SUFFIXES:
        if q.endswith(s) and len(q) > len(s):
            q = q[: -len(s)]
    return config.APP_ALIASES.get(q, q)


class AppIndex:
    """Name -> App lookup built from the Start menu, plus Windows built-ins."""

    def __init__(self, apps: dict[str, App] | None = None):
        self.apps = apps if apps is not None else self._discover()

    # -- discovery -----------------------------------------------------------
    @staticmethod
    def _discover() -> dict[str, App]:
        apps = {k: App(k.title(), v) for k, v in config.BUILTIN_APPS.items()}
        if sys.platform != "win32":
            return apps
        apps.update(AppIndex._scan_shortcuts())
        apps.update(AppIndex._get_start_apps())  # most complete; wins on clashes
        return apps

    @staticmethod
    def _keep(name: str) -> bool:
        low = name.lower()
        return bool(low) and not any(w in low for w in _JUNK_WORDS)

    @staticmethod
    def _scan_shortcuts() -> dict[str, App]:
        found = {}
        for var in ("APPDATA", "PROGRAMDATA"):
            root = Path(os.environ.get(var, "")) / "Microsoft/Windows/Start Menu/Programs"
            if not root.is_dir():
                continue
            for lnk in root.rglob("*.lnk"):
                if AppIndex._keep(lnk.stem):
                    found[lnk.stem.lower()] = App(lnk.stem, str(lnk))
        return found

    @staticmethod
    def _get_start_apps() -> dict[str, App]:
        """Get-StartApps covers desktop apps AND Microsoft Store apps."""
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            data = json.loads(out.stdout or "[]")
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return {}
        if isinstance(data, dict):
            data = [data]
        found = {}
        for item in data:
            name, app_id = item.get("Name") or "", item.get("AppID") or ""
            if app_id and AppIndex._keep(name):
                found[name.lower()] = App(name, f"shell:AppsFolder\\{app_id}")
        return found

    # -- lookup --------------------------------------------------------------
    def resolve(self, name: str) -> tuple[App | None, str | None]:
        """Return (app, None) on a confident match, else (None, best_guess_name)."""
        q = normalize(name)
        if not q:
            return None, None
        if q in self.apps:
            return self.apps[q], None
        # "android studio" -> "android studio 2025.1", "brave" -> "brave beta"
        prefixed = [k for k in self.apps if k.startswith(q + " ")]
        if prefixed:
            return self.apps[min(prefixed, key=len)], None
        match = process.extractOne(q, list(self.apps), scorer=fuzz.WRatio,
                                   processor=utils.default_process)
        if match and match[1] >= 88:
            return self.apps[match[0]], None
        guess = self.apps[match[0]].name if match and match[1] >= 60 else None
        return None, guess

    def can_open(self, name: str) -> bool:
        return self.resolve(name)[0] is not None or normalize(name) in config.SITE_SHORTCUTS


def launch(target: str) -> None:
    if config.DRY_RUN:
        print(f"   [dry run] would launch: {target}")
        return
    if sys.platform != "win32":
        raise OSError("Launching apps is only supported on Windows.")
    os.startfile(target)  # ShellExecute: handles .exe, .lnk, shell: and ms-settings:
