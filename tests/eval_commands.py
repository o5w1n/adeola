"""Score a model on sample commands. Nothing is actually opened (dry run).

Run from the project folder:
    python -m tests.eval_commands                      # full router, default model
    python -m tests.eval_commands --model qwen2.5:3b
    python -m tests.eval_commands --llm-only           # skip the fast path, test the model alone
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"  # must be set before app.config is imported

import argparse            # noqa: E402
import contextlib          # noqa: E402
import io                  # noqa: E402
import statistics          # noqa: E402
import time                # noqa: E402

from app import config                     # noqa: E402
from app.brain.model import Brain          # noqa: E402
from app.brain.router import Router, clean  # noqa: E402
from app.tools.applications import AppIndex  # noqa: E402
from app.tools.registry import SCHEMAS, Toolbox  # noqa: E402

# (command, acceptable tool names, text the first argument should contain; None = any)
# A tool set of () means "no tool should be called" (plain chat).
CASES = [
    (f"{config.ASSISTANT_NAME}, open Brave.",     ("open_app",), "brave"),
    ("launch vs code",                            ("open_app",), "code"),
    ("open notepad and calculator",               ("open_app",), "notepad"),
    ("open github.com",                           ("open_url",), "github"),
    ("search for kotlin coroutines tutorial",     ("web_search",), "kotlin"),
    ("what time is it",                           ("get_time",), None),
    ("lock my pc",                                ("lock_pc",), None),
    ("could you bring up the calculator for me",  ("open_app",), "calc"),
    ("I want to write some code",                 ("open_app",), "code"),
    ("I need to browse the internet",             ("open_app",), None),
    ("find me tutorials on jetpack compose",      ("web_search",), "compose"),
    ("take me to youtube",                        ("open_url", "open_app"), "youtube"),
    ("what's today's date",                       ("get_time",), None),
    ("I'm stepping away, secure my computer",     ("lock_pc",), None),
    ("show me what's using my CPU",               ("open_app",), "task"),
    ("I need to change my display settings",      ("open_app",), "settings"),
    ("how do I spell necessary",                  (), None),
    ("tell me a short joke",                      (), None),
    ("who are you",                               (), None),
    ("what's the capital of Ghana",               (), None),
]


def check(calls, tools, needle) -> bool:
    if not tools:
        return not calls
    if not calls or calls[0][0] not in tools:
        return False
    if needle is None:
        return True
    first_arg = next(iter(calls[0][1].values()), "")
    return needle in str(first_arg).lower()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=config.MODEL)
    p.add_argument("--llm-only", action="store_true")
    args = p.parse_args()

    brain = Brain(SCHEMAS, model=args.model)
    print(f"Loading {args.model}...")
    brain.warm_up()
    router = Router(Toolbox(AppIndex()), brain)

    passed, timings, all_stats = 0, [], []
    print(f"\n{'':2} {'source':6} {'ms':>6}  command -> calls")
    for text, tools, needle in CASES:
        brain.history.clear()  # judge each command on its own
        brain.last_stats = {}
        start = time.perf_counter()
        try:
            if args.llm_only:
                calls, _ = brain.decide(clean(text))
                source = "llm"
            else:
                with contextlib.redirect_stdout(io.StringIO()):  # hide dry-run noise
                    res = router.handle(text)
                calls, source = res.calls, res.source
        except Exception as e:  # report and keep going
            calls, source = [], "error"
            print(f"   ! {text!r} raised {e.__class__.__name__}: {e}")
        ms = (time.perf_counter() - start) * 1000
        ok = check(calls, tools, needle)
        passed += ok
        timings.append((source, ms))
        print(f"{'✓' if ok else '✗':2} {source:6} {ms:6.0f}  {text!r} -> {calls}")
        if s := brain.last_stats:
            all_stats.append(s)
            print(f"{'':10} load {s['load_ms']:.0f} ms · prompt {s['prompt_tokens']} tok in "
                  f"{s['prompt_ms']:.0f} ms · output {s['out_tokens']} tok at "
                  f"{s['tok_per_s']:.1f} tok/s · thinking {s['thinking_chars']} chars")

    model_ms = [ms for s, ms in timings if s in ("llm", "chat")]
    fast_ms = [ms for s, ms in timings if s == "fast"]
    print(f"\nScore: {passed}/{len(CASES)}")
    if fast_ms:
        print(f"Fast path: {len(fast_ms)} commands, median {statistics.median(fast_ms):.0f} ms")
    if model_ms:
        print(f"Model path: {len(model_ms)} commands, median {statistics.median(model_ms):.0f} ms")
    if all_stats:
        diagnose(all_stats)


def diagnose(stats: list[dict]) -> None:
    """Plain-English reading of where model time is going."""
    med = lambda k: statistics.median(s[k] for s in stats)  # noqa: E731
    print("\nDiagnosis:")
    problems = 0
    if med("load_ms") > 1000:
        problems += 1
        print(f" - Model reloads on each call ({med('load_ms'):.0f} ms). Check `ollama ps` "
              "shows 100% GPU and nothing else is loaded alongside it.")
    if med("thinking_chars") > 0 or med("out_tokens") > 120:
        problems += 1
        print(f" - Model is writing too much ({med('out_tokens'):.0f} tokens, "
              f"{med('thinking_chars'):.0f} thinking chars). Thinking isn't switched off: "
              "update Ollama (`ollama --version`) and the client (`pip install -U ollama`).")
    if 0 < med("tok_per_s") < 15:
        problems += 1
        print(f" - Generation is slow ({med('tok_per_s'):.1f} tok/s; expect 25+ on the GPU). "
              "The model is probably running on the CPU: check `ollama ps` and `nvidia-smi`.")
    if med("prompt_ms") > 3000:
        problems += 1
        print(f" - Reading the prompt is slow ({med('prompt_ms'):.0f} ms for "
              f"{med('prompt_tokens'):.0f} tokens). Another sign of CPU fallback.")
    if not problems:
        print(" - No obvious problem in Ollama's timings.")


if __name__ == "__main__":
    main()
