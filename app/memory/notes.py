"""Read-only markdown index over the user's Obsidian vault (never writes to vault files)."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from app import config
from app.memory.store import words

_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.DOTALL)
_WIKILINK_ALIAS_RE = re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]")
_WIKILINK_PLAIN_RE = re.compile(r"\[\[([^\]]+)\]\]")
_GENERIC_QUERY_WORDS = frozenset("""
want wants need needs some more much many very also too now today here there make made use used
using get got give take let lets like good best new way time help open start run launch write
work working code app apps editor file files folder pc computer laptop
""".split())


def _clean_markdown(raw: str) -> str:
    """Strip YAML frontmatter and unwrap Obsidian wikilinks."""
    text = _FRONTMATTER_RE.sub("", raw, count=1)
    text = _WIKILINK_ALIAS_RE.sub(r"\2", text)
    text = _WIKILINK_PLAIN_RE.sub(r"\1", text)
    return text.strip()


@dataclass
class Note:
    title: str
    path: Path
    folder: str
    body: str


class NotesIndex:
    """In-memory, read-only index of markdown notes."""

    def __init__(self, dirs: list[str | Path] | None = None):
        self.dirs: list[Path] = [
            Path(d).expanduser() for d in (config.NOTES_DIRS if dirs is None else dirs)
        ]
        self.notes: list[Note] = []
        self.missing: list[Path] = []
        self.load_ms: float = 0.0
        self._words: list[tuple[set[str], set[str]]] = []
        self.load()

    def load(self) -> int:
        """(Re)scan configured directories and return the number of indexed notes."""
        t0 = time.perf_counter()
        self.notes = []
        self.missing = []
        self._words = []
        exclude = set(config.NOTES_EXCLUDE)
        max_bytes = config.NOTES_MAX_BYTES

        for root in self.dirs:
            try:
                if not root.exists() or not root.is_dir():
                    self.missing.append(root)
                    continue
                # Probe readability of root directory (handles sandbox/OS permission blocks)
                os.scandir(root).close()
            except OSError:
                self.missing.append(root)
                continue

            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in exclude)
                for fname in sorted(filenames):
                    if not fname.lower().endswith(".md"):
                        continue
                    fpath = Path(dirpath) / fname
                    try:
                        rel = fpath.relative_to(root)
                    except ValueError:
                        rel = Path(fname)
                    if any(part in exclude for part in rel.parts):
                        continue
                    try:
                        if fpath.stat().st_size > max_bytes:
                            continue
                        raw = fpath.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue

                    body = _clean_markdown(raw)
                    rel_parent = rel.parent
                    folder = "" if str(rel_parent) == "." else rel_parent.as_posix()
                    note = Note(title=fpath.stem, path=fpath, folder=folder, body=body)
                    self.notes.append(note)
                    self._words.append((words(note.title), words(note.body)))

        self.load_ms = (time.perf_counter() - t0) * 1000.0
        return len(self.notes)

    def search(
        self,
        query: str,
        limit: int = config.NOTES_IN_PROMPT,
        min_score: int = config.NOTES_MIN_SCORE,
    ) -> list[Note]:
        """Return up to `limit` notes scoring at least `min_score` against `query`."""
        raw_q = words(query)
        q = raw_q if min_score <= 1 else (raw_q - _GENERIC_QUERY_WORDS)
        if not q:
            return []
        scored: list[tuple[int, int, str, Note]] = []
        for note, (title_w, body_w) in zip(self.notes, self._words):
            score = 3 * len(q & title_w) + len(q & body_w)
            if score >= min_score:
                scored.append((-score, len(str(note.path)), note.title.lower(), note))
        scored.sort(key=lambda item: (item[0], item[1], item[2]))
        return [item[3] for item in scored[:limit]]

    def excerpt(
        self,
        note: Note,
        query: str,
        chars: int = config.NOTES_EXCERPT_CHARS,
    ) -> str:
        """Return a window of at most `chars` around the first query-word hit in `note.body`."""
        body = note.body
        if len(body) <= chars:
            return body.strip()

        q = words(query)
        lower_body = body.lower()
        first_pos: int | None = None

        for w in q:
            m = re.search(rf"\b{re.escape(w)}\b", lower_body)
            pos = m.start() if m else lower_body.find(w)
            if pos != -1 and (first_pos is None or pos < first_pos):
                first_pos = pos

        if first_pos is None:
            return body[:chars].strip()

        start = max(0, first_pos - chars // 3)
        end = start + chars
        if end > len(body):
            end = len(body)
            start = max(0, end - chars)
        return body[start:end].strip()
