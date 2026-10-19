"""Talks to the local model in Ollama. Only used when the fast path can't parse a command."""

from __future__ import annotations

import re

import ollama

from app import config

SYSTEM_PROMPT = f"""You are {config.ASSISTANT_NAME}, a personal assistant running on the user's Windows 11 PC.
If the request matches one of your tools, call the tool. For apps, use the name Windows shows in the Start menu (for example "Brave", "Visual Studio Code", "Android Studio").
If no tool fits, answer in one or two short sentences of plain text.
Never say you did something unless a tool did it. Never invent tools."""

_THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL)


class Brain:
    def __init__(self, tool_schemas: list[dict], model: str = config.MODEL, client=None):
        self.model = model
        self.tools = tool_schemas
        self.client = client or ollama.Client()
        self.history: list[dict] = []
        self._think_supported = True
        self.last_stats: dict = {}
        # Qwen3 also obeys this soft switch, a backup in case `think=False` is ignored.
        self.system_prompt = SYSTEM_PROMPT + (" /no_think" if model.startswith("qwen3") else "")

    def _options(self) -> dict:
        return {"temperature": 0, "num_ctx": config.NUM_CTX}

    def warm_up(self) -> None:
        """Load the model into GPU memory now, with the same settings real calls use,
        so Ollama doesn't reload it on the first command."""
        self.client.chat(model=self.model, messages=[{"role": "user", "content": "hi"}],
                         keep_alive=config.KEEP_ALIVE, options={**self._options(), "num_predict": 1})

    def _chat(self, messages: list[dict]):
        kwargs = dict(model=self.model, messages=messages, tools=self.tools,
                      keep_alive=config.KEEP_ALIVE, options=self._options())
        if self._think_supported:
            try:
                return self.client.chat(**kwargs, think=False)
            except ollama.ResponseError as e:
                if "think" not in str(e).lower():
                    raise
                self._think_supported = False  # e.g. qwen2.5 has no thinking mode
        return self.client.chat(**kwargs)

    def decide(self, text: str) -> tuple[list[tuple[str, dict]], str]:
        """Return (tool calls, plain-text reply). One of them is usually empty."""
        messages = [{"role": "system", "content": self.system_prompt},
                    *self.history[-2 * config.HISTORY_TURNS:],
                    {"role": "user", "content": text}]
        resp = self._chat(messages)
        msg = resp.message
        raw = msg.content or ""
        self.last_stats = _stats(resp, len(getattr(msg, "thinking", None) or "")
                                 + sum(len(t) for t in _THINK_TAGS.findall(raw)))
        calls = [(tc.function.name, dict(tc.function.arguments or {}))
                 for tc in (msg.tool_calls or [])]
        return calls, _THINK_TAGS.sub("", raw).strip()

    def remember(self, user_text: str, reply: str) -> None:
        self.history += [{"role": "user", "content": user_text},
                         {"role": "assistant", "content": reply}]
        self.history = self.history[-2 * config.HISTORY_TURNS:]


def _stats(resp, thinking_chars: int) -> dict:
    """Where the time went, from Ollama's own timings (nanoseconds)."""
    ms = lambda v: (v or 0) / 1e6  # noqa: E731
    out_tokens, out_ms = resp.eval_count or 0, ms(resp.eval_duration)
    return {
        "load_ms": ms(resp.load_duration),
        "prompt_tokens": resp.prompt_eval_count or 0,
        "prompt_ms": ms(resp.prompt_eval_duration),
        "out_tokens": out_tokens,
        "tok_per_s": out_tokens / (out_ms / 1000) if out_ms else 0,
        "thinking_chars": thinking_chars,
    }
