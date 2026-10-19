"""Speech-to-text with faster-whisper, on the CPU so the GPU stays free for the LLM.

The model downloads automatically the first time (base.en is about 145 MB).
"""

from __future__ import annotations

import numpy as np

from app import config

# Phrases Whisper tends to invent from silence or background noise.
_HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "thank you for watching",
                   "bye", "okay", "uh", "um", "hmm"}


def hint_prompt() -> str:
    """Example sentence that teaches Whisper how names we use are spelled."""
    apps = ", ".join(dict.fromkeys(config.PREFERRED_APPS.values()))
    return f"{config.ASSISTANT_NAME}, open {apps}."


def is_noise(text: str) -> bool:
    return text.lower().strip(" .,!?") in _HALLUCINATIONS


class Transcriber:
    def __init__(self, model: str = config.STT_MODEL):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model, device="cpu", compute_type="int8",
                                  cpu_threads=config.STT_THREADS)
        self.prompt = hint_prompt()

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True,
            initial_prompt=self.prompt, without_timestamps=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        return "" if is_noise(text) else text
