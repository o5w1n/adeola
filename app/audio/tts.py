"""Adeola's voice: Kokoro text-to-speech on the CPU, with a custom blend of voices.

Model files download into models/ the first time (about 205 MB in total).
Replies are spoken sentence by sentence: the next sentence is generated while
the current one plays. Pressing the push-to-talk key cuts speech off.
"""

from __future__ import annotations

import collections
import io
import queue
import re
import sys
import threading
import time
import urllib.request
import wave
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
    # keyboard shortcuts: "Ctrl+Shift+T" -> "Control Shift T"
    text = re.sub(r"\bCtrl\b", "Control", text)
    text = re.sub(r"\bEsc\b", "Escape", text)
    text = re.sub(r"(?<=\w)\+(?=\w)", " ", text)
    return re.sub(r"[^\w\s.,!?;:'%()-]", "", text).strip()


def split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip(" .!?")]


def speech_chunks(text: str, max_chars: int = 170) -> list[str]:
    """Sentences kept intact up to max_chars; only sentences exceeding max_chars
    are split at clause boundaries ([,;:]), packing clauses up to max_chars and
    never leaving a 1- or 2-word fragment on its own."""
    out: list[str] = []
    for sentence in split_sentences(text):
        if len(sentence) <= max_chars:
            out.append(sentence)
            continue
        parts = [p for p in re.split(r"(?<=[,;:])\s+", sentence) if p]
        merged: list[str] = []
        for part in parts:   # never leave a one- or two-word fragment on its own
            if merged and (
                len(part.split()) < 3
                or len(merged[-1].split()) < 3
                or len(merged[-1]) + 1 + len(part) <= max_chars
            ):
                merged[-1] = f"{merged[-1]} {part}"
            else:
                merged.append(part)
        out.extend(merged)
    return out


class AudioCache:
    """Generated speech, kept in memory and on disk, so repeated replies play instantly.
    Chunks containing digits (times, dates, numbers) are not cached: they rarely repeat."""

    def __init__(self, folder: Path | None, max_ram: int = 200, max_files: int = 800):
        self.folder = folder
        self.max_ram, self.max_files = max_ram, max_files
        self._ram: "collections.OrderedDict[str, tuple]" = collections.OrderedDict()
        self._lock = threading.Lock()
        self._writes = 0
        if folder:
            folder.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def cacheable(text: str) -> bool:
        return not re.search(r"\d", text)

    def get(self, key: str):
        with self._lock:
            if key in self._ram:
                self._ram.move_to_end(key)
                return self._ram[key]
        if self.folder:
            f = self.folder / f"{key}.npz"
            if f.exists():
                try:
                    with np.load(f) as z:
                        item = (z["audio"].astype(np.float32) / 32767.0, int(z["rate"]))
                except (OSError, ValueError, KeyError):
                    return None
                self._remember(key, item)
                return item
        return None

    def put(self, key: str, audio, rate: int) -> None:
        self._remember(key, (audio, rate))
        if not self.folder:
            return
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        tmp = self.folder / f"{key}.tmp.npz"
        np.savez(tmp, audio=pcm, rate=np.array(rate))
        tmp.replace(self.folder / f"{key}.npz")
        self._writes += 1
        if self._writes % 50 == 0:
            self._prune()

    def _remember(self, key, item) -> None:
        with self._lock:
            self._ram[key] = item
            self._ram.move_to_end(key)
            while len(self._ram) > self.max_ram:
                self._ram.popitem(last=False)

    def _prune(self) -> None:
        files = sorted(self.folder.glob("*.npz"), key=lambda f: f.stat().st_mtime)
        for f in files[: max(0, len(files) - self.max_files)]:
            f.unlink(missing_ok=True)


# -- playback --------------------------------------------------------------------
class SoundDevicePlayer:
    """Streams audio chunks through a persistent OutputStream so consecutive
    sentences do not reopen the Windows audio device between chunks."""

    BLOCK_FRAMES = 2048

    def __init__(self, stream_factory=None):
        self._stream_factory = stream_factory
        self._stream = None
        self._rate: int | None = None
        self._queue: queue.Queue = queue.Queue()
        self._abort = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def _open_stream(self, rate: int):
        if self._stream is not None and self._rate == rate:
            return self._stream
        self._close_stream()
        if self._stream_factory is not None:
            self._stream = self._stream_factory(samplerate=rate, channels=1, dtype="float32")
        else:
            import sounddevice as sd
            self._stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32")
        self._stream.start()
        self._rate = rate
        return self._stream

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            self._rate = None

    def _loop(self) -> None:
        while True:
            audio, rate = self._queue.get()
            try:
                if self._abort.is_set():
                    continue
                arr = np.asarray(audio, dtype=np.float32).reshape(-1, 1)
                stream = self._open_stream(int(rate))
                for idx in range(0, len(arr), self.BLOCK_FRAMES):
                    if self._abort.is_set():
                        break
                    stream.write(arr[idx : idx + self.BLOCK_FRAMES])
            except Exception:
                self._close_stream()
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._idle.set()

    def play(self, audio, rate) -> None:
        with self._lock:
            self._abort.clear()
            self._idle.clear()
            self._queue.put((audio, int(rate)))

    def wait(self) -> None:
        self._queue.join()
        self._idle.wait(timeout=10.0)

    def stop(self) -> None:
        with self._lock:
            self._abort.set()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
                self._queue.task_done()
            self._close_stream()
            self._idle.set()


class WindowsSapiEngine:
    """Fallback TTS engine using Windows SAPI when Kokoro cannot be initialized."""

    def get_voices(self) -> list[str]:
        return ["af_heart", "af_bella", "bf_emma", "sapi_default"]

    def get_voice_style(self, name: str) -> np.ndarray:
        return np.ones((1, 256), dtype=np.float32)

    def create(self, text: str, voice=None, speed: float = 1.0, lang: str = "en-us") -> tuple[np.ndarray, int]:
        if config.DRY_RUN or sys.platform != "win32":
            return np.zeros(2400, dtype=np.float32), 24000
        import subprocess
        import tempfile
        import wave

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tmp_path = tf.name
            safe_text = text.replace("'", "''")
            safe_path = tmp_path.replace("'", "''")
            ps_cmd = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.SetOutputToWaveFile('{safe_path}'); "
                f"$s.Speak('{safe_text}'); "
                "$s.Dispose()"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                check=True,
                capture_output=True,
                timeout=10,
            )
            with wave.open(tmp_path, "rb") as wf:
                rate = wf.getframerate()
                n_channels = wf.getnchannels()
                raw = wf.readframes(wf.getnframes())
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if n_channels > 1:
                pcm = pcm.reshape(-1, n_channels).mean(axis=1)
            return pcm, int(rate)
        except Exception:
            return np.zeros(2400, dtype=np.float32), 24000
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)


def load_kokoro(fallback: bool = True, kokoro_loader=None):
    try:
        if kokoro_loader is not None:
            return kokoro_loader()
        from kokoro_onnx import Kokoro
        return Kokoro(str(ensure_file(config.TTS_MODEL)), str(ensure_file(VOICES_FILE)))
    except Exception as e:
        if not fallback:
            raise
        print(f"   (Kokoro unavailable ({e}); using Windows fallback voice.)")
        return WindowsSapiEngine()


class Speaker:
    def __init__(self, blend: str = config.VOICE_BLEND, engine=None, player=None,
                 cache_dir: Path | None = config.VOICE_CACHE_DIR, verbose: bool = False):
        self.engine = engine or load_kokoro()
        self.player = player or SoundDevicePlayer()
        self.set_voice(blend)
        self.cache = AudioCache(cache_dir)
        self.verbose = verbose
        self._queue: queue.Queue = queue.Queue()
        self._generation = 0          # bumped by stop(); older work is skipped
        self.speaking = False         # True while a reply is being generated or played
        self.last_spoke = 0.0         # time.monotonic() when the last reply finished
        self.last_latency_ms = None   # say() -> first sound, for the most recent reply
        self.last_gap_ms: float = 0.0 # max inter-chunk playback gap (ms) in the last reply
        self._last_request = 0.0
        self._lock = threading.Lock()
        self._engine_lock = threading.Lock()   # one generation at a time (live or pre-warm)
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

    def voice_name(self) -> str:
        for name, blend in config.VOICES.items():
            if blend == self.blend:
                return name
        return self.blend

    def switch_voice(self, name_or_blend: str) -> tuple[bool, str]:
        key = name_or_blend.strip().lower()
        blend = config.VOICES.get(key, name_or_blend.strip())
        label = key if key in config.VOICES else blend
        if blend == self.blend:
            return True, f"Already using my {label} voice."
        try:
            self.set_voice(blend)
        except ValueError:
            return False, f"Unknown voice '{name_or_blend}'. Available: {', '.join(config.VOICES)}."
        return True, f"Switched to my {label} voice."

    def synth(self, text: str):
        with self._engine_lock:
            return self.engine.create(text, voice=self.style, speed=config.VOICE_SPEED, lang="en-us")

    def _key(self, chunk: str) -> str:
        import hashlib
        raw = f"{config.TTS_MODEL}|{self.blend}|{config.VOICE_SPEED}|{chunk}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def synth_cached(self, chunk: str):
        """(audio, rate, was_cached) for one chunk of already-cleaned speech text."""
        if not AudioCache.cacheable(chunk):
            return (*self.synth(chunk), False)
        key = self._key(chunk)
        hit = self.cache.get(key)
        if hit is not None:
            return (*hit, True)
        audio, rate = self.synth(chunk)
        self.cache.put(key, audio, rate)
        return audio, rate, False

    def _synth(self, chunk: str) -> tuple[np.ndarray, int]:
        audio, rate, _ = self.synth_cached(chunk)
        return np.asarray(audio, dtype=np.float32), int(rate)

    def synthesize_wav(self, text: str) -> bytes:
        """Synthesize speech into a mono 16-bit PCM WAV byte buffer for remote clients."""
        cleaned = speech_text(text) if text else ""
        chunks = speech_chunks(cleaned) if cleaned else []
        rate = 24000
        parts: list[np.ndarray] = []
        pause_ms = getattr(config, "SENTENCE_PAUSE_MS", 120)
        for idx, chunk in enumerate(chunks):
            arr, chunk_rate = self._synth(chunk)
            rate = int(chunk_rate) if chunk_rate else rate
            parts.append(np.asarray(arr, dtype=np.float32).reshape(-1))
            if idx < len(chunks) - 1 and chunk.rstrip().endswith((".", "!", "?")) and pause_ms > 0:
                parts.append(np.zeros(int(rate * pause_ms / 1000), dtype=np.float32))
        if not parts:
            audio = np.zeros(int(rate * 0.1), dtype=np.float32)
        else:
            audio = np.concatenate(parts)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()

    def warm_up(self) -> None:
        self.synth("Hi.")

    def prewarm(self, replies: list[str], idle_seconds: float = 8.0) -> threading.Thread:
        """Generate likely replies in the background, only while Adeola is idle."""
        def work():
            for reply in replies:
                for chunk in speech_chunks(speech_text(reply)):
                    if not AudioCache.cacheable(chunk) or self.cache.get(self._key(chunk)):
                        continue
                    while (self.speaking or not self._queue.empty()
                           or time.monotonic() - self._last_request < idle_seconds):
                        time.sleep(0.2)            # live speech always goes first
                    try:
                        self.synth_cached(chunk)
                    except Exception:
                        return
        t = threading.Thread(target=work, daemon=True)
        t.start()
        return t

    # -- queue -------------------------------------------------------------------
    def say(self, text: str) -> None:
        if text and text.strip():
            with self._lock:
                self._last_request = time.monotonic()
                self._queue.put((self._generation, text, time.perf_counter()))

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
            gen, text, asked_at = self._queue.get()
            was_speaking = self.speaking
            self.speaking = True
            self.last_gap_ms = 0.0
            first = True
            prev_play_started = 0.0
            prev_audio_ms = 0.0
            for chunk in speech_chunks(speech_text(text)):
                if not self._current(gen):
                    break
                try:
                    audio, rate, cached = self.synth_cached(chunk)
                except Exception as e:  # never let a bad sentence kill the voice
                    print(f"   (couldn't speak: {e})")
                    continue
                if not first or was_speaking:
                    wait_start = time.perf_counter()
                    self.player.wait()        # let the previous chunk finish
                    wait_end = time.perf_counter()
                    if not first:
                        synth_ms = (wait_start - prev_play_started) * 1000
                        wait_ms = (wait_end - wait_start) * 1000
                        gap_ms = 0.0 if wait_ms >= 1.0 else max(0.0, synth_ms - prev_audio_ms)
                        if gap_ms > self.last_gap_ms:
                            self.last_gap_ms = gap_ms
                if not self._current(gen):
                    break
                self.player.play(audio, rate)
                prev_play_started = time.perf_counter()
                prev_audio_ms = (len(audio) / rate) * 1000 if rate else 0.0
                if first:
                    first = False
                    self.last_latency_ms = (time.perf_counter() - asked_at) * 1000
                    if self.verbose:
                        print(f"   [voice started after {self.last_latency_ms:.0f} ms"
                              f"{', cached' if cached else ''}]", flush=True)
            if self.verbose and self.last_gap_ms > 20:
                print(f"   [voice gap {self.last_gap_ms:.0f} ms]", flush=True)
            if self._queue.empty():
                self.player.wait()        # let the last chunk finish before saying we're done
                self.speaking = False
                self.last_spoke = time.monotonic()
            self._queue.task_done()

    def wait_until_done(self) -> None:
        self._queue.join()
        self.player.wait()
