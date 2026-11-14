"""Bounded multi-step Tier 3 agent harness for Adeola.

Loops over plan -> allow-listed tool execution -> observation -> synthesize
with a hard step cap and live EventBus progress events.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.events import BUS
from app.tools.registry import SCHEMAS, Toolbox

_ALLOWED_TOOLS = {s["function"]["name"] for s in SCHEMAS}


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
        steps = 0

        for step in range(1, self.max_steps + 1):
            steps = step
            step_notes = list(notes or [])
            if observations:
                obs_summary = "\n".join(f"- {n}({a}): {out}" for n, a, out in observations)
                step_notes.append(("Tool observations so far", obs_summary))

            prompt = (
                text
                if step == 1
                else f"{text}\n\n(Step {step}/{self.max_steps}: use the observations above; call another tool only if needed, otherwise give your final spoken reply with actions=[].)"
            )
            BUS.emit("harness", state="thinking", step=step, max_steps=self.max_steps)
            calls, reply = brain.decide(prompt, facts=facts, notes=step_notes or None)

            real_calls = [
                (name, args)
                for name, args in (calls or [])
                if name in _ALLOWED_TOOLS
            ]
            if not real_calls:
                final_reply = reply.strip()
                if not final_reply and observations:
                    final_reply = self._synthesize(brain, text, observations)
                return HarnessResult(
                    reply=final_reply or "Done.",
                    calls=all_calls,
                    observations=observations,
                    steps=steps,
                    confidence=0.9 if final_reply else 0.5,
                )

            for name, args in real_calls:
                BUS.emit("harness", state="tool", step=step, tool=name)
                out = self.toolbox.run(name, args)
                all_calls.append((name, args))
                observations.append((name, args, out))

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
