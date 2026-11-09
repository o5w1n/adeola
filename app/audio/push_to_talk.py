"""Hold a key, speak, release: the speech is transcribed and handled like typed text."""

from __future__ import annotations

import sys
import threading
import time

from app import config


def parse_key(name: str):
    """'ctrl_r' -> Key.ctrl_r, 'f9' -> Key.f9, 'x' -> KeyCode for 'x'."""
    from pynput.keyboard import Key, KeyCode
    if hasattr(Key, name):
        return getattr(Key, name)
    if len(name) == 1:
        return KeyCode.from_char(name.lower())
    raise ValueError(f"Unknown push-to-talk key {name!r}. Try 'ctrl_r', 'alt_r' or 'f9'.")


def _safe_beep(freq: int) -> None:
    import winsound
    try:
        winsound.Beep(freq, 80)
    except RuntimeError:
        pass


def beep(freq: int) -> None:
    if config.BEEPS and sys.platform == "win32":
        threading.Thread(target=_safe_beep, args=(freq,), daemon=True).start()


MISSED_REPLIES = (
    "Sorry, I didn't catch that.",
    "I missed that — could you say it again?",
    "Didn't quite catch that.",
)


class PushToTalk:
    def __init__(self, router, recorder, transcriber, lock: threading.Lock | None = None,
                 key=None, say=print, verbose: bool = False, speaker=None):
        self.router = router
        self.recorder = recorder
        self.transcriber = transcriber
        self.lock = lock or threading.Lock()   # one command at a time, typed or spoken
        self.key = key
        self.say = say
        self.verbose = verbose
        self.speaker = speaker  # optional: speaks replies, and is cut off when you press the key
        self.listening = False
        self._listener = None
        self._missed_idx = 0

    # -- keyboard ------------------------------------------------------------
    def start(self) -> None:
        from pynput import keyboard
        self.key = self.key or parse_key(config.PUSH_TO_TALK_KEY)
        self._listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()

    def on_press(self, key) -> None:
        if key != self.key or self.listening:  # holding a key repeats on_press; ignore repeats
            return
        self.listening = True
        if self.speaker:
            self.speaker.stop()  # talking over Adeola interrupts her
        self.recorder.start()
        beep(880)
        self.say("🎙  Listening...")

    def on_release(self, key) -> None:
        if key != self.key or not self.listening:
            return
        self.listening = False
        audio = self.recorder.stop()
        beep(440)
        threading.Thread(target=self.process, args=(audio,), daemon=True).start()

    # -- speech -> command ---------------------------------------------------
    def process(self, audio) -> str | None:
        seconds = len(audio) / config.SAMPLE_RATE
        if seconds < config.MIN_RECORD_SECONDS:
            self.say("   (too short, hold the key while you speak)")
            return None
        start = time.perf_counter()
        text = self.transcriber.transcribe(audio)
        stt_ms = (time.perf_counter() - start) * 1000
        if not text:
            msg = MISSED_REPLIES[self._missed_idx % len(MISSED_REPLIES)]
            self._missed_idx += 1
            self.reply(msg)
            return None
        self.say(f"You (voice): {text}")
        with self.lock:
            result = self.router.handle(text)
        self.reply(result.reply)
        if getattr(result, "sources", None):
            self.say(f"   (from your notes: {', '.join(result.sources)})")
        if self.verbose:
            self.say(f"   [{seconds:.1f}s audio · speech-to-text {stt_ms:.0f} ms · "
                     f"{result.source} {result.ms:.0f} ms]")
        return result.reply

    def reply(self, text: str) -> None:
        self.say(f"{config.ASSISTANT_NAME}: {text}")
        if self.speaker:
            self.speaker.say(text)
