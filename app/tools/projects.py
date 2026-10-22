"""Find your project folders and open them in the right app.

Folders directly inside config.PROJECT_ROOTS (e.g. ~/AndroidStudioProjects/Quail) are found
automatically. Android projects (Gradle files) open in Android Studio, everything else in
VS Code. Projects you add by hand ("add project X at C:\\path") override the scan.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process, utils

from app import config

_ANDROID_MARKERS = ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts", "gradlew")
_SKIP = {"node_modules", "venv", ".venv", "__pycache__", "build", "dist", "out", "bin", "obj"}


@dataclass
class Project:
    name: str
    path: Path
    app: str          # "android studio", "vs code" or "explorer"


def detect_app(folder: Path) -> str:
    if any((folder / m).exists() for m in _ANDROID_MARKERS):
        return "android studio"
    return "vs code"


def scan(roots=None) -> dict[str, Project]:
    found: dict[str, Project] = {}
    for root in roots if roots is not None else config.PROJECT_ROOTS:
        root = Path(os.path.expandvars(str(root))).expanduser()
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for d in entries:
            if d.is_dir() and not d.name.startswith(".") and d.name.lower() not in _SKIP:
                found.setdefault(d.name.lower(), Project(d.name, d, detect_app(d)))
    return found


class Projects:
    def __init__(self, memory=None, roots=None):
        self.memory = memory
        self.roots = roots
        self.reload()

    def reload(self) -> None:
        self.items = scan(self.roots)
        if self.memory:
            for name, path, app in self.memory.projects():   # hand-added ones win
                self.items[name.lower()] = Project(name, Path(path), app)

    def all(self) -> list[Project]:
        return sorted(self.items.values(), key=lambda p: p.name.lower())

    def find(self, name: str) -> Project | None:
        q = name.lower().strip()
        for suffix in (" project", " app", " repo", " folder"):
            if q.endswith(suffix):
                q = q[: -len(suffix)].strip()
        if q in self.items:
            return self.items[q]
        if not self.items:
            return None
        match = process.extractOne(q, list(self.items), scorer=fuzz.WRatio,
                                   processor=utils.default_process)
        return self.items[match[0]] if match and match[1] >= 85 else None

    def add(self, name: str, path: str) -> Project:
        folder = Path(os.path.expandvars(path.strip().strip('"'))).expanduser()
        if not folder.is_dir():
            raise FileNotFoundError(f"There's no folder at {folder}")
        p = Project(name.strip(), folder, detect_app(folder))
        if self.memory:
            self.memory.add_project(p.name, str(p.path), p.app)
        self.items[p.name.lower()] = p
        return p


def opener_command(app: str, folder: Path) -> list[str] | None:
    """The command that opens `folder` in `app`, or None if that app isn't installed."""
    for candidate in config.PROJECT_OPENERS.get(app, []):
        exe = os.path.expandvars(candidate)
        if os.path.isabs(exe):
            if Path(exe).exists():
                return [exe, str(folder)]
        elif shutil.which(exe):
            return [shutil.which(exe), str(folder)]
    return None


def open_project(p: Project) -> str:
    """Opens the folder; falls back to File Explorer if the app can't be found."""
    cmd = opener_command(p.app, p.path)
    if config.DRY_RUN:
        print(f"   [dry run] would run: {cmd or ['explorer', str(p.path)]}")
    elif cmd:
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    elif sys.platform == "win32":
        os.startfile(str(p.path))
    else:
        raise OSError("Opening folders only works on Windows.")
    where = p.app.title().replace("Vs ", "VS ") if cmd else "File Explorer"
    return f"Opening your {p.name} project in {where}."
