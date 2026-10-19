"""Check your microphone and Whisper before using voice mode.

Run from the project folder:
    python -m tests.mic_check               # record 4 seconds from the default mic
    python -m tests.mic_check --list        # show available microphones
    python -m tests.mic_check --device 2 --seconds 6 --model small.en
"""

import argparse
import time

import numpy as np

from app import config


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true")
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--seconds", type=float, default=4)
    p.add_argument("--model", default=config.STT_MODEL)
    args = p.parse_args()

    import sounddevice as sd
    if args.list:
        print(sd.query_devices())
        return

    print(f"Loading Whisper {args.model} (first run downloads it)...")
    start = time.perf_counter()
    from app.audio.stt import Transcriber
    stt = Transcriber(args.model)
    print(f"Loaded in {time.perf_counter() - start:.1f} s.")

    input(f"\nPress Enter, then say something like '{config.ASSISTANT_NAME}, open Brave'...")
    print(f"Recording {args.seconds:g} s...")
    audio = sd.rec(int(args.seconds * config.SAMPLE_RATE), samplerate=config.SAMPLE_RATE,
                   channels=1, dtype="float32", device=args.device)
    sd.wait()
    audio = audio[:, 0]

    peak = float(np.abs(audio).max()) if audio.size else 0.0
    level = "too quiet, check the mic or move closer" if peak < 0.05 else \
            "clipping, move back a little" if peak > 0.98 else "good"
    print(f"Mic level: peak {peak:.2f} ({level})")

    start = time.perf_counter()
    text = stt.transcribe(audio)
    ms = (time.perf_counter() - start) * 1000
    print(f"Heard: {text!r}")
    print(f"Speech-to-text took {ms:.0f} ms for {args.seconds:g} s of audio.")


if __name__ == "__main__":
    main()
