"""Adeola: a local assistant that opens apps and sites on Windows.

Run:  python main.py              type commands
      python main.py --voice      also hold Right Ctrl and speak
      add -v to see which path handled each command and how long it took
"""

import argparse
import sys
import threading

from app import config
from app.brain.model import Brain
from app.brain.router import Router
from app.tools.applications import AppIndex
from app.tools.registry import SCHEMAS, Toolbox

N = config.ASSISTANT_NAME
EXIT_WORDS = {"exit", "quit", "goodbye", "bye"}


def build(model: str = config.MODEL, use_model: bool = True) -> Router:
    apps = AppIndex()
    brain = Brain(SCHEMAS, model=model) if use_model else None
    return Router(Toolbox(apps), brain)


def start_voice(router: Router, lock: threading.Lock, verbose: bool):
    """Load Whisper, open the mic and start listening for the push-to-talk key."""
    from app.audio.push_to_talk import PushToTalk
    from app.audio.recorder import Recorder
    from app.audio.stt import Transcriber

    print(f"{N}: Loading speech recognition ({config.STT_MODEL})...")
    transcriber = Transcriber()
    recorder = Recorder()
    recorder.open()
    ptt = PushToTalk(router, recorder, transcriber, lock=lock, verbose=verbose)
    ptt.start()
    key = config.PUSH_TO_TALK_KEY.replace("_r", " (right)").replace("_l", " (left)")
    print(f"{N}: Voice on. Hold {key} and speak, release when you're done.")
    return ptt, recorder


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{N}, your local assistant")
    parser.add_argument("--model", default=config.MODEL)
    parser.add_argument("--no-model", action="store_true", help="fast path only")
    parser.add_argument("--voice", action="store_true", help="enable push-to-talk")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print(f"{N}: Indexing your apps...")
    router = build(args.model, not args.no_model)
    print(f"{N}: Found {len(router.toolbox.apps.apps)} apps.")

    if router.brain:
        try:
            router.brain.warm_up()
            print(f"{N}: Model {args.model} loaded.")
        except Exception as e:
            print(f"{N}: Couldn't load {args.model} ({e}). Simple commands still work.")

    lock = threading.Lock()  # typed and spoken commands take turns
    voice = None
    if args.voice:
        try:
            voice = start_voice(router, lock, args.verbose)
        except Exception as e:
            print(f"{N}: Voice couldn't start ({e.__class__.__name__}: {e}). Typing still works.")

    print(f"{N}: Online. Type 'help' for ideas or 'exit' to quit.")
    while True:
        try:
            text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower().strip(" .!") in EXIT_WORDS:
            break
        if text.lower() == "reload apps":
            router.toolbox.apps = AppIndex()
            print(f"{N}: Re-indexed, {len(router.toolbox.apps.apps)} apps.")
            continue
        with lock:
            result = router.handle(text)
        print(f"{N}: {result.reply}")
        if args.verbose:
            print(f"   [{result.source}, {result.ms:.0f} ms, calls={result.calls}]")

    if voice:
        ptt, recorder = voice
        ptt.stop()
        recorder.close()
    print(f"{N}: Signing off.")


if __name__ == "__main__":
    sys.exit(main())
