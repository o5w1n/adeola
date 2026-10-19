"""Live wake-word meter: see how strongly each thing you say scores, to tune WAKE_THRESHOLD.

Run from the project folder:
    python -m tests.wake_check              # your adeola.onnx if present, else "hey jarvis"
    python -m tests.wake_check --device 2   # pick a different microphone (see mic_check --list)
Ctrl+C to stop.
"""

import argparse
import queue
import time

from app import config
from app.audio.recorder import Recorder
from app.audio.wake import OpenWakeWordDetector, wake_model_ref


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--device", type=int, default=None)
    args = p.parse_args()

    ref, phrase = wake_model_ref()
    print(f"Loading wake word model: {ref}")
    detector = OpenWakeWordDetector(ref)
    blocks: queue.Queue = queue.Queue()
    rec = Recorder()
    rec.add_listener(blocks.put)
    rec.open(args.device)
    print(f"Say \"{phrase}\" a few times, and some other sentences. "
          f"Threshold is {config.WAKE_THRESHOLD}. Ctrl+C to stop.\n")

    peak, peak_at, hits = 0.0, 0.0, 0
    try:
        while True:
            score = detector.score(blocks.get())
            bar = "#" * int(score * 40)
            print(f"\r{score:4.2f} |{bar:<40}|", end="", flush=True)
            if score > peak:
                peak, peak_at = score, time.monotonic()
            # report each utterance's peak once it has passed
            if peak > 0.2 and time.monotonic() - peak_at > 1.0:
                fired = peak >= config.WAKE_THRESHOLD
                hits += fired
                print(f"\r{'FIRED' if fired else 'below'}  peak {peak:.2f}{' ' * 40}")
                peak = 0.0
    except KeyboardInterrupt:
        print(f"\n{hits} detection(s).")
    finally:
        rec.close()


if __name__ == "__main__":
    main()
