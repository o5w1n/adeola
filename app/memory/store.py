"""Adeola's memory: one SQLite file on your PC (data/adeola.db, never uploaded).

Three tables:
  facts     things you asked her to remember ("I'm working on Quail this week")
  projects  folders you added by hand (scanned folders are found automatically)
  activity  a log of what she did for you, for "what was I doing earlier?"
  counters  small numbers: how often each app was opened, which tips were taught
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from rapidfuzz import fuzz

_STOP = set("""a an the and or but if of to in on at for with about from by is are was were be been
am i im i'm me my mine you your we our it its this that these those what whats what's who how when
where which do does did doing done have has had will would can could should please just really
remember recall forget know thing things something anything tell""".split())

_SECOND_PERSON = [
    (r"\bi am\b", "you are"), (r"\bi'm\b", "you're"), (r"\bim\b", "you're"),
    (r"\bi've\b", "you've"), (r"\bi'll\b", "you'll"), (r"\bi'd\b", "you'd"),
    (r"\bmyself\b", "yourself"), (r"\bmy\b", "your"), (r"\bmine\b", "yours"),
    (r"\bme\b", "you"), (r"\bi\b", "you"),
]


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _STOP and len(w) > 1}


def to_second_person(text: str) -> str:
    """'I'm working on my app' -> 'you're working on your app' (for read-backs)."""
    out = text
    for pat, rep in _SECOND_PERSON:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    return out


@dataclass
class Fact:
    id: int
    text: str
    created: str


class Memory:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # Used from the typing loop and the voice threads; one lock keeps it simple.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY, text TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS projects (
                    name TEXT PRIMARY KEY, path TEXT NOT NULL, app TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS counters (key TEXT PRIMARY KEY, n INTEGER NOT NULL);
            """)

    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock, self._db:
            return self._db.execute(sql, args).fetchall()

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # -- facts -----------------------------------------------------------------------
    def add_fact(self, text: str) -> Fact:
        text = text.strip().rstrip(".")
        text = text[0].upper() + text[1:] if text else text
        for f in self.facts():                      # don't store the same thing twice
            if fuzz.ratio(f.text.lower(), text.lower()) >= 92:
                return f
        now = self._now()
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO facts (text, created) VALUES (?, ?)", (text, now))
        return Fact(cur.lastrowid, text, now)

    def facts(self) -> list[Fact]:
        return [Fact(*r) for r in self._q("SELECT id, text, created FROM facts ORDER BY id")]

    def search(self, query: str, limit: int = 5) -> list[Fact]:
        """Facts that share real words with the query, best first."""
        q = words(query)
        scored = []
        for f in self.facts():
            overlap = len(q & words(f.text))
            fuzzy = fuzz.token_set_ratio(query.lower(), f.text.lower()) if q else 0
            if overlap or fuzzy >= 80:
                scored.append((overlap * 100 + fuzzy, f.id, f))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [f for *_, f in scored[:limit]]

    def delete_fact(self, fact_id: int) -> None:
        self._q("DELETE FROM facts WHERE id = ?", (fact_id,))

    def clear_facts(self) -> int:
        n = len(self.facts())
        self._q("DELETE FROM facts")
        return n

    # -- projects ---------------------------------------------------------------------
    def add_project(self, name: str, path: str, app: str) -> None:
        self._q("INSERT OR REPLACE INTO projects (name, path, app, created) VALUES (?, ?, ?, ?)",
                (name, path, app, self._now()))

    def projects(self) -> list[tuple[str, str, str]]:
        return self._q("SELECT name, path, app FROM projects ORDER BY name")

    # -- activity ---------------------------------------------------------------------
    def log(self, kind: str, detail: str) -> None:
        self._q("INSERT INTO activity (ts, kind, detail) VALUES (?, ?, ?)", (self._now(), kind, detail))
        self._q("DELETE FROM activity WHERE ts < ?",          # keep 30 days
                ((datetime.now() - timedelta(days=30)).isoformat(timespec="seconds"),))

    def recent(self, hours: float = 24, limit: int = 50) -> list[tuple[str, str, str]]:
        since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        rows = self._q("SELECT ts, kind, detail FROM activity WHERE ts >= ? ORDER BY id DESC LIMIT ?",
                       (since, limit))
        return rows[::-1]

    # -- counters -----------------------------------------------------------------------
    def get_counter(self, key: str) -> int:
        rows = self._q("SELECT n FROM counters WHERE key = ?", (key,))
        return rows[0][0] if rows else 0

    def set_counter(self, key: str, n: int) -> None:
        self._q("INSERT OR REPLACE INTO counters (key, n) VALUES (?, ?)", (key, n))

    def bump(self, key: str) -> int:
        n = self.get_counter(key) + 1
        self.set_counter(key, n)
        return n

    def close(self) -> None:
        self._db.close()
