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
import sys  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from app import config  # noqa: E402
from app.brain.model import Brain  # noqa: E402
from app.brain.remote import RemoteBrain  # noqa: E402
from app.brain.router import Router, clean  # noqa: E402
from app.memory.store import Memory  # noqa: E402
from app.tools.applications import AppIndex  # noqa: E402
from app.tools.projects import Projects  # noqa: E402
from app.tools.registry import SCHEMAS, Toolbox  # noqa: E402
from tests.eval_commands import CASES, check, is_failure  # noqa: E402

# Tier 3: goals routed through the AgentHarness via the `agent:` / `research:` / `deep:` prefix.
# (goal, tools that must ALL be called in any order). A pass also needs source == "agent"
# and a non-empty final reply.
TIER3_CASES = [
    ("agent: what time is it, and when is my exam?", {"get_time", "recall"}),
    ("agent: list my projects and tell me what I was doing earlier", {"list_projects", "recent_activity"}),
    ("research: what do you remember about my exam, and what's today's date?", {"recall", "get_time"}),
    ("agent: check what I've been doing today and what you remember about Quail",
     {"recent_activity", "recall"}),
    ("deep: what projects do I have?", {"list_projects"}),
    ("agent: what time is it", {"get_time"}),
]
_REPLY_CHARS = 200


class _MockRemoteBrain(RemoteBrain):
    """Deterministic offline mock for smoke-testing the eval harness itself."""

    def __init__(self, schemas):
        def _fake(_url, _headers, payload, timeout=30.0):
            user_msg = payload["messages"][-1]["content"].lower()
            if "step 2/" in user_msg or "step 3/" in user_msg or "step 4/" in user_msg:
                body = {"actions": [], "reply": "Here's what I found."}
            elif "time" in user_msg and "exam" in user_msg:
                body = {"actions": [{"tool": "get_time", "argument": ""},
                                    {"tool": "recall", "argument": "exam"}], "reply": ""}
            elif "tool observations:" in user_msg:
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


def _select_cases(tier: str) -> list[tuple[str, tuple]]:
    t1 = [("T1", c) for c in CASES[:16]]
    t2 = [("T2", c) for c in CASES[16:]]
    t3 = [("T3", c) for c in TIER3_CASES]
    return {"1": t1, "2": t2, "3": t3}.get(tier, t1 + t2 + t3)


def check_agent(res, required: set[str]) -> bool:
    called = {name for name, _ in res.calls}
    return res.source == "agent" and required <= called and bool(res.reply.strip())


def _score(label: str, case: tuple, res) -> bool:
    if is_failure(res.source):
        return False
    if label == "T3":
        return check_agent(res, case[1])
    _, tools, needle = case
    return check(res.calls, tools, needle)


def _summarise(rows: list[dict]) -> dict:
    latencies = sorted(r["ms"] for r in rows)
    model_ms = sorted(r["ms"] for r in rows if r["source"] != "fast" and not is_failure(r["source"]))

    def pct(values: list[float], q: float) -> float:
        return round(values[min(len(values) - 1, int(q * len(values)))], 1) if values else 0.0

    by_tier: dict[str, dict] = {}
    for r in rows:
        t = by_tier.setdefault(r["tier"], {"passed": 0, "total": 0, "failures": 0})
        t["total"] += 1
        t["passed"] += r["ok"]
        t["failures"] += is_failure(r["source"])
    llm_rows = [r for r in rows if r["source"] != "fast"]
    passed = sum(r["ok"] for r in rows)
    return {
        "passed": passed,
        "total": len(rows),
        "accuracy": round(passed / len(rows), 4) if rows else 0.0,
        "failures": sum(is_failure(r["source"]) for r in rows),
        "by_tier": by_tier,
        "llm_calls": len(llm_rows),
        "schema_ok": sum(not r["parse_error"] for r in llm_rows),
        "p50_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "p95_ms": pct(latencies, 0.95),
        "model_p50_ms": round(statistics.median(model_ms), 1) if model_ms else 0.0,
        "model_p95_ms": pct(model_ms, 0.95),
    }


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
    for label, case in items:
        rows.append(_run_case(router, brain, label, case, llm_only and label != "T3"))
        if delay > 0 and rows[-1]["source"] != "fast":
            time.sleep(delay)   # spacing for rate-limited cloud providers
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "brain": brain_name,
        "model": getattr(brain, "model", brain_name),
        "tier": tier,
        "llm_only": llm_only,
        **_summarise(rows),
        "rows": rows,
    }


def _run_case(router: Router, brain, label: str, case: tuple, llm_only: bool) -> dict:
    """Run one case. T3 always goes through the router, since the harness lives there."""
    text = case[0]
    brain.history.clear()
    brain.last_stats = {}
    t0 = time.perf_counter()
    error = ""
    try:
        if llm_only:
            calls, reply = brain.decide(clean(text))
            res = SimpleNamespace(calls=calls, reply=reply, source="llm", steps=1)
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                res = router.handle(text)
    except Exception as e:
        error = f"{e.__class__.__name__}: {e}"
        res = SimpleNamespace(calls=[], reply="", source=f"error:{e.__class__.__name__}", steps=0)
    ms = (time.perf_counter() - t0) * 1000
    if is_failure(res.source) and not error:
        error = res.reply
    stats = dict(getattr(brain, "last_stats", {}) or {})
    return {
        "tier": label,
        "command": text,
        "ok": _score(label, case, res),
        "source": res.source,
        "ms": round(ms, 1),
        "steps": getattr(res, "steps", 1),
        "parse_error": bool(stats.get("parse_error", False)),
        "calls": res.calls,
        "reply": (res.reply or "")[:_REPLY_CHARS],
        "error": error[:_REPLY_CHARS],
    }


def compare(paths: list[Path]) -> str:
    """Markdown table comparing saved reports side by side."""
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    tiers = sorted({t for r in reports for t in r.get("by_tier", {})})
    head = ["brain", "model", "score", *tiers, "failures", "model p50 ms", "model p95 ms", "schema ok", "run"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in reports:
        if "by_tier" not in r:
            lines.append(f"| {r['brain']} | {r['model']} | {r['passed']}/{r['total']} "
                         f"| (old report format: no failure gating, re-run) |")
            continue
        cells = [r["brain"], r["model"], f"{r['passed']}/{r['total']}"]
        for t in tiers:
            s = r["by_tier"].get(t)
            cells.append(f"{s['passed']}/{s['total']}" if s else "-")
        cells += [str(r["failures"]), str(r["model_p50_ms"]), str(r["model_p95_ms"]),
                  f"{r['schema_ok']}/{r['llm_calls']}", r["timestamp"][:16]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--brain", default="smart")
    p.add_argument("--tier", choices=["1", "2", "3", "all"], default="all")
    p.add_argument("--llm-only", action="store_true")
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--save", action="store_true")
    p.add_argument("--out", default="")
    p.add_argument("--compare", nargs="+", metavar="REPORT.json",
                   help="print a markdown table of saved reports and exit")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    if args.compare:
        print(compare([Path(x) for x in args.compare]))
        return

    report = run_eval(args.brain, tier=args.tier, llm_only=args.llm_only, delay=args.delay, limit=args.limit)
    tiers = " ".join(f"{t}={s['passed']}/{s['total']}" for t, s in report["by_tier"].items())
    print(f"Eval: {report['brain']} ({report['model']}) | tier={report['tier']} | "
          f"score={report['passed']}/{report['total']} ({report['accuracy']*100:.1f}%) [{tiers}] | "
          f"failures={report['failures']} | model p50={report['model_p50_ms']}ms "
          f"p95={report['model_p95_ms']}ms | schema={report['schema_ok']}/{report['llm_calls']}")
    for r in report["rows"]:
        if not r["ok"]:
            detail = f" !! {r['error']}" if r["error"] else ""
            print(f"  ✗ [{r['tier']}] {r['source']} {r['ms']}ms {r['command']!r} -> {r['calls']}{detail}")

    if args.save or args.out:
        out_path = Path(args.out) if args.out else config.DATA_DIR / "evals" / f"{args.brain}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()

