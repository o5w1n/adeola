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
import threading
import time

import ollama

from app import config

_THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL)

# Adeola's character & laws (kept under 300 words so the system prompt prefix stays small and fast).
BASE_PROMPT = f"""You are {config.ASSISTANT_NAME}, a sharp, warm, practical assistant running locally on the user's Windows 11 PC.
Speak plainly and concisely — one or two short sentences that sound natural out loud. Skip filler like "Certainly!", "Of course!", or "As an AI".

Hard rules:
- Receipts law: Never say you opened, changed, or saved anything unless a tool actually ran in this turn. Never invent tools.
- Conversation vs. tools: Only call a tool when the user's current message asks to perform an action. For casual chat, slang, reactions ("aight, go off", "nice", "thanks"), or questions, set "actions" to [] and reply naturally and conversationally.
- Local & free: You run 100% on this PC (Ollama, Whisper, Kokoro). You cost nothing to run and never send conversation data to the cloud.
- Internet honesty: Only opening websites and web search use the internet.
- Memory: If the user tells you a personal fact or preference they want kept, use remember.

When the user wants to do one of these jobs, open the preferred app:
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
        self._last_call: float = time.monotonic()
        self.last_stats: dict = {}
        self._params = {s["function"]["name"]: list(s["function"]["parameters"]["properties"])
                        for s in tool_schemas}
        self._schema = _decision_schema(tool_schemas)
        self.system_prompt = self._build_system_prompt(model)

    def _build_system_prompt(self, model: str) -> str:
        if self.mode == "json":
            prompt = (f"{BASE_PROMPT}\n\nTools:\n{_tool_lines(self.tools)}\n\n"
                      "Reply only with JSON.\n"
                      '- If tools fit, list them in "actions" and set "reply" to "" '
                      '(except offer_to_remember, which goes with a normal reply).\n'
                      '- Otherwise set "actions" to [] and answer in "reply" with at most '
                      "two short sentences.\n"
                      'Example (action): {"actions": [{"tool": "open_app", "argument": "Brave"}], "reply": ""}\n'
                      'Example (chat): {"actions": [], "reply": "Appreciate it — what are we tackling next?"}')
        else:
            prompt = (f"{BASE_PROMPT}\nIf the request matches one of your tools, call it. "
                      "Otherwise answer in one or two short sentences of plain text.")
        # Qwen3 obeys this soft switch too, a backup in case `think=False` is ignored.
        return prompt + (" /no_think" if model.startswith("qwen3") else "")

    def brain_name(self) -> str:
        for name, tag in config.BRAINS.items():
            if tag == self.model:
                return name
        return self.model

    def switch_model(self, name_or_tag: str) -> tuple[bool, str]:
        key = name_or_tag.strip().lower()
        target = config.BRAINS.get(key, name_or_tag.strip())
        label = key if key in config.BRAINS else target
        if target == self.model:
            return True, f"Already on my {label} brain ({self.model})."
        prev = (self.model, self.system_prompt, self._think_supported)
        self.model = target
        self.system_prompt = self._build_system_prompt(target)
        self._think_supported = True
        try:
            self.warm_up()
        except Exception as e:
            self.model, self.system_prompt, self._think_supported = prev
            return False, f"Couldn't load {target} ({e}). Run: ollama pull {target}"
        return True, f"Switched to my {label} brain ({self.model})."

    def _options(self, **extra) -> dict:
        return {"temperature": 0, "num_ctx": config.NUM_CTX, **extra}

    def warm_up(self) -> None:
        """Load the model into GPU memory with the same settings real calls use."""
        self._chat([{"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": "hi"}], num_predict=1)

    def ping_if_idle(self, idle_seconds: float) -> bool:
        """Ping Ollama to keep the model warm if no call has happened in `idle_seconds`."""
        if time.monotonic() - self._last_call < idle_seconds:
            return False
        try:
            self.warm_up()
        except Exception:
            pass
        self._last_call = time.monotonic()
        return True

    def start_keep_warm(self, idle_seconds: float = config.KEEP_WARM_SECONDS) -> threading.Thread | None:
        """Start a background daemon that pings Ollama when idle, or return None if disabled."""
        if idle_seconds <= 0:
            return None

        def _loop() -> None:
            step = max(1.0, min(idle_seconds / 4, 30.0))
            while True:
                time.sleep(step)
                self.ping_if_idle(idle_seconds)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        return t

    def _chat(self, messages: list[dict], num_predict: int = config.MAX_OUTPUT_TOKENS):
        self._last_call = time.monotonic()
        kwargs = dict(model=self.model, messages=messages, keep_alive=config.KEEP_ALIVE,
                      options=self._options(num_predict=num_predict))
        if self.mode == "json":
            kwargs["format"] = self._schema
        else:
            kwargs["tools"] = self.tools
        if self._think_supported:
            try:
                resp = self.client.chat(**kwargs, think=False)
                self._last_call = time.monotonic()
                return resp
            except ollama.ResponseError as e:
                if "think" not in str(e).lower():
                    raise
                self._think_supported = False  # e.g. qwen2.5 has no thinking mode
        resp = self.client.chat(**kwargs)
        self._last_call = time.monotonic()
        return resp

    def _unpack(self, resp, retried: bool) -> str:
        msg = resp.message
        raw = msg.content or ""
        thinking = len(getattr(msg, "thinking", None) or "") + sum(map(len, _THINK_TAGS.findall(raw)))
        raw = _THINK_TAGS.sub("", raw).strip()
        self.last_stats = {**_stats(resp, thinking, len(raw)), "retried": retried}
        return raw

    def decide(self, text: str, facts: list[str] | None = None,
               notes: list[tuple[str, str]] | None = None) -> tuple[list[tuple[str, dict]], str]:
        """Return (tool calls, plain-text reply). One of them is usually empty.
        `facts` are relevant things the user asked to be remembered."""
        memory = ([{"role": "system", "content": "Things the user told you before (use only if "
                    "relevant):\n" + "\n".join(f"- {f}" for f in facts)}] if facts else [])
        notes_msg = ([{"role": "system", "content": "Excerpts from the user's notes. Answer from "
                       "them when relevant and name the note; if they don't cover the question, "
                       "say so plainly:\n" + "\n\n".join(f"### {t}\n{x}" for t, x in notes)}]
                     if notes else [])
        messages = [{"role": "system", "content": self.system_prompt},
                    *self.history[-2 * config.HISTORY_TURNS:],
                    *memory,
                    *notes_msg,
                    {"role": "user", "content": text}]
        resp = self._chat(messages)
        raw = self._unpack(resp, retried=False)

        if self.mode == "tools":
            calls = [(tc.function.name, dict(tc.function.arguments or {}))
                     for tc in (resp.message.tool_calls or [])]
            return calls, raw
        if getattr(resp, "done_reason", "") != "length":
            calls, reply = self._parse_json(raw)
            if not self.last_stats.get("parse_error"):
                return calls, reply
        resp = self._chat(messages, config.RETRY_OUTPUT_TOKENS)
        raw = self._unpack(resp, retried=True)
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
