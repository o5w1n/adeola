"""Memory and project tests. Uses a throwaway in-memory database and temp folders.

Run from the project folder:  python -m unittest -v
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import contextlib  # noqa: E402
import io          # noqa: E402
import tempfile    # noqa: E402
import unittest    # noqa: E402
from pathlib import Path  # noqa: E402

from app.brain.router import Router  # noqa: E402
from app.memory.store import Memory, to_second_person  # noqa: E402
from app.tools.applications import App, AppIndex  # noqa: E402
from app.tools.projects import Projects, detect_app  # noqa: E402
from app.tools.registry import Toolbox  # noqa: E402


def run(router, text):
    with contextlib.redirect_stdout(io.StringIO()):
        return router.handle(text)


class FakeBrain:
    def __init__(self, calls=None, reply=""):
        self.calls, self.reply, self.seen, self.history = calls or [], reply, [], []

    def decide(self, text, facts=None):
        self.seen.append((text, facts))
        return self.calls, self.reply

    def remember(self, *_):
        pass


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.m = Memory(":memory:")
        self.addCleanup(self.m.close)

    def test_add_search_delete(self):
        self.m.add_fact("I'm working on Quail this week")
        self.m.add_fact("My exam is on Friday")
        self.assertEqual([f.text for f in self.m.search("what am I working on")],
                         ["I'm working on Quail this week"])
        self.assertEqual(self.m.search("exam")[0].text, "My exam is on Friday")
        self.assertEqual(self.m.search("pizza"), [])
        self.m.delete_fact(self.m.search("exam")[0].id)
        self.assertEqual(len(self.m.facts()), 1)

    def test_no_duplicates(self):
        self.m.add_fact("my exam is on friday")
        self.m.add_fact("My exam is on Friday.")
        self.assertEqual(len(self.m.facts()), 1)

    def test_persists_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "sub" / "adeola.db"
            m = Memory(db)
            m.add_fact("I like jollof")
            m.close()
            m2 = Memory(db)
            self.assertEqual(m2.facts()[0].text, "I like jollof")
            m2.close()

    def test_second_person(self):
        self.assertEqual(to_second_person("I'm working on my app"), "you're working on your app")
        self.assertEqual(to_second_person("I am Albert"), "you are Albert")


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "Quail").mkdir()
        (root / "Quail" / "settings.gradle.kts").write_text("")
        (root / "adeola").mkdir()
        (root / "node_modules").mkdir()
        self.root = root
        self.mem = Memory(":memory:")
        self.addCleanup(self.mem.close)
        self.projects = Projects(self.mem, roots=[root])

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_and_detect(self):
        names = [p.name for p in self.projects.all()]
        self.assertEqual(names, ["adeola", "Quail"])
        self.assertEqual(self.projects.find("quail").app, "android studio")
        self.assertEqual(self.projects.find("Adeola project").app, "vs code")
        self.assertIsNone(self.projects.find("banana"))

    def test_add_by_hand_persists(self):
        extra = self.root / "elsewhere" / "Khijani"
        extra.mkdir(parents=True)
        self.projects.add("Khijani", str(extra))
        again = Projects(self.mem, roots=[])
        self.assertEqual(again.find("khijani").path, extra)
        with self.assertRaises(FileNotFoundError):
            self.projects.add("Nope", str(self.root / "missing"))

    def test_detect_app(self):
        self.assertEqual(detect_app(self.root / "Quail"), "android studio")


class RouterMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "Quail").mkdir()
        (root / "Quail" / "gradlew").write_text("")
        self.mem = Memory(":memory:")
        self.addCleanup(self.mem.close)
        apps = AppIndex({"brave": App("Brave", "brave.exe")})
        self.brain = FakeBrain(reply="ok")
        self.router = Router(Toolbox(apps, self.mem, Projects(self.mem, roots=[root])), self.brain)
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_remember_keeps_capitals_and_recalls(self):
        res = run(self.router, "Adeola, remember that I'm working on Quail this week")
        self.assertEqual(res.source, "fast")
        self.assertIn("you're working on Quail this week", res.reply)
        self.assertEqual(self.mem.facts()[0].text, "I'm working on Quail this week")
        self.assertIn("Quail", run(self.router, "what am I working on?").reply)
        self.assertIn("Quail", run(self.router, "what do you remember").reply)
        self.assertIn("Quail", run(self.router, "do you remember what I'm working on").reply)
        self.assertEqual(self.brain.seen, [])        # all handled without the model

    def test_forget_one_and_all(self):
        run(self.router, "remember my exam is on Friday")
        run(self.router, "remember I like jollof")
        self.assertIn("exam", run(self.router, "forget my exam").reply)
        self.assertEqual([f.text for f in self.mem.facts()], ["I like jollof"])
        self.assertIn("confirm", run(self.router, "forget everything").reply)
        self.assertEqual(len(self.mem.facts()), 1)  # nothing deleted yet
        self.assertIn("forgotten all 1", run(self.router, "yes, forget everything").reply)
        self.assertEqual(self.mem.facts(), [])
        self.assertIn("first", run(self.router, "yes forget everything").reply)

    def test_forget_that(self):
        run(self.router, "remember the wifi password is on the fridge")
        self.assertIn("fridge", run(self.router, "forget that").reply)
        self.assertEqual(self.mem.facts(), [])

    def test_projects(self):
        res = run(self.router, "open my quail project")
        self.assertEqual(res.calls, [("open_project", {"name": "quail"})])
        self.assertIn("Quail project", res.reply)
        self.assertIn("Quail", run(self.router, "list my projects").reply)
        other = self.root / "x" / "Imani"
        other.mkdir(parents=True)
        self.assertIn("Added your Imani project",
                      run(self.router, f"add project Imani at {other}").reply)
        self.assertIn("Imani", run(self.router, "open project imani").reply)

    def test_recent_activity_logged(self):
        os.environ.pop("ADEOLA_DRY_RUN")
        from app import config
        config.DRY_RUN = False
        try:
            self.mem.log("app", "Brave")
            self.mem.log("project", "Quail")
            reply = run(self.router, "what was I doing earlier").reply
        finally:
            config.DRY_RUN = True
            os.environ["ADEOLA_DRY_RUN"] = "1"
        self.assertIn("opened your Quail project", reply)
        self.assertLess(reply.index("Quail"), reply.index("Brave"))   # newest first

    def test_model_sees_relevant_facts(self):
        run(self.router, "remember my exam is on Friday")
        run(self.router, "remember I like jollof")
        run(self.router, "should I study tonight for my exam or not")
        text, facts = self.brain.seen[-1]
        self.assertEqual(facts, ["My exam is on Friday"])
        self.assertTrue(text.startswith("should I study"))

    def test_memory_off(self):
        router = Router(Toolbox(AppIndex({})), FakeBrain())
        self.assertIn("switched off", run(router, "remember my exam is on Friday").reply)


if __name__ == "__main__":
    unittest.main()
