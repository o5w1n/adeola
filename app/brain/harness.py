"""Bounded multi-step Tier 3 agent harness for Adeola.

Loops over plan -> allow-listed tool execution -> observation -> synthesize
with a hard step cap and live EventBus progress events.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.brain.remote import RemoteBrain, cloud_blocked_tools
from app.events import BUS
from app.tools.registry import SCHEMAS, Toolbox

_ALLOWED_TOOLS = {s["function"]["name"] for s in SCHEMAS}


def _call_key(name: str, args: dict) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (name, tuple(sorted((k, str(v).strip().lower()) for k, v in (args or {}).items())))


def _fmt_call(name: str, args: dict) -> str:
    inner = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
    return f"{name}({inner})"


@dataclass
class HarnessResult:
    reply: str
    calls: list[tuple[str, dict]] = field(default_factory=list)
    observations: list[tuple[str, dict, str]] = field(default_factory=list)
    steps: int = 1
    confidence: float = 0.85


class AgentHarness:
    """Runs a bounded multi-step tool loop using any Brain/RemoteBrain."""

    def __init__(self, toolbox: Toolbox, max_steps: int = 4):
        self.toolbox = toolbox
        self.max_steps = max(1, max_steps)

    def run(
        self,
        brain,
        text: str,
        facts: list[str] | None = None,
        notes: list[tuple[str, str]] | None = None,
    ) -> HarnessResult:
        all_calls: list[tuple[str, dict]] = []
        observations: list[tuple[str, dict, str]] = []
        seen_calls: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        steps = 0
        is_cloud = isinstance(brain, RemoteBrain)
        blocked = cloud_blocked_tools() if is_cloud else set()

        for step in range(1, self.max_steps + 1):
            steps = step
            if step == 1:
                prompt = text
            else:
                done_list = ", ".join(_fmt_call(n, a) for n, a in all_calls)
                prompt = (
                    f"{text}\n\n"
                    f"(Step {step}/{self.max_steps}: Already done: {done_list}. Don't repeat these. "
                    "Use the tool observations above; call a different tool only if part of the request is "
                    "still unanswered, otherwise give your final spoken reply with actions=[].)"
                )
            BUS.emit("harness", state="thinking", step=step, max_steps=self.max_steps)
            if observations:
                calls, reply = brain.decide(prompt, facts=facts, notes=notes, observations=observations)
            else:
                calls, reply = brain.decide(prompt, facts=facts, notes=notes)

            real_calls = [
                (name, args)
                for name, args in (calls or [])
                if name in _ALLOWED_TOOLS
            ]
            fresh_calls = []
            for name, args in real_calls:
                key = _call_key(name, args)
                if key not in seen_calls:
                    seen_calls.add(key)
                    fresh_calls.append((name, args))
            if not fresh_calls:
                final_reply = reply.strip()
                if not final_reply and observations:
                    final_reply = self._synthesize(brain, text, observations)
                return HarnessResult(
                    reply=final_reply or " ".join(o for _, _, o in observations) or "Done.",
                    calls=all_calls,
                    observations=observations,
                    steps=steps,
                    confidence=0.9 if final_reply else 0.5,
                )

            hit_private = False
            for name, args in fresh_calls:
                BUS.emit("harness", state="tool", step=step, tool=name)
                out = self.toolbox.run(name, args)
                all_calls.append((name, args))
                observations.append((name, args, out))
                hit_private = hit_private or (name in blocked)

            if hit_private:
                return HarnessResult(
                    reply=" ".join(o for _, _, o in observations) or "Done.",
                    calls=all_calls,
                    observations=observations,
                    steps=steps,
                    confidence=0.85,
                )

        final_reply = self._synthesize(brain, text, observations)
        return HarnessResult(
            reply=final_reply or " ".join(o for _, _, o in observations) or "Done.",
            calls=all_calls,
            observations=observations,
            steps=steps,
            confidence=0.85 if final_reply else 0.55,
        )

    @staticmethod
    def _synthesize(brain, text: str, observations: list[tuple[str, dict, str]]) -> str:
        try:
            return (brain.synthesize(text, observations) or "").strip()
        except Exception:
            return ""
