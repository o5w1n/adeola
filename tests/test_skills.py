"""Unit tests for Phase 4: SKILL.md discovery, vault output writing, trace suggestions, and /api/skills."""

import contextlib
import io
import json
import os
import tempfile
from pathlib import Path

os.environ["ADEOLA_DRY_RUN"] = "1"

import unittest  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app import config  # noqa: E402
from app.brain.router import Router, clean  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.server import AdeolaServer  # noqa: E402
from app.skills.loader import (  # noqa: E402
    Skill,
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


def _clock_skill(**kw) -> Skill:
    """A skill whose prompt resolves on the fast path, so no model is needed."""
    return Skill(name=kw.get("name", "clock-check"), description="Tell the time.",
                 triggers=kw.get("triggers", ["clock check", "what o'clock"]),
                 save_to_vault=kw.get("save", True), prompt=kw.get("prompt", "what time is it"))


class _RecordingBrain:
    model = config.MODEL

    def __init__(self):
        self.seen: list[str] = []
        self.history: list[dict] = []
        self.last_stats: dict = {}

    def decide(self, text, facts=None, notes=None, observations=None):
        self.seen.append(text)
        return [], "Model answer."

    def remember(self, *_):
        pass


class SkillRoutingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)
        env = patch.dict(os.environ, {"ADEOLA_VAULT_OUT": str(self.out)})
        env.start()
        self.addCleanup(env.stop)
        self.mem = Memory(":memory:")
        self.addCleanup(self.mem.close)

    def _router(self, *skills, brain=None):
        return Router(Toolbox(AppIndex({}), self.mem), brain, skills=list(skills))

    def _run(self, router, text):
        with contextlib.redirect_stdout(io.StringIO()):
            return router.handle(text)

    def test_trigger_phrase_runs_the_skill_and_saves_output(self):
        res = self._run(self._router(_clock_skill()), "Adeola, clock check")
        self.assertTrue(res.reply.startswith("It's"), res.reply)
        self.assertEqual(res.skill, "clock-check")
        saved = Path(res.saved_to)
        self.assertEqual(saved.parent, self.out)
        self.assertIn(res.reply, saved.read_text(encoding="utf-8"))

    def test_run_by_name_tolerates_spaces_hyphens_and_my(self):
        router = self._router(_clock_skill())
        for text in ("run the clock check skill", "run my clock-check skill", "run clock check skill"):
            self.assertEqual(self._run(router, text).skill, "clock-check", text)
        self.assertEqual(router.run_skill("clock-check").skill, "clock-check")

    def test_unknown_skill_name_lists_the_real_ones(self):
        res = self._run(self._router(_clock_skill()), "run the banana skill")
        self.assertEqual(res.skill, "")
        self.assertIn("banana", res.reply)
        self.assertIn("clock-check", res.reply)

    def test_a_skill_prompt_cannot_trigger_a_skill(self):
        looping = _clock_skill(name="loop", triggers=["loop forever"], prompt="loop forever")
        res = self._run(self._router(looping), "loop forever")
        self.assertEqual(res.skill, "loop")
        self.assertNotEqual(res.source, "error")

    def test_output_not_saved_when_disabled_or_failed(self):
        quiet = _clock_skill(save=False)
        self.assertEqual(self._run(self._router(quiet), "clock check").saved_to, "")
        failing = _clock_skill(prompt="ponder the meaning of life")   # needs a model; none here
        res = self._run(self._router(failing), "clock check")
        self.assertEqual(res.source, "none")
        self.assertEqual(res.saved_to, "")
        self.assertEqual(list(self.out.iterdir()), [])

    def test_suggestions_skip_skill_runs_triggers_and_failures(self):
        skills = [_clock_skill()]
        for text, source in [("check my sprint", "agent"), ("check my sprint", "chat"),
                             ("run the clock-check skill", "agent"), ("run the clock-check skill", "agent"),
                             ("clock check", "agent"), ("clock check", "agent"),
                             ("what is the meaning of life", "none"), ("what is the meaning of life", "error")]:
            self.mem.add_trace(text, text, source, "qwen3:4b", [], [], "ok", 100.0)
        self.assertEqual(suggest_skills_from_traces(self.mem, skills=skills),
                         [{"command": "check my sprint", "count": 2}])

    def test_empty_skill_prompt_is_refused_without_asking_the_model(self):
        brain = _RecordingBrain()
        res = self._run(self._router(_clock_skill(prompt="   "), brain=brain), "clock check")
        self.assertEqual(res.skill, "clock-check")
        self.assertIn("empty", res.reply)
        self.assertEqual(brain.seen, [])
        self.assertEqual(res.saved_to, "")

    def test_shipped_triggers_do_not_shadow_fast_path_commands(self):
        router = self._router()
        for skill in discover_skills():
            for trigger in skill.triggers:
                self.assertIsNone(router.fast_parse(clean(trigger), clean(trigger, keep_case=True)),
                                  f"{skill.name} trigger {trigger!r} would hijack an instant command")

    def test_server_runs_skills_through_the_router(self):
        srv = AdeolaServer(self._router(_clock_skill()))
        status, _, body = srv.dispatch("GET", "/api/skills", {}, b"")
        data = json.loads(body)
        self.assertEqual([s["name"] for s in data["skills"]], ["clock-check"])
        self.assertIn("suggested", data)

        status, _, body = srv.dispatch("POST", "/api/skills/run", {}, json.dumps({"name": "clock-check"}).encode())
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["skill"], "clock-check")
        self.assertTrue(data["reply"].startswith("It's"))
        self.assertTrue(Path(data["saved_to"]).is_file())

        status, _, _ = srv.dispatch("POST", "/api/skills/run", {}, json.dumps({}).encode())
        self.assertEqual(status, 400)
        status, _, _ = srv.dispatch("POST", "/api/skills/run", {}, json.dumps({"name": "banana"}).encode())
        self.assertEqual(status, 404)

