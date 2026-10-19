"""Adeola's voice: Kokoro text-to-speech on the CPU, with a custom blend of voices.

Model files download into models/ the first time (about 205 MB in total).
Replies are spoken sentence by sentence: the next sentence is generated while
the current one plays. Pressing the push-to-talk key cuts speech off.
"""

from __future__ import annotations

import queue
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

from app import config

RELEASES = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
VOICES_FILE = "voices-v1.0.bin"


# -- helpers -------------------------------------------------------------------
def ensure_file(name: str, folder: Path = config.MODELS_DIR) -> Path:
    """Download a model file once, with a simple progress readout."""
    path = folder / name
    if path.exists():
        return path
    folder.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    print(f"Downloading {name} (one time only)...")

    def progress(blocks, block_size, total):
        if total > 0:
            done = min(blocks * block_size, total)
            sys.stdout.write(f"\r  {done / 1e6:6.1f} / {total / 1e6:.1f} MB")
            sys.stdout.flush()

    urllib.request.urlretrieve(RELEASES + name, tmp, progress)
    print()
    tmp.replace(path)
    return path


def parse_blend(spec: str) -> list[tuple[str, float]]:
    """'af_heart*0.5 + af_bella*30% + bf_emma' -> [(name, weight), ...] summing to 1."""
    parts = []
    for piece in spec.split("+"):
        piece = piece.strip()
        if not piece:
            continue
        name, _, weight = piece.partition("*")
        weight = weight.strip()
        w = float(weight.rstrip("%")) / (100 if weight.endswith("%") else 1) if weight else 1.0
        if w <= 0:
            raise ValueError(f"Weight for {name.strip()} must be above zero.")
        parts.append((name.strip(), w))
    if not parts:
        raise ValueError("Empty voice blend.")
    total = sum(w for _, w in parts)
    return [(n, w / total) for n, w in parts]


def speech_text(text: str) -> str:
    """Apply pronunciation fixes and drop symbols that shouldn't be read out."""
    for word, spoken in config.SPOKEN_FORMS.items():
        text = re.sub(rf"\b{re.escape(word)}\b", spoken, text, flags=re.IGNORECASE)
    return re.sub(r"[^\w\s.,!?;:'%()-]", "", text).strip()


def split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip(" .!?")]


# -- playback --------------------------------------------------------------------
class SoundDevicePlayer:
    def play(self, audio, rate):
        import sounddevice as sd
        sd.play(audio, rate)

    def wait(self):
        import sounddevice as sd
        sd.wait()

    def stop(self):
        import sounddevice as sd
        sd.stop()


def load_kokoro():
    from kokoro_onnx import Kokoro
    return Kokoro(str(ensure_file(config.TTS_MODEL)), str(ensure_file(VOICES_FILE)))


class Speaker:
    def __init__(self, blend: str = config.VOICE_BLEND, engine=None, player=None):
        self.engine = engine or load_kokoro()
        self.player = player or SoundDevicePlayer()
        self.set_voice(blend)
        self._queue: queue.Queue = queue.Queue()
        self._generation = 0          # bumped by stop(); older work is skipped
        self.speaking = False         # True while a reply is being generated or played
        self.last_spoke = 0.0         # time.monotonic() when the last reply finished
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def set_voice(self, blend: str) -> None:
        available = set(self.engine.get_voices())
        parts = parse_blend(blend)
        unknown = [n for n, _ in parts if n not in available]
        if unknown:
            raise ValueError(f"Unknown voice(s) {unknown}. Available: {sorted(available)}")
        self.style = sum(self.engine.get_voice_style(n) * w for n, w in parts).astype(np.float32)
        self.blend = blend

    def synth(self, text: str):
        return self.engine.create(text, voice=self.style, speed=config.VOICE_SPEED, lang="en-us")

    def warm_up(self) -> None:
        self.synth("Hi.")

    # -- queue -------------------------------------------------------------------
    def say(self, text: str) -> None:
        if text and text.strip():
            with self._lock:
                self._queue.put((self._generation, text))

    def stop(self) -> None:
        """Cut off anything being spoken or waiting to be spoken."""
        with self._lock:
            self._generation += 1
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
                self._queue.task_done()  # keep the queue's count right, or wait_until_done hangs
        self.player.stop()

    def _current(self, gen: int) -> bool:
        return gen == self._generation

    def _run(self) -> None:
        while True:
            gen, text = self._queue.get()
            self.speaking = True
            for sentence in split_sentences(speech_text(text)):
                if not self._current(gen):
                    break
                try:
                    audio, rate = self.synth(sentence)
                except Exception as e:  # never let a bad sentence kill the voice
                    print(f"   (couldn't speak: {e})")
                    continue
                self.player.wait()        # let the previous sentence finish
                if not self._current(gen):
                    break
                self.player.play(audio, rate)
            if self._queue.empty():
                self.player.wait()        # let the last sentence finish before saying we're done
                self.speaking = False
                self.last_spoke = time.monotonic()
            self._queue.task_done()

    def wait_until_done(self) -> None:
        self._queue.join()
        self.player.wait()
