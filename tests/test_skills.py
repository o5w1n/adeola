"""Unit tests for Phase 4: SKILL.md discovery, vault output writing, trace suggestions, and /api/skills."""

import json
import os
import tempfile
from pathlib import Path

os.environ["ADEOLA_DRY_RUN"] = "1"

import unittest  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app import config  # noqa: E402
from app.brain.router import Router  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.server import AdeolaServer  # noqa: E402
from app.skills.loader import (  # noqa: E402
    default_outputs_dir,
    discover_skills,
    save_vault_output,
    suggest_skills_from_traces,
)
from app.tools.applications import AppIndex  # noqa: E402
from app.tools.projects import Projects  # noqa: E402
from app.tools.registry import Toolbox  # noqa: E402


class SkillsTests(unittest.TestCase):
    def test_discover_skills_and_save_vault_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdir = root / "daily-check"
            sdir.mkdir()
            (sdir / "SKILL.md").write_text(
                "---\nname: daily-check\ndescription: Quick check.\ndomain: ops\ntriggers: daily, check\n---\nwhat time is it\n",
                encoding="utf-8",
            )
            skills = discover_skills([root])
            self.assertEqual(len(skills), 1)
            self.assertEqual(skills[0].name, "daily-check")
            self.assertEqual(skills[0].domain, "ops")
            self.assertEqual(skills[0].triggers, ["daily", "check"])

            out_path = save_vault_output("daily-check", "It is 10:00 AM.", out_dir=root / "outputs")
            self.assertTrue(out_path.is_file())
            self.assertIn("It is 10:00 AM.", out_path.read_text(encoding="utf-8"))

    def test_outputs_default_to_data_dir_not_the_vault(self):
        with patch.dict(os.environ, {"ADEOLA_VAULT_OUT": ""}):
            self.assertEqual(default_outputs_dir(), config.DATA_DIR / "outputs")
            for notes_dir in config.NOTES_DIRS:
                self.assertNotIn(Path(notes_dir).expanduser(), default_outputs_dir().parents)

    def test_vault_outputs_are_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ADEOLA_VAULT_OUT": tmp}):
            self.assertEqual(default_outputs_dir(), Path(tmp))

    def test_suggest_skills_from_traces_done_twice(self):
        mem = Memory(":memory:")
        for _ in range(2):
            mem.add_trace("check my sprint", "check my sprint", "llm", "qwen3:4b", [], [], "ok", 100.0)
        mem.add_trace("open brave", "open brave", "fast", "fast", [], [], "ok", 1.0)
        mem.add_trace("open brave", "open brave", "fast", "fast", [], [], "ok", 1.0)
        sugg = suggest_skills_from_traces(mem, min_count=2)
        self.assertEqual(sugg, [{"command": "check my sprint", "count": 2}])

    def test_server_skills_endpoints(self):
        mem = Memory(":memory:")
        router = Router(Toolbox(AppIndex(), mem, Projects(mem, roots=[])), brain=None)
        srv = AdeolaServer(router)
        status, _, body = srv.dispatch("GET", "/api/skills", {}, b"")
        self.assertEqual(status, 200)
        data = json.loads(body)
        names = [s["name"] for s in data["skills"]]
        self.assertIn("morning-brief", names)
        self.assertIn("project-status", names)

        status, _, body = srv.dispatch("POST", "/api/skills/run", {}, json.dumps({"name": "unknown"}).encode())
        self.assertEqual(status, 404)

