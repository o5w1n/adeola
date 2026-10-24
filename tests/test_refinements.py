"""Fading tips and keep/skip offers. No model, no Windows needed.

Run from the project folder:  python -m unittest -v
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import contextlib  # noqa: E402
import io          # noqa: E402
import unittest    # noqa: E402

from app import config  # noqa: E402
from app.brain.router import Router  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.tools.applications import App, AppIndex  # noqa: E402
from app.tools.projects import Projects  # noqa: E402
from app.tools.registry import Toolbox  # noqa: E402
from app.tools.tips import TIPS  # noqa: E402


def run(router, text):
    with contextlib.redirect_stdout(io.StringIO()):
        return router.handle(text)


class FakeBrain:
    def __init__(self):
        self.calls, self.reply, self.history = [], "", []

    def decide(self, text, facts=None):
        return self.calls, self.reply

    def remember(self, *_):
        pass


def make():
    mem = Memory(":memory:")
    apps = AppIndex({"brave": App("Brave", "brave.exe"), "notepad": App("Notepad", "notepad.exe"),
                     "spotify": App("Spotify", "spotify.exe")})
    brain = FakeBrain()
    return Router(Toolbox(apps, mem, Projects(mem, roots=[])), brain), mem, brain


class TipTests(unittest.TestCase):
    def setUp(self):
        self.router, self.mem, _ = make()
        self.addCleanup(self.mem.close)

    def replies(self, n, cmd="open brave"):
        return [run(self.router, cmd).reply for _ in range(n)]

    def test_spaced_then_retired(self):
        replies = self.replies(40)
        tipped = [i + 1 for i, r in enumerate(replies) if "Tip:" in r]
        self.assertEqual(tipped[:4], [1, 3, 6, 10])                  # gaps grow
        n_tips = len(TIPS["brave"]) * config.TIP_REPEATS
        self.assertEqual(len(tipped), n_tips)                         # each taught twice, then done
        self.assertTrue(replies[0].startswith("Opening Brave. Tip: "))
        self.assertTrue(all(r == "Opening Brave." for i, r in enumerate(replies) if i + 1 not in tipped))

    def test_rotates_through_tips(self):
        tips = [r.split("Tip: ")[1] for r in self.replies(40) if "Tip:" in r]
        self.assertEqual(tips[:2], [TIPS["brave"][0]] * 2)
        self.assertEqual(tips[2], TIPS["brave"][1])

    def test_apps_without_tips(self):
        self.assertEqual(run(self.router, "open spotify").reply, "Opening Spotify.")

    def test_turn_off_and_on(self):
        self.assertEqual(run(self.router, "no more tips").reply, "Okay, no more tips.")
        self.assertNotIn("Tip:", "".join(self.replies(12)))
        self.assertEqual(run(self.router, "turn tips on").reply, "Tips are back on.")
        self.assertIn("Tip:", "".join(self.replies(10, "open notepad")))

    def test_tips_survive_restart(self):
        self.replies(3)
        again = Router(Toolbox(AppIndex({"brave": App("Brave", "brave.exe")}), self.mem), FakeBrain())
        self.assertNotIn("Tip:", run(again, "open brave").reply)    # 4th open overall: no tip

    def test_off_without_memory(self):
        r = Router(Toolbox(AppIndex({"brave": App("Brave", "brave.exe")})), FakeBrain())
        self.assertEqual(run(r, "open brave").reply, "Opening Brave.")


class OfferTests(unittest.TestCase):
    def setUp(self):
        self.router, self.mem, self.brain = make()
        self.addCleanup(self.mem.close)
        self.brain.reply = "Take a few slow breaths and arrive early."
        self.brain.calls = [("offer_to_remember", {"note": "My dentist appointment is on Monday"})]

    def ask(self):
        return run(self.router, "I have a dentist appointment on Monday, any tips for nerves?")

    def test_answer_plus_offer_then_keep(self):
        res = self.ask()
        self.assertTrue(res.reply.startswith("Take a few slow breaths"))
        self.assertIn("Want me to remember that your dentist appointment is on Monday?", res.reply)
        self.assertEqual(self.mem.facts(), [])                      # nothing stored yet
        self.assertIn("I'll remember", run(self.router, "keep").reply)
        self.assertEqual(self.mem.facts()[0].text, "My dentist appointment is on Monday")

    def test_skip_is_remembered_for_the_session(self):
        self.ask()
        self.assertEqual(run(self.router, "skip").reply, "Okay, I won't.")
        self.assertEqual(self.mem.facts(), [])
        self.assertNotIn("Want me to remember", self.ask().reply)    # doesn't ask again

    def test_offer_expires_after_next_command(self):
        self.ask()
        self.brain.calls, self.brain.reply = [], "Sure."
        run(self.router, "open brave")
        self.assertIsNone(self.router.toolbox.pending_offer)
        run(self.router, "yes")                                       # no longer means keep
        self.assertEqual(self.mem.facts(), [])

    def test_no_offer_if_already_known(self):
        self.mem.add_fact("My dentist appointment is on Monday")
        self.assertNotIn("Want me to remember", self.ask().reply)

    def test_offer_mixed_with_real_action(self):
        self.brain.calls = [("open_app", {"name": "spotify"}),
                            ("offer_to_remember", {"note": "I like lo-fi while coding"})]
        res = run(self.router, "play something, I like lo-fi while coding")
        self.assertEqual(res.reply, "Opening Spotify.")                # the action wins, no stray offer
        self.assertEqual(res.calls, [("open_app", {"name": "spotify"})])

    def test_disabled(self):
        config.OFFER_TO_REMEMBER = False
        try:
            self.assertEqual(self.ask().reply, "Take a few slow breaths and arrive early.")
        finally:
            config.OFFER_TO_REMEMBER = True


if __name__ == "__main__":
    unittest.main()
