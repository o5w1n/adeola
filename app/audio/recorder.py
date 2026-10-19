"""Microphone capture, shared by push-to-talk and the wake word.

The stream stays open the whole time and keeps the last half second of audio,
so the start of a word spoken as you press the key isn't cut off. Other parts
(the wake-word listener) can subscribe to every block with add_listener().
"""

from __future__ import annotations

import collections
import threading

import numpy as np

from app import config

PREROLL_SECONDS = 0.5
BLOCK = 1280  # 80 ms at 16 kHz, the chunk size openWakeWord works in


class Recorder:
    def __init__(self, rate: int = config.SAMPLE_RATE):
        self.rate = rate
        self._lock = threading.Lock()
        self._preroll: collections.deque = collections.deque(
            maxlen=max(1, int(PREROLL_SECONDS * rate / BLOCK)))
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._stream = None
        self._listeners: list = []

    def add_listener(self, fn) -> None:
        """fn(block) is called with every 80 ms block of float32 audio. Keep it fast."""
        self._listeners.append(fn)

    def open(self, device=None) -> None:
        import sounddevice as sd  # imported here so tests don't need audio hardware
        self._stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32",
                                      blocksize=BLOCK, device=device, callback=self._callback)
        self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, indata, frames, time_info, status) -> None:
        block = indata[:, 0].copy()
        with self._lock:
            if self._recording:
                self._chunks.append(block)
            else:
                self._preroll.append(block)
        for fn in self._listeners:
            fn(block)

    def start(self) -> None:
        with self._lock:
            self._chunks = list(self._preroll)
            self._preroll.clear()
            self._recording = True

    def stop(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)[: int(config.MAX_RECORD_SECONDS * self.rate)]
