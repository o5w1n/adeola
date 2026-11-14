"""Multi-tier evaluation harness for Adeola (local vs. cloud/remote).

Run from the project folder:
    python -m tests.eval_harness                           # local default brain
    python -m tests.eval_harness --brain gemini            # requires GEMINI_API_KEY
    python -m tests.eval_harness --tier 3 --save           # Tier 3 only, save JSON
    python -m tests.eval_harness --brain mock-gemini       # offline smoke check
"""

import os

os.environ["ADEOLA_DRY_RUN"] = "1"

import argparse  # noqa: E402
import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import statistics  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

from app import config  # noqa: E402
from app.brain.model import Brain  # noqa: E402
from app.brain.remote import RemoteBrain  # noqa: E402
from app.brain.router import Router, clean  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.tools.applications import AppIndex  # noqa: E402
from app.tools.projects import Projects  # noqa: E402
from app.tools.registry import SCHEMAS, Toolbox  # noqa: E402
from tests.eval_commands import CASES, check  # noqa: E402

# Tier 3: multi-step, observation+synthesis, or combined intent cases
TIER3_CASES = [
    ("what was I doing earlier", ("recent_activity",), None),
    ("what projects do I have", ("list_projects",), None),
    ("which apps did you open for me today", ("recent_activity",), None),
    ("when is my exam again?", ("recall",), "exam"),
    ("I'm working on Quail this week, keep that in mind", ("remember",), "quail"),
    ("my dentist appointment is on Monday, any tips for staying calm?", ("offer_to_remember",), "dentist"),
]


class _MockRemoteBrain(RemoteBrain):
    """Deterministic offline mock for smoke-testing the eval harness itself."""

    def __init__(self, schemas):
        def _fake(_url, _headers, payload, timeout=30.0):
            user_msg = payload["messages"][-1]["content"].lower()
            if "tool observations:" in user_msg:
                body = {"actions": [], "reply": "Here is what I found from your tools."}
            elif "quail" in user_msg and "keep that in mind" in user_msg:
                body = {"actions": [{"tool": "remember", "argument": "Working on Quail this week"}], "reply": ""}
            elif "exam" in user_msg:
                body = {"actions": [{"tool": "recall", "argument": "exam"}], "reply": ""}
            elif "dentist" in user_msg:
                body = {"actions": [{"tool": "offer_to_remember", "argument": "Dentist appointment on Monday"}], "reply": "Take slow breaths."}
            elif "apps did you open" in user_msg:
                body = {"actions": [{"tool": "recent_activity", "argument": ""}], "reply": ""}
            elif "write some code" in user_msg:
                body = {"actions": [{"tool": "open_app", "argument": "Visual Studio Code"}], "reply": ""}
            elif "browse the internet" in user_msg:
                body = {"actions": [{"tool": "open_app", "argument": "Brave"}], "reply": ""}
            else:
                body = {"actions": [], "reply": "Okay."}
            return {
                "choices": [{"message": {"content": json.dumps(body)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }

        super().__init__(schemas, provider_name="gemini", api_key="mock", requester=_fake)


def _select_cases(tier: str) -> list[tuple[str, tuple[str, tuple[str, ...], str | None]]]:
    if tier == "1":
        return [("T1", c) for c in CASES[:16]]
    if tier == "2":
        return [("T2", c) for c in CASES[16:]]
    if tier == "3":
        return [("T3", c) for c in TIER3_CASES]
    return [("T1/T2", c) for c in CASES] + [("T3", c) for c in TIER3_CASES]


def _make_brain(name: str):
    if name == "mock-gemini":
        return _MockRemoteBrain(SCHEMAS)
    if name in config.CLOUD_BRAINS:
        return RemoteBrain(SCHEMAS, provider_name=name)
    tag = config.BRAINS.get(name, name)
    return Brain(SCHEMAS, model=tag)


def run_eval(brain_name: str, tier: str = "all", llm_only: bool = False, delay: float = 0.0, limit: int = 0) -> dict:
    brain = _make_brain(brain_name)
    brain.warm_up()
    memory = Memory(":memory:")
    memory.add_project("Quail", ".", "android studio")
    memory.add_fact("My exam is on Friday")
    router = Router(Toolbox(AppIndex(), memory, Projects(memory, roots=[])), brain)

    items = _select_cases(tier)
    if limit > 0:
        items = items[:limit]
    rows = []
    for label, (text, tools, needle) in items:
        brain.history.clear()
        brain.last_stats = {}
        t0 = time.perf_counter()
        try:
            if llm_only:
                calls, _ = brain.decide(clean(text))
                source = "llm"
            else:
                with contextlib.redirect_stdout(io.StringIO()):
                    res = router.handle(text)
                calls, source = res.calls, res.source
        except Exception as e:
            calls, source = [], f"error:{e.__class__.__name__}"
        ms = (time.perf_counter() - t0) * 1000
        if delay > 0 and source != "fast":
            time.sleep(delay)
        ok = check(calls, tools, needle)
        stats = dict(getattr(brain, "last_stats", {}) or {})
        rows.append({
            "tier": label,
            "command": text,
            "ok": ok,
            "source": source,
            "ms": round(ms, 1),
            "parse_error": bool(stats.get("parse_error", False)),
            "calls": calls,
        })

    total = len(rows)
    passed = sum(r["ok"] for r in rows)
    latencies = sorted(r["ms"] for r in rows)
    llm_rows = [r for r in rows if r["source"] != "fast"]
    schema_ok = sum(not r["parse_error"] for r in llm_rows)
    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else 0.0
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "brain": brain_name,
        "model": getattr(brain, "model", brain_name),
        "tier": tier,
        "llm_only": llm_only,
        "passed": passed,
        "total": total,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "llm_calls": len(llm_rows),
        "schema_ok": schema_ok,
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "rows": rows,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--brain", default="smart")
    p.add_argument("--tier", choices=["1", "2", "3", "all"], default="all")
    p.add_argument("--llm-only", action="store_true")
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--save", action="store_true")
    p.add_argument("--out", default="")
    args = p.parse_args()

    report = run_eval(args.brain, tier=args.tier, llm_only=args.llm_only, delay=args.delay, limit=args.limit)
    print(f"Eval: {report['brain']} ({report['model']}) | tier={report['tier']} | "
          f"score={report['passed']}/{report['total']} ({report['accuracy']*100:.1f}%) | "
          f"p50={report['p50_ms']}ms p95={report['p95_ms']}ms | "
          f"schema={report['schema_ok']}/{report['llm_calls']}")
    for r in report["rows"]:
        if not r["ok"]:
            print(f"  ✗ [{r['tier']}] {r['source']} {r['ms']}ms {r['command']!r} -> {r['calls']}")

    if args.save or args.out:
        out_path = Path(args.out) if args.out else config.DATA_DIR / "evals" / f"{args.brain}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()

