"""Find Adeola's voice: audition Kokoro voices and blends, then save your favourite.

Run from the project folder:
    python -m tests.voice_lab

Then type:
    tour                            hear every female voice once
    af_heart                        hear a single voice
    af_heart*0.6 + bf_emma*0.4      hear a blend (weights are normalised)
    say Opening Brave.              hear the current voice say something else
    speed 1.1                       change speaking speed
    save                            write the current blend into app/config.py
    quit
"""

import re
from pathlib import Path

from app import config
from app.audio.tts import Speaker

SAMPLE = "Hi, I'm Adeola. Opening Visual Studio Code for you. Anything else?"
CONFIG_FILE = Path(config.__file__)


def female_voices(speaker: Speaker) -> list[str]:
    return [v for v in speaker.engine.get_voices() if v[:2] in ("af", "bf")]


def save_blend(blend: str, speed: float) -> None:
    text = CONFIG_FILE.read_text(encoding="utf-8")
    text = re.sub(r'^VOICE_BLEND = .*$', f'VOICE_BLEND = "{blend}"', text, count=1, flags=re.M)
    text = re.sub(r'^VOICE_SPEED = .*$', f'VOICE_SPEED = {speed:g}', text, count=1, flags=re.M)
    CONFIG_FILE.write_text(text, encoding="utf-8")


def main() -> None:
    speaker = Speaker()
    voices = female_voices(speaker)
    print(__doc__)
    print("Female voices: " + ", ".join(voices))
    print("af_ = American, bf_ = British. Current blend: " + speaker.blend)
    sentence = SAMPLE

    while True:
        try:
            cmd = input("\nvoice> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not cmd:
            continue
        low = cmd.lower()
        if low in {"quit", "exit", "q"}:
            break
        try:
            if low == "tour":
                for v in voices:
                    print(f"  {v}")
                    speaker.set_voice(v)
                    speaker.say(f"This is {v.split('_')[1]}.")
                    speaker.wait_until_done()
                continue
            if low.startswith("say "):
                sentence = cmd[4:]
            elif low.startswith("speed "):
                config.VOICE_SPEED = float(cmd[6:])
                print(f"  speed {config.VOICE_SPEED:g}")
            elif low == "save":
                save_blend(speaker.blend, config.VOICE_SPEED)
                print(f"  Saved to config.py: VOICE_BLEND = \"{speaker.blend}\", "
                      f"VOICE_SPEED = {config.VOICE_SPEED:g}")
                continue
            else:
                speaker.set_voice(cmd)
                print(f"  {speaker.blend}")
            speaker.say(sentence)
            speaker.wait_until_done()
        except ValueError as e:
            print(f"  {e}")


if __name__ == "__main__":
    main()
