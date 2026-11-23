"""Brain and voice switching for the Router: local shelf, cloud brains, auto routing, and the
"which brain / are you local" answers that depend on which brain is active."""

from __future__ import annotations

import os

from app import config
from app.brain.policy import BrainPolicy
from app.brain.remote import RemoteBrain
from app.events import BUS
from app.tools.registry import SCHEMAS

LOCAL_HONESTY_TEXT = (
    "I run locally on this PC with Ollama, Whisper and Kokoro. I cost nothing to run, "
    "and nothing leaves your machine unless you ask me to open a website or search the web."
)
INTERNET_HONESTY_TEXT = (
    "Only web search, opening websites, and first-run model downloads need the internet. "
    "Everything else — apps, media, projects, memory, notes and voice — runs offline on this PC."
)
_BRAIN_ALIASES = {
    "fast": "quick",
    "coding": "coder",
}


class BrainSwitching:
    """Mixed into Router. Expects the attributes declared below to be set by Router.__init__."""

    brain: object | None
    _local_brain: object | None
    _remote_brains: dict[str, RemoteBrain]
    speaker: object | None
    policy: BrainPolicy
    verbose: bool

    def _which_brain(self) -> str:
        if self.brain is None:
            return "The local model is switched off."
        name = (self.brain.brain_name() if hasattr(self.brain, "brain_name")
                else getattr(self.brain, "model", config.MODEL))
        model = getattr(self.brain, "model", config.MODEL)
        return (f"I'm on my {name} brain ({model}). "
                f"Local: {', '.join(config.BRAINS)}. Cloud: {', '.join(config.CLOUD_BRAINS)}.")

    def _switch_cloud(self, name: str = config.DEFAULT_CLOUD_BRAIN) -> str:
        key = name.strip().lower()
        if key == "cloud":
            key = config.DEFAULT_CLOUD_BRAIN
        info = config.CLOUD_BRAINS.get(key)
        if info is None:
            return f"Unknown cloud brain '{name}'. Available: {', '.join(config.CLOUD_BRAINS)}."
        env_var = info["api_key_env"]
        api_key = os.getenv(env_var, "").strip()
        if not api_key or api_key == "PUT-YOUR-KEY-HERE":
            return f"The cloud brain has no API key set ({env_var}), so I'm staying on my local brain."
        if self.brain is not None and not isinstance(self.brain, RemoteBrain):
            self._local_brain = self.brain
        remote = self._remote_brains.get(key)
        if remote is None or remote.api_key != api_key:
            tools = getattr(self.brain, "tools", None) or SCHEMAS
            remote = RemoteBrain(tools, provider_name=key, api_key=api_key)
            self._remote_brains[key] = remote
        self.brain = remote
        self.policy.pinned = True
        return f"Switched to my {key} cloud brain ({remote.model})."

    def _go_local(self) -> str:
        self.policy.pinned = False
        if self.speaker is not None and hasattr(self.speaker, "switch_voice"):
            self.speaker.switch_voice("default")
        if self._local_brain is not None and isinstance(self.brain, RemoteBrain):
            self.brain = self._local_brain
        if self.brain is None:
            return "The local model is switched off."
        target = config.BRAINS.get("smart", config.MODEL)
        if getattr(self.brain, "model", None) != target:
            if self.speaker is not None and hasattr(self.speaker, "say"):
                self.speaker.say("One moment.")
            ok, msg = self.brain.switch_model("smart")
            if not ok:
                return msg
        return f"All local: smart brain ({getattr(self.brain, 'model', target)})."

    def _enable_auto_brain(self) -> str:
        self.policy.pinned = False
        if not config.AUTO_BRAIN:
            return ("Auto brain is turned off in my settings, because switching models on this GPU "
                    "takes about five seconds each time. Set ADEOLA_AUTO_BRAIN=1 to turn it on.")
        if self.brain is None:
            return "Auto brain routing is on, though the local model is currently switched off."
        name = (self.brain.brain_name() if hasattr(self.brain, "brain_name")
                else getattr(self.brain, "model", config.MODEL))
        return f"Auto brain routing is on (currently on {name})."

    def _warn(self, where: str, message: str) -> None:
        """A background step failed but the turn carries on: tell the HUD, and the terminal under -v."""
        BUS.emit("warning", where=where, message=message)
        if self.verbose:
            print(f"   [warning] {where}: {message}")

    def _maybe_auto_switch(self, text: str, has_notes: bool = False) -> None:
        if self.brain is None or isinstance(self.brain, RemoteBrain) or not hasattr(self.brain, "switch_model"):
            return
        current = (self.brain.brain_name() if hasattr(self.brain, "brain_name")
                   else getattr(self.brain, "model", config.MODEL))
        rec = self.policy.recommend(text, current_brain=current, has_notes=has_notes)
        if not rec:
            return
        try:
            ok, msg = self.brain.switch_model(rec)
        except Exception as e:
            ok, msg = False, f"{e.__class__.__name__}: {e}"
        if not ok:
            self._warn("auto_switch", f"Couldn't switch to the {rec} brain, staying on {current}. {msg}")

    def _switch_brain(self, target: str) -> str:
        key = target.strip().lower()
        key = _BRAIN_ALIASES.get(key, key)
        if key in ("auto", "automatic"):
            return self._enable_auto_brain()
        if key in ("local", "lookout", "offline"):
            return self._go_local()
        if key in config.CLOUD_BRAINS or key == "cloud":
            return self._switch_cloud(config.DEFAULT_CLOUD_BRAIN if key == "cloud" else key)
        if self._local_brain is not None and isinstance(self.brain, RemoteBrain):
            self.brain = self._local_brain
        if self.brain is None:
            return "The local model is switched off."
        if key not in config.BRAINS and ":" not in target:
            from rapidfuzz import fuzz, process
            candidates = list(config.BRAINS) + list(config.CLOUD_BRAINS) + ["local", "auto"]
            match = process.extractOne(key, candidates, scorer=fuzz.ratio, score_cutoff=75)
            if match:
                matched = match[0]
                if matched == "auto":
                    return self._enable_auto_brain()
                if matched == "local":
                    return self._go_local()
                if matched in config.CLOUD_BRAINS:
                    return self._switch_cloud(matched)
                key = matched
                target = matched
            else:
                return f"Unknown brain '{target}'. Available: {', '.join(config.BRAINS)}."
        else:
            target = key if key in config.BRAINS else target.strip()
        resolved = config.BRAINS.get(key, target.strip())
        if self.speaker is not None and hasattr(self.speaker, "say") and getattr(self.brain, "model", None) != resolved:
            self.speaker.say("One moment.")
        ok, msg = self.brain.switch_model(target)
        if ok:
            self.policy.pinned = True
        return msg

    def _which_voice(self) -> str:
        if self.speaker is None:
            return (f"Voice is off right now (default: {config.VOICE_BLEND}). "
                    f"Available: {', '.join(config.VOICES)}.")
        return (f"I'm using my {self.speaker.voice_name()} voice ({self.speaker.blend}). "
                f"Available: {', '.join(config.VOICES)}.")

    def _switch_voice(self, target: str) -> str:
        key = target.strip().lower()
        if self.speaker is None:
            if key not in config.VOICES:
                return f"Unknown voice '{target}'. Available: {', '.join(config.VOICES)}."
            return f"Voice is off right now, but '{key}' is available when you run with --voice or --speak."
        _, msg = self.speaker.switch_voice(target)
        return msg

    def _is_cloud_brain(self) -> bool:
        return isinstance(self.brain, RemoteBrain)

    def _honesty_local(self) -> str:
        if not self._is_cloud_brain():
            return LOCAL_HONESTY_TEXT
        provider = self.brain.brain_name()
        model = getattr(self.brain, "model", "")
        if config.CLOUD_SHARES_MEMORY and config.CLOUD_SHARES_NOTES:
            sharing = "Cloud sharing is enabled for both your saved memory and notes."
        elif config.CLOUD_SHARES_MEMORY:
            sharing = "Cloud sharing is enabled for your saved memory; your notes stay on this PC."
        elif config.CLOUD_SHARES_NOTES:
            sharing = "Cloud sharing is enabled for your notes; your saved memory stays on this PC."
        else:
            sharing = "Only your current question goes to the cloud — your saved memory and notes stay on this PC."
        return (
            f"Right now I'm using the {provider} cloud brain ({model}), so model requests leave this PC "
            f"and may incur API costs. Voice, apps and local tools still run on your machine. "
            f"{sharing} Say 'go local' anytime to switch back to Ollama."
        )

    def _honesty_internet(self) -> str:
        if not self._is_cloud_brain():
            return INTERNET_HONESTY_TEXT
        provider = self.brain.brain_name()
        model = getattr(self.brain, "model", "")
        return (
            f"Right now the {provider} cloud brain ({model}) uses the internet, along with web search, "
            "opening websites, and first-run model downloads. Voice, apps, media, projects, memory and "
            "notes stay on this PC. Say 'go local' to run offline with Ollama."
        )
