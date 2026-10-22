"""Talks to the local model in Ollama. Only used when the fast path can't parse a command.

Two decision modes (config.DECISION_MODE):
  json  - the model must reply with a small fixed JSON object. Ollama enforces the
          shape while generating, so there is no room for preamble or reasoning.
          Typical output: ~20-40 tokens.
  tools - Ollama's native tool calling. Kept for comparison.
"""

from __future__ import annotations

import json
import re

import ollama

from app import config

_THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL)

BASE_PROMPT = f"""You are {config.ASSISTANT_NAME}, a personal assistant running on the user's Windows 11 PC.
Never say you did something unless a tool did it. Never invent tools.
If the user tells you something about themselves and wants it kept, use remember.
When the user wants to do one of these things, open the app named:
""" + "\n".join(f"- {job}: {app}" for job, app in config.PREFERRED_APPS.items())


def _tool_lines(schemas: list[dict]) -> str:
    lines = []
    for s in schemas:
        f = s["function"]
        params = list(f["parameters"]["properties"])
        arg = f"argument = {params[0]}" if params else "no argument"
        lines.append(f"- {f['name']} ({arg}): {f['description']}")
    return "\n".join(lines)


def _decision_schema(schemas: list[dict]) -> dict:
    names = [s["function"]["name"] for s in schemas]
    return {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "enum": names},
                        "argument": {"type": "string"},
                    },
                    "required": ["tool", "argument"],
                },
            },
            "reply": {"type": "string"},
        },
        "required": ["actions", "reply"],
    }


class Brain:
    def __init__(self, tool_schemas: list[dict], model: str = config.MODEL,
                 client=None, mode: str = config.DECISION_MODE):
        self.model = model
        self.tools = tool_schemas
        self.mode = mode
        self.client = client or ollama.Client()
        self.history: list[dict] = []
        self._think_supported = True
        self.last_stats: dict = {}
        self._params = {s["function"]["name"]: list(s["function"]["parameters"]["properties"])
                        for s in tool_schemas}
        self._schema = _decision_schema(tool_schemas)

        if mode == "json":
            prompt = (f"{BASE_PROMPT}\n\nTools:\n{_tool_lines(tool_schemas)}\n\n"
                      "Reply only with JSON.\n"
                      '- If tools fit, list them in "actions" and set "reply" to "".\n'
                      '- Otherwise set "actions" to [] and answer in "reply" with at most '
                      "two short sentences.\n"
                      'Example: {"actions": [{"tool": "open_app", "argument": "Brave"}], "reply": ""}')
        else:
            prompt = (f"{BASE_PROMPT}\nIf the request matches one of your tools, call it. "
                      "Otherwise answer in one or two short sentences of plain text.")
        # Qwen3 obeys this soft switch too, a backup in case `think=False` is ignored.
        self.system_prompt = prompt + (" /no_think" if model.startswith("qwen3") else "")

    def _options(self, **extra) -> dict:
        return {"temperature": 0, "num_ctx": config.NUM_CTX, **extra}

    def warm_up(self) -> None:
        """Load the model into GPU memory with the same settings real calls use."""
        self.client.chat(model=self.model, messages=[{"role": "user", "content": "hi"}],
                         keep_alive=config.KEEP_ALIVE, options=self._options(num_predict=1))

    def _chat(self, messages: list[dict]):
        kwargs = dict(model=self.model, messages=messages, keep_alive=config.KEEP_ALIVE,
                      options=self._options(num_predict=config.MAX_OUTPUT_TOKENS))
        if self.mode == "json":
            kwargs["format"] = self._schema
        else:
            kwargs["tools"] = self.tools
        if self._think_supported:
            try:
                return self.client.chat(**kwargs, think=False)
            except ollama.ResponseError as e:
                if "think" not in str(e).lower():
                    raise
                self._think_supported = False  # e.g. qwen2.5 has no thinking mode
        return self.client.chat(**kwargs)

    def decide(self, text: str, facts: list[str] | None = None) -> tuple[list[tuple[str, dict]], str]:
        """Return (tool calls, plain-text reply). One of them is usually empty.
        `facts` are relevant things the user asked to be remembered."""
        memory = ([{"role": "system", "content": "Things the user told you before (use only if "
                    "relevant):\n" + "\n".join(f"- {f}" for f in facts)}] if facts else [])
        messages = [{"role": "system", "content": self.system_prompt},
                    *self.history[-2 * config.HISTORY_TURNS:],
                    *memory,
                    {"role": "user", "content": text}]
        resp = self._chat(messages)
        msg = resp.message
        raw = msg.content or ""
        thinking = len(getattr(msg, "thinking", None) or "") + sum(map(len, _THINK_TAGS.findall(raw)))
        raw = _THINK_TAGS.sub("", raw).strip()
        self.last_stats = _stats(resp, thinking, len(raw))

        if self.mode == "tools":
            calls = [(tc.function.name, dict(tc.function.arguments or {}))
                     for tc in (msg.tool_calls or [])]
            return calls, raw
        return self._parse_json(raw)

    def _parse_json(self, raw: str) -> tuple[list[tuple[str, dict]], str]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.last_stats["parse_error"] = True
            return [], "Sorry, I lost my train of thought. Could you say that again?"
        calls = []
        for action in data.get("actions") or []:
            name = action.get("tool", "")
            params = self._params.get(name, [])
            arg = str(action.get("argument", "")).strip()
            calls.append((name, {params[0]: arg} if params else {}))
        return calls, str(data.get("reply", "")).strip()

    def remember(self, user_text: str, reply: str) -> None:
        self.history += [{"role": "user", "content": user_text},
                         {"role": "assistant", "content": reply}]
        self.history = self.history[-2 * config.HISTORY_TURNS:]


def _stats(resp, thinking_chars: int, content_chars: int) -> dict:
    """Where the time went, from Ollama's own timings (nanoseconds)."""
    ms = lambda v: (v or 0) / 1e6  # noqa: E731
    out_tokens, out_ms = resp.eval_count or 0, ms(resp.eval_duration)
    in_tokens, in_ms = resp.prompt_eval_count or 0, ms(resp.prompt_eval_duration)
    return {
        "load_ms": ms(resp.load_duration),
        "prompt_tokens": in_tokens,
        "prompt_ms": in_ms,
        "prompt_tok_per_s": in_tokens / (in_ms / 1000) if in_ms else 0,
        "out_tokens": out_tokens,
        "tok_per_s": out_tokens / (out_ms / 1000) if out_ms else 0,
        "thinking_chars": thinking_chars,
        "content_chars": content_chars,
    }
