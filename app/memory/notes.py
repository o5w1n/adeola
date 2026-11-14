"""Read-only markdown index over the user's Obsidian vault (never writes to vault files)."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
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
    mtime: float = 0.0


_RECENCY_RE = re.compile(
    r"\b(?:last|latest|newest|recent|recently)\b.*\b(?:note|notes|vault|brain|added|created|updated|saved)\b"
    r"|\b(?:note|notes|vault)\b.*\b(?:last|latest|newest|recent|recently)\b"
    r"|^(?:last|latest|newest|recent)(?:\s+notes?)?$",
    re.IGNORECASE,
)


def is_recency_query(query: str) -> bool:
    return bool(_RECENCY_RE.search(query.strip()))


class NotesIndex:
    """In-memory, read-only index of markdown notes."""

    def __init__(self, dirs: list[str | Path] | None = None, captures_dir: Path | str | None = None):
        self.dirs: list[Path] = [
            Path(d).expanduser() for d in (config.NOTES_DIRS if dirs is None else dirs)
        ]
        self._explicit_captures: bool = captures_dir is not None
        self.captures_dir: Path | None = (
            Path(captures_dir).expanduser()
            if captures_dir is not None
            else (config.CAPTURES_DIR if dirs is None else None)
        )
        self.notes: list[Note] = []
        self.missing: list[Path] = []
        self.load_ms: float = 0.0
        self._words: list[tuple[set[str], set[str]]] = []
        self.load()

    def _scan_root(self, root: Path, exclude: set[str], max_bytes: int, default_folder: str = "") -> None:
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
                    st = fpath.stat()
                    if st.st_size > max_bytes:
                        continue
                    raw = fpath.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue

                body = _clean_markdown(raw)
                rel_parent = rel.parent
                folder = default_folder if str(rel_parent) == "." else rel_parent.as_posix()
                note = Note(title=fpath.stem, path=fpath, folder=folder, body=body, mtime=st.st_mtime)
                self.notes.append(note)
                self._words.append((words(note.title), words(note.body)))

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

            self._scan_root(root, exclude, max_bytes, default_folder="")

        if (
            self.captures_dir is not None
            and self.captures_dir not in self.dirs
            and self.captures_dir.exists()
            and self.captures_dir.is_dir()
        ):
            try:
                os.scandir(self.captures_dir).close()
                self._scan_root(self.captures_dir, exclude, max_bytes, default_folder="captures")
            except OSError:
                pass

        self.load_ms = (time.perf_counter() - t0) * 1000.0
        return len(self.notes)

    def add_capture(self, text: str) -> Note | None:
        """Write a markdown capture note (when allowed) and index it live in memory."""
        clean_text = text.strip().rstrip(".")
        if not clean_text:
            return None
        raw_title = " ".join(clean_text.split()[:6]).strip(" \t\r\n.,;:!?\"'()[]{}-")
        title = (raw_title[0].upper() + raw_title[1:]) if raw_title else "Capture"
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "capture"
        today = datetime.now().strftime("%Y-%m-%d")
        raw_md = f"# {title}\n\nDate: {today}\n\n{clean_text}.\n"
        body = _clean_markdown(raw_md)
        cap_root = self.captures_dir or config.CAPTURES_DIR
        fpath = cap_root / f"{today}-{slug}.md"

        if self._explicit_captures or not config.DRY_RUN:
            cap_root.mkdir(parents=True, exist_ok=True)
            fpath.write_text(raw_md, encoding="utf-8")
            try:
                mtime = fpath.stat().st_mtime
            except OSError:
                mtime = time.time()
        else:
            mtime = time.time()

        note = Note(title=title, path=fpath, folder="captures", body=body, mtime=mtime)
        w_pair = (words(note.title), words(note.body))
        for i, existing in enumerate(self.notes):
            if existing.path == fpath:
                self.notes[i] = note
                self._words[i] = w_pair
                break
        else:
            self.notes.append(note)
            self._words.append(w_pair)
        return note

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

    def recent(self, limit: int = config.NOTES_IN_PROMPT) -> list[Note]:
        """Return the `limit` most recently modified curated notes (newest first, generated Graph/ last)."""
        ordered = sorted(
            self.notes,
            key=lambda n: (not n.folder.startswith("Graph"), n.mtime, n.title.lower()),
            reverse=True,
        )
        return ordered[:limit]

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
