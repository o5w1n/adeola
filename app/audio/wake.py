"""Wake word: say "Adeola, open Brave" without touching the keyboard.

openWakeWord listens to every 80 ms of mic audio. When it hears the wake word,
we keep recording until you stop talking, then hand the audio to the same
speech-to-text and command handling as push-to-talk.
"""

from __future__ import annotations

import collections
import queue
import threading
import time
from pathlib import Path

import numpy as np

from app import config
from app.audio.push_to_talk import beep

BLOCK_SECONDS = 0.08
SELF_TRIGGER_GUARD = 0.6  # seconds after Adeola stops talking before we listen again


def wake_model_ref() -> tuple[str, str]:
    """(model path or built-in name, phrase to tell the user)."""
    custom = config.MODELS_DIR / config.WAKE_MODEL
    if custom.exists():
        return str(custom), config.ASSISTANT_NAME
    return config.WAKE_FALLBACK, config.WAKE_FALLBACK.replace("_", " ")


class OpenWakeWordDetector:
    """Wraps openWakeWord: score(block) -> 0..1 for one 80 ms block."""

    def __init__(self, model_ref: str):
        from openwakeword.model import Model
        from openwakeword.utils import download_models
        # download_models always fetches the small shared feature models (once).
        # For a built-in wake word it also fetches that model; for your own file,
        # a name that matches nothing means "feature models only".
        if Path(model_ref).suffix in (".onnx", ".tflite"):
            download_models(["feature-models-only"])
        else:
            download_models([model_ref])
        self.model = Model(wakeword_models=[model_ref], inference_framework="onnx")

    def score(self, block: np.ndarray) -> float:
        pcm = (np.clip(block, -1, 1) * 32767).astype(np.int16)
        return max(self.model.predict(pcm).values(), default=0.0)

    def reset(self) -> None:
        self.model.reset()


class WakeWord:
    IDLE, CAPTURE, FINISH, BUSY = "idle", "capture", "finish", "busy"

    def __init__(self, handle_audio, detector, speaker=None, is_busy=lambda: False,
                 say=print, verbose: bool = False):
        self.handle_audio = handle_audio  # same as push-to-talk: transcribe, act, reply
        self.detector = detector
        self.speaker = speaker
        self.is_busy = is_busy            # e.g. push-to-talk key is being held
        self.say = say
        self.verbose = verbose
        self.state = self.IDLE
        self.noise_floor = 0.005
        self._preroll: collections.deque = collections.deque(
            maxlen=max(1, round(config.WAKE_PREROLL_SECONDS / BLOCK_SECONDS))
        )
        self._finish_elapsed = 0.0
        self._awaiting_followup: bool = False
        self._blocks: queue.Queue = queue.Queue(maxsize=200)
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # Called from the audio callback: just queue the block, do the work elsewhere.
    def on_block(self, block: np.ndarray) -> None:
        try:
            self._blocks.put_nowait(block)
        except queue.Full:
            pass

    def _run(self) -> None:
        while True:
            self.feed(self._blocks.get())

    # -- state machine (public so tests can drive it directly) ---------------------
    def feed(self, block: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(block ** 2))) if block.size else 0.0
        if self.state == self.IDLE:
            self._idle(block, rms)
        elif self.state == self.CAPTURE:
            self._capture(block, rms)
        elif self.state == self.FINISH:
            self._finish(block, rms)
        # BUSY: a command is being handled; ignore the mic until it's done

    def _adeola_talking(self) -> bool:
        s = self.speaker
        return bool(s and (s.speaking or time.monotonic() - s.last_spoke < SELF_TRIGGER_GUARD))

    def _idle(self, block: np.ndarray, rms: float) -> None:
        if rms < 0.05:  # learn the room's background level from quiet moments
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms
        if self.speaker and not self.speaker.speaking and (
            time.monotonic() - self.speaker.last_spoke < SELF_TRIGGER_GUARD
        ):
            return
        score = self.detector.score(block)
        speaking = bool(self.speaker and self.speaker.speaking)
        threshold = config.BARGE_IN_THRESHOLD if speaking else config.WAKE_THRESHOLD
        if score < threshold or self.is_busy():
            if not speaking:
                self._preroll.append(block)
            return
        if self.verbose:
            self.say(f"   [wake word {score:.2f}]")
        if self.speaker:
            self.speaker.stop()
        self._start_capture(include_preroll=not speaking)

    def _start_capture(self, include_preroll: bool = True) -> None:
        self.state = self.CAPTURE
        self._audio: list[np.ndarray] = list(self._preroll) if include_preroll else []
        self._preroll.clear()
        self._heard_speech = False
        self._quiet = 0.0
        self._elapsed = 0.0
        self._finish_elapsed = 0.0
        beep(880)
        self.say("🎙  Listening...")

    def _capture(self, block: np.ndarray, rms: float) -> None:
        self._audio.append(block)
        self._elapsed += BLOCK_SECONDS
        speech_level = max(self.noise_floor * 3, 0.01)
        if rms > speech_level:
            self._heard_speech = True
            self._quiet = 0.0
        else:
            self._quiet += BLOCK_SECONDS

        if not self._heard_speech and self._elapsed >= config.NO_SPEECH_TIMEOUT:
            beep(440)
            self.say("   (didn't hear a command)")
            self._awaiting_followup = False
            self._back_to_idle()
            return
        if self._elapsed >= config.MAX_COMMAND_SECONDS:
            self._dispatch()
            return
        if self._heard_speech and self._quiet >= config.END_SILENCE_SECONDS:
            if config.FINISH_SECONDS > 0:
                self.state = self.FINISH
                self._finish_elapsed = 0.0
            else:
                self._dispatch()

    def _finish(self, block: np.ndarray, rms: float) -> None:
        self._audio.append(block)
        self._elapsed += BLOCK_SECONDS
        self._finish_elapsed += BLOCK_SECONDS
        speech_level = max(self.noise_floor * 3, 0.01)
        if rms > speech_level:
            self.state = self.CAPTURE
            self._quiet = 0.0
            return
        if self._finish_elapsed >= config.FINISH_SECONDS or self._elapsed >= config.MAX_COMMAND_SECONDS:
            self._dispatch()

    def _dispatch(self) -> None:
        beep(440)
        audio = np.concatenate(self._audio)
        self.state = self.BUSY
        threading.Thread(target=self._handle, args=(audio,), daemon=True).start()

    def _handle(self, audio: np.ndarray) -> None:
        reply = None
        try:
            reply = self.handle_audio(audio)
        finally:
            if reply == "Yes?" and not self._awaiting_followup:
                deadline = time.monotonic() + 3.0
                while self.speaker and self.speaker.speaking and time.monotonic() < deadline:
                    time.sleep(0.02)
                self._awaiting_followup = True
                self.detector.reset()
                self._preroll.clear()
                while not self._blocks.empty():
                    try:
                        self._blocks.get_nowait()
                    except queue.Empty:
                        break
                self._start_capture(include_preroll=False)
            else:
                self._awaiting_followup = False
                self._back_to_idle()

    def _back_to_idle(self) -> None:
        self.detector.reset()
        self._preroll.clear()
        while not self._blocks.empty():   # drop audio that arrived while we were busy
            try:
                self._blocks.get_nowait()
            except queue.Empty:
                break
        self.state = self.IDLE
