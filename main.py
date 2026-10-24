"""Adeola: a local assistant that opens apps and sites on Windows.

Run:  python main.py              type commands
      python main.py --voice      also speak: say the wake word, or hold Right Ctrl; Adeola answers out loud
      python main.py --speak      type commands, hear the replies
      add -v to see which path handled each command and how long it took
"""

import argparse
import sys
import threading

from app import config
from app.brain.model import Brain
from app.brain.router import Router
from app.memory.store import Memory
from app.tools.applications import AppIndex
from app.tools.projects import Projects
from app.tools.registry import SCHEMAS, Toolbox

N = config.ASSISTANT_NAME
EXIT_WORDS = {"exit", "quit", "goodbye", "bye"}


def build(model: str = config.MODEL, use_model: bool = True) -> Router:
    apps = AppIndex()
    memory = Memory(config.MEMORY_DB)
    projects = Projects(memory)
    brain = Brain(SCHEMAS, model=model) if use_model else None
    return Router(Toolbox(apps, memory, projects), brain)


# Short replies she says often. Generated in the background at startup so they play instantly.
COMMON_REPLIES = [
    "Yes?", "Sorry, I didn't catch that.", "Hey! What can I do for you?", "Paused.", "Playing.",
    "Next track.", "Previous track.", "Muted.", "Unmuted.", "Turned it up.", "Turned it down.",
    "Nothing is playing right now.", "Locking your PC.", "Okay, no more tips.", "Okay, I won't.",
]


def likely_replies(router: Router) -> list[str]:
    """Common replies, then "Opening X." for your apps (most used first), then your projects."""
    tb = router.toolbox
    names: list[str] = []
    wanted = [k for k, _ in tb.memory.top_counters("open:", 15)] if tb.memory else []
    wanted += list(config.PREFERRED_APPS.values()) + list(config.BUILTIN_APPS)
    for n in wanted:
        app, _ = tb.apps.resolve(n)
        if app and app.name not in names:
            names.append(app.name)
    replies = COMMON_REPLIES + [f"Opening {n}." for n in names]
    if tb.projects:
        from app.tools.projects import opener_command
        for p in tb.projects.all()[:15]:
            where = p.app.title().replace("Vs ", "VS ") if opener_command(p.app, p.path) else "File Explorer"
            replies.append(f"Opening your {p.name} project in {where}.")
    return replies


def start_speaker(verbose: bool = False):
    """Load Adeola's voice (Kokoro). Returns None if it can't start."""
    from app.audio.tts import Speaker
    print(f"{N}: Loading my voice...")
    try:
        speaker = Speaker(verbose=verbose)
        speaker.warm_up()
        return speaker
    except Exception as e:
        print(f"{N}: My voice couldn't start ({e.__class__.__name__}: {e}). I'll reply in text.")
        return None


def start_voice(router: Router, lock: threading.Lock, verbose: bool, speaker=None):
    """Load Whisper, open the mic and start listening for the push-to-talk key."""
    from app.audio.push_to_talk import PushToTalk
    from app.audio.recorder import Recorder
    from app.audio.stt import Transcriber

    print(f"{N}: Loading speech recognition ({config.STT_MODEL})...")
    transcriber = Transcriber()
    recorder = Recorder()
    recorder.open()
    ptt = PushToTalk(router, recorder, transcriber, lock=lock, verbose=verbose, speaker=speaker)
    ptt.start()
    key = config.PUSH_TO_TALK_KEY.replace("_r", " (right)").replace("_l", " (left)")
    print(f"{N}: Voice on. Hold {key} and speak, release when you're done.")

    if config.WAKE_WORD:
        try:
            from app.audio.wake import OpenWakeWordDetector, WakeWord, wake_model_ref
            model_ref, phrase = wake_model_ref()
            wake = WakeWord(ptt.process, OpenWakeWordDetector(model_ref), speaker=speaker,
                            is_busy=lambda: ptt.listening, verbose=verbose)
            recorder.add_listener(wake.on_block)
            print(f"{N}: Or just say \"{phrase}\" and then your command.")
            if phrase != N:
                print(f"   (No {config.WAKE_MODEL} in models/ yet, so the test phrase is used. "
                      "See docs/wake_word.md.)")
        except Exception as e:
            print(f"{N}: Wake word couldn't start ({e.__class__.__name__}: {e}). "
                  "Push-to-talk still works.")
    return ptt, recorder


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{N}, your local assistant")
    parser.add_argument("--model", default=config.MODEL)
    parser.add_argument("--no-model", action="store_true", help="fast path only")
    parser.add_argument("--voice", action="store_true", help="enable push-to-talk")
    parser.add_argument("--speak", action="store_true", help="say replies out loud")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print(f"{N}: Indexing your apps...")
    router = build(args.model, not args.no_model)
    tb = router.toolbox
    print(f"{N}: Found {len(tb.apps.apps)} apps and {len(tb.projects.all())} projects; "
          f"I remember {len(tb.memory.facts())} things.")

    if router.brain:
        try:
            router.brain.warm_up()
            print(f"{N}: Model {args.model} loaded.")
        except Exception as e:
            print(f"{N}: Couldn't load {args.model} ({e}). Simple commands still work.")

    lock = threading.Lock()  # typed and spoken commands take turns
    speaker = None
    if args.speak or (args.voice and config.SPEAK_REPLIES):
        speaker = start_speaker(args.verbose)
        if speaker:
            speaker.prewarm(likely_replies(router))   # background, only while idle

    voice = None
    if args.voice:
        try:
            voice = start_voice(router, lock, args.verbose, speaker)
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
        if text.lower() in ("reload apps", "reload"):
            router.toolbox.apps = AppIndex()
            router.toolbox.projects.reload()
            print(f"{N}: Re-indexed, {len(router.toolbox.apps.apps)} apps and "
                  f"{len(router.toolbox.projects.all())} projects.")
            continue
        with lock:
            result = router.handle(text)
        print(f"{N}: {result.reply}")
        if speaker:
            speaker.stop()           # a new command replaces anything still being said
            speaker.say(result.reply)
        if args.verbose:
            print(f"   [{result.source}, {result.ms:.0f} ms, calls={result.calls}]")

    if speaker:
        speaker.stop()
    if voice:
        ptt, recorder = voice
        ptt.stop()
        recorder.close()
    print(f"{N}: Signing off.")


if __name__ == "__main__":
    sys.exit(main())
