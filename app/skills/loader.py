"""Markdown SKILL.md loader, vault output writer, and trace-based skill suggestions."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app import config

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    domain: str = "general"
    triggers: list[str] = field(default_factory=list)
    save_to_vault: bool = True
    prompt: str = ""
    path: Path | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "triggers": self.triggers,
            "save_to_vault": self.save_to_vault,
            "path": str(self.path) if self.path else "",
        }


def default_skill_dirs() -> list[Path]:
    repo_skills = Path(__file__).resolve().parent.parent.parent / "skills"
    vault_skills = Path(config.NOTES_DIRS[0]).expanduser() / "Adeola" / "skills" if config.NOTES_DIRS else None
    dirs = [repo_skills]
    if vault_skills is not None:
        dirs.append(vault_skills)
    return dirs


def default_outputs_dir() -> Path:
    """data/outputs by default, so the vault stays read-only; set ADEOLA_VAULT_OUT to opt in."""
    env_out = os.getenv("ADEOLA_VAULT_OUT", "").strip()
    if env_out:
        return Path(env_out).expanduser()
    return config.DATA_DIR / "outputs"


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw.strip()
    meta_raw, body = m.group(1), m.group(2).strip()
    meta: dict[str, str] = {}
    for line in meta_raw.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, body


def discover_skills(dirs: list[Path] | None = None) -> list[Skill]:
    """Scan skill directories for `<name>/SKILL.md` files."""
    seen: dict[str, Skill] = {}
    for root in (dirs if dirs is not None else default_skill_dirs()):
        try:
            if not root.exists() or not root.is_dir():
                continue
            entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            skill_file = entry / "SKILL.md" if entry.is_dir() else None
            if skill_file is None or not skill_file.is_file():
                continue
            try:
                raw = skill_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            meta, body = _parse_frontmatter(raw)
            name = meta.get("name") or entry.name
            triggers = [t.strip() for t in meta.get("triggers", "").split(",") if t.strip()]
            save_flag = meta.get("save_to_vault", "true").lower() not in ("false", "0", "no")
            seen[name] = Skill(
                name=name,
                description=meta.get("description", ""),
                domain=meta.get("domain", "general"),
                triggers=triggers,
                save_to_vault=save_flag,
                prompt=body,
                path=skill_file,
            )
    return list(seen.values())


def save_vault_output(title: str, content: str, out_dir: Path | None = None) -> Path:
    """Save a markdown skill/agent output (see default_outputs_dir), falling back to data/outputs."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "output"
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filename = f"{stamp}_{slug}.md"
    target_dir = out_dir or default_outputs_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        path.write_text(f"# {title}\n\n{content.strip()}\n", encoding="utf-8")
        return path
    except OSError:
        fallback_dir = config.DATA_DIR / "outputs"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        path = fallback_dir / filename
        path.write_text(f"# {title}\n\n{content.strip()}\n", encoding="utf-8")
        return path


def suggest_skills_from_traces(memory, min_count: int = 2, limit: int = 5) -> list[dict]:
    """Find repeated non-fast requests in SQLite traces ('done twice -> make a skill')."""
    if memory is None or not hasattr(memory, "recent_traces"):
        return []
    try:
        counts: dict[str, int] = {}
        for t in memory.recent_traces(limit=200):
            if getattr(t, "source", "") != "fast" and getattr(t, "clean_text", ""):
                counts[t.clean_text] = counts.get(t.clean_text, 0) + 1
        ranked = sorted(
            ((cmd, n) for cmd, n in counts.items() if n >= min_count),
            key=lambda x: x[1],
            reverse=True,
        )
        return [{"command": cmd, "count": n} for cmd, n in ranked[:limit]]
    except Exception:
        return []
