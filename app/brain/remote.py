"""Provider-agnostic remote/cloud brain matching Brain's interface.

Supports OpenAI-compatible endpoints (Google Gemini, Groq, OpenRouter) and
Anthropic Messages API using Python's stdlib urllib so zero extra SDKs are
required.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from app import config
from app.brain.model import BASE_PROMPT, _tool_lines, format_observations

NOTE_TOOLS = {"search_notes"}
MEMORY_TOOLS = {"recall", "recent_activity", "forget"}   # forget's reply echoes the stored fact
PROJECT_TOOLS = {"list_projects", "open_project"}        # replies can list local project folder names


def cloud_blocked_tools() -> set[str]:
    """Tools whose outputs stay local unless the user opts into cloud sharing."""
    blocked: set[str] = set()
    if not config.CLOUD_SHARES_NOTES:
        blocked |= NOTE_TOOLS
    if not config.CLOUD_SHARES_MEMORY:
        blocked |= MEMORY_TOOLS | PROJECT_TOOLS
    return blocked


class ProviderHTTPError(RuntimeError):
    """An HTTP error from a cloud provider, keeping the full response body for inspection."""

    def __init__(self, url: str, status: int, body: str):
        super().__init__(f"HTTP {status} from {url}: {body[:400]}")
        self.status = status
        self.body = body


def _post_json(url: str, headers: dict[str, str], payload: dict, timeout: float = 30.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    merged_headers = {"User-Agent": "adeola/1.0", **headers}
    req = urllib.request.Request(url, data=body, headers=merged_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise ProviderHTTPError(url, e.code, detail or str(e.reason)) from e


def _rejected_prose(err: ProviderHTTPError) -> str | None:
    """Groq's json_object mode returns 400 json_validate_failed when the model answers in plain
    prose. That prose is a usable chat reply; return it, or None for any other error."""
    if err.status != 400:
        return None
    try:
        info = json.loads(err.body).get("error") or {}
    except (json.JSONDecodeError, AttributeError):
        return None
    if info.get("code") != "json_validate_failed":
        return None
    text = str(info.get("failed_generation") or "").strip()
    return text if text and not text.startswith("{") else None


class RemoteBrain:
    """Drop-in cloud counterpart to `app.brain.model.Brain`."""

    def __init__(
        self,
        tool_schemas: list[dict],
        provider_name: str = config.DEFAULT_CLOUD_BRAIN,
        api_key: str | None = None,
        requester=None,
    ):
        key_name = provider_name.strip().lower()
        spec = config.CLOUD_BRAINS.get(key_name)
        if spec is None:
            raise ValueError(f"Unknown cloud brain '{provider_name}'")
        self.name = key_name
        self.provider = spec.get("provider", "openai_compat")
        self.model = spec["model"]
        self.base_url = spec["base_url"].rstrip("/")
        self.api_key_env = spec["api_key_env"]
        self.api_key = (api_key if api_key is not None else os.getenv(self.api_key_env, "")).strip()
        if requester is None and (not self.api_key or self.api_key == "PUT-YOUR-KEY-HERE"):
            raise ValueError(f"Missing API key: set {self.api_key_env} in your environment or .env")
        self.tools = tool_schemas
        self.mode = "json"
        self.history: list[dict] = []
        self.last_stats: dict = {}
        self._requester = requester or _post_json
        self._params = {
            s["function"]["name"]: list(s["function"]["parameters"]["properties"])
            for s in tool_schemas
        }
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        base = BASE_PROMPT.replace(
            "assistant running locally on the user's Windows 11 PC",
            "assistant on the user's Windows 11 PC",
        ).replace(
            "You run 100% on this PC (Ollama, Whisper, Kokoro). You cost nothing to run and never send conversation data to the cloud.",
            f"You are currently running on the {self.name} cloud brain ({self.model}) for stronger reasoning, while voice and local tools stay on this PC.",
        ).replace(
            "Only opening websites and web search use the internet.",
            f"Your active {self.name} cloud brain, opening websites, and web search use the internet.",
        )
        return (
            f"{base}\n\nTools:\n{_tool_lines(self.tools)}\n\n"
            "Reply ONLY with valid JSON matching:\n"
            '{"actions": [{"tool": "<tool_name>", "argument": "<arg>"}], "reply": "<spoken reply>"}\n'
            '- If tools fit, list them in "actions" (max 3) and set "reply" to "" '
            "(except offer_to_remember, which goes with a normal reply).\n"
            '- Otherwise set "actions" to [] and answer in "reply" with at most two short sentences.\n'
            'Example (action): {"actions": [{"tool": "open_app", "argument": "Brave"}], "reply": ""}\n'
            'Example (chat): {"actions": [], "reply": "Appreciate it — what are we tackling next?"}'
        )

    def brain_name(self) -> str:
        return self.name

    def warm_up(self) -> None:
        return None

    def switch_model(self, name_or_tag: str) -> tuple[bool, str]:
        key = name_or_tag.strip().lower()
        if key in config.CLOUD_BRAINS:
            spec = config.CLOUD_BRAINS[key]
            env_key = os.getenv(spec["api_key_env"], "").strip()
            if not env_key or env_key == "PUT-YOUR-KEY-HERE":
                return False, f"The {key} cloud brain has no API key set ({spec['api_key_env']})."
            self.name = key
            self.provider = spec.get("provider", "openai_compat")
            self.model = spec["model"]
            self.base_url = spec["base_url"].rstrip("/")
            self.api_key_env = spec["api_key_env"]
            self.api_key = env_key
            self.system_prompt = self._build_system_prompt()
            return True, f"Switched to my {key} cloud brain ({self.model})."
        if key in config.BRAINS:
            return False, f"'{key}' is a local brain; say 'go local' to switch back from {self.name}."
        self.model = name_or_tag.strip()
        self.system_prompt = self._build_system_prompt()
        return True, f"Switched {self.name} model to {self.model}."

    def _call(self, messages: list[dict], max_tokens: int) -> tuple[str, str, int, int, float]:
        t0 = time.perf_counter()
        if self.provider == "anthropic":
            sys_parts = [m["content"] for m in messages if m["role"] == "system"]
            chat_msgs = [m for m in messages if m["role"] != "system"]
            url = f"{self.base_url}/messages"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": 0,
                "system": "\n\n".join(sys_parts),
                "messages": chat_msgs,
            }
            data = self._requester(url, headers, payload)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            blocks = data.get("content") or []
            raw = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            usage = data.get("usage") or {}
            in_tok = int(usage.get("input_tokens") or 0)
            out_tok = int(usage.get("output_tokens") or 0)
            finish = str(data.get("stop_reason") or "")
            return raw, finish, in_tok, out_tok, elapsed_ms

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            data = self._requester(url, headers, payload)
        except ProviderHTTPError as e:
            prose = _rejected_prose(e)
            if prose is None:
                raise
            raw = json.dumps({"actions": [], "reply": prose})
            return raw, "stop", 0, 0, (time.perf_counter() - t0) * 1000
        elapsed_ms = (time.perf_counter() - t0) * 1000
        choice = (data.get("choices") or [{}])[0]
        raw = ((choice.get("message") or {}).get("content") or "").strip()
        finish = str(choice.get("finish_reason") or "")
        usage = data.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens") or 0)
        out_tok = int(usage.get("completion_tokens") or 0)
        return raw, finish, in_tok, out_tok, elapsed_ms

    def _strip_fences(self, raw: str) -> str:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    def _record_stats(self, in_tok: int, out_tok: int, elapsed_ms: float, content_chars: int, retried: bool) -> None:
        self.last_stats = {
            "load_ms": 0.0,
            "prompt_tokens": in_tok,
            "prompt_ms": elapsed_ms,
            "prompt_tok_per_s": in_tok / (elapsed_ms / 1000) if elapsed_ms else 0.0,
            "out_tokens": out_tok,
            "tok_per_s": out_tok / (elapsed_ms / 1000) if elapsed_ms and out_tok else 0.0,
            "thinking_chars": 0,
            "content_chars": content_chars,
            "retried": retried,
        }

    def _parse_json(self, raw: str) -> tuple[list[tuple[str, dict]], str]:
        cleaned = self._strip_fences(raw)
        try:
            data = json.loads(cleaned)
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

    def decide(
        self,
        text: str,
        facts: list[str] | None = None,
        notes: list[tuple[str, str]] | None = None,
        observations: list[tuple[str, dict, str]] | None = None,
    ) -> tuple[list[tuple[str, dict]], str]:
        shared_facts = facts if (facts and config.CLOUD_SHARES_MEMORY) else None
        shared_notes = notes if (notes and config.CLOUD_SHARES_NOTES) else None
        blocked = cloud_blocked_tools()
        shared_obs = [o for o in observations if o[0] not in blocked] if observations else None

        memory = (
            [{"role": "system", "content": "Things the user told you before (use only if relevant):\n" + "\n".join(f"- {f}" for f in shared_facts)}]
            if shared_facts else []
        )
        notes_msg = (
            [{"role": "system", "content": "Excerpts from the user's notes. Answer from them when relevant and name the note; if they don't cover the question, say so plainly:\n" + "\n\n".join(f"### {t}\n{x}" for t, x in shared_notes)}]
            if shared_notes else []
        )
        obs_msg = (
            [{"role": "system", "content": "Tool observations from earlier steps in this turn (use these directly; do not treat them as saved notes):\n" + format_observations(shared_obs)}]
            if shared_obs else []
        )
        messages = [
            {"role": "system", "content": self.system_prompt},
            *self.history[-2 * config.HISTORY_TURNS:],
            *memory,
            *notes_msg,
            *obs_msg,
            {"role": "user", "content": text},
        ]
        raw, finish, in_tok, out_tok, elapsed_ms = self._call(messages, config.RETRY_OUTPUT_TOKENS)
        self._record_stats(in_tok, out_tok, elapsed_ms, len(raw), retried=False)
        if finish != "length":
            calls, reply = self._parse_json(raw)
            if not self.last_stats.get("parse_error"):
                return calls, reply
        raw, _, in_tok, out_tok, elapsed_ms = self._call(messages, config.RETRY_OUTPUT_TOKENS * 2)
        self._record_stats(in_tok, out_tok, elapsed_ms, len(raw), retried=True)
        return self._parse_json(raw)

    def synthesize(self, text: str, observations: list[tuple[str, dict, str]]) -> str:
        blocked = cloud_blocked_tools()
        if any(name in blocked for name, _, _ in observations):
            return ""
        obs_lines = format_observations(observations)
        user_prompt = (
            f"User request: {text}\n\n"
            f"Tool observations:\n{obs_lines}\n\n"
            "Using only the tool observations above, answer the user's request in one or two "
            'short spoken sentences. Set "actions" to [] and put your spoken answer in "reply".'
        )
        messages = [
            {"role": "system", "content": self.system_prompt},
            *self.history[-2 * config.HISTORY_TURNS:],
            {"role": "user", "content": user_prompt},
        ]
        raw, _, in_tok, out_tok, elapsed_ms = self._call(messages, config.RETRY_OUTPUT_TOKENS)
        self._record_stats(in_tok, out_tok, elapsed_ms, len(raw), retried=False)
        _, reply = self._parse_json(raw)
        if self.last_stats.get("parse_error"):
            return ""
        return reply

    def remember(self, user_text: str, reply: str) -> None:
        self.history += [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": reply},
        ]
        self.history = self.history[-2 * config.HISTORY_TURNS:]
