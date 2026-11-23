"""Live end-to-end preflight check for Adeola with latency budget and offline verification."""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import ollama
from rapidfuzz import fuzz

from app import config
from app.audio.stt import Transcriber
from app.audio.tts import load_kokoro, parse_blend, speech_chunks, speech_text
from app.audio.wake import wake_model_ref
from app.brain.model import Brain
from app.memory.notes import NotesIndex
from app.memory.store import Memory, words
from app.netwatch import NEEDS_INTERNET, ConnectionLog
from app.tools.registry import SCHEMAS

LOOP_SENTENCE = "Good afternoon. The local voice is live."
LOOP_MIN = 85
SWAP_WARN_SECONDS = 4.0

_MARKS = {"PASS": "✓", "FAIL": "✗", "WARN": "!"}


@dataclass
class Check:
    name: str
    status: str
    detail: str
    seconds: float | None = None


def run_check(
    name: str,
    fn: Callable[[], str | tuple[str, float]],
    warn_only: bool = False,
) -> Check:
    """Run a single preflight check, capturing any exception as FAIL (or WARN if warn_only)."""
    t0 = time.perf_counter()
    try:
        res = fn()
        elapsed = time.perf_counter() - t0
        if isinstance(res, tuple):
            detail, seconds = res
            return Check(name, "PASS", str(detail), float(seconds))
        return Check(name, "PASS", str(res), elapsed)
    except Exception as e:
        elapsed = time.perf_counter() - t0
        status = "WARN" if warn_only else "FAIL"
        return Check(name, status, f"{e.__class__.__name__}: {e}", elapsed)


def _norm(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(cleaned.split())


def loop_match(expected: str, heard: str) -> float:
    """Fuzzy similarity (0..100) on lowercased text with punctuation stripped."""
    return float(fuzz.ratio(_norm(expected), _norm(heard)))


def summary(checks: list[Check]) -> tuple[str, int]:
    """Return ('N pass · N fail · N warn', exit_code)."""
    n_pass = sum(1 for c in checks if c.status == "PASS")
    n_fail = sum(1 for c in checks if c.status == "FAIL")
    n_warn = sum(1 for c in checks if c.status == "WARN")
    code = 1 if n_fail > 0 else 0
    return f"{n_pass} pass · {n_fail} fail · {n_warn} warn", code


def budget_line(hear: float | None, think: float | None, speak: float | None) -> str:
    """Format the latency budget line across STT (hear), LLM (think), and TTS (speak)."""
    fmt = lambda v: f"{v:.2f} s" if v is not None else "n/a"  # noqa: E731
    present = [v for v in (hear, think, speak) if v is not None]
    total_str = f"{sum(present):.2f} s" if present else "n/a"
    return (
        f"LATENCY BUDGET: hear {fmt(hear)} · think {fmt(think)} "
        f"· speak {fmt(speak)} · total {total_str}"
    )


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int = config.SAMPLE_RATE) -> np.ndarray:
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32)
    n_out = int(round(len(audio) * dst_rate / src_rate))
    x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _format_check(c: Check) -> str:
    mark = _MARKS.get(c.status, "?")
    sec = f" ({c.seconds:.2f} s)" if c.seconds is not None else ""
    detail = f" — {c.detail}" if c.detail else ""
    return f"  {mark} {c.name}{detail}{sec}"


def _model_names(resp) -> list[str]:
    models = getattr(resp, "models", None)
    if models is None and isinstance(resp, dict):
        models = resp.get("models", [])
    names = []
    for m in models or []:
        name = getattr(m, "model", None) or getattr(m, "name", None)
        if name is None and isinstance(m, dict):
            name = m.get("model") or m.get("name")
        if name:
            names.append(str(name))
    return names


def _has_model(names: list[str], target: str) -> bool:
    return any(
        n == target or n == f"{target}:latest" or (":" not in target and n.startswith(f"{target}:"))
        for n in names
    )


def check_brain_swap(
    brain,
    installed_models: list[str],
    warn_seconds: float = SWAP_WARN_SECONDS,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[str, float]:
    """Time a smart -> coder -> smart swap when AUTO_BRAIN is enabled."""
    if not config.AUTO_BRAIN:
        return "auto-brain off (skipped)", 0.0
    smart_tag = config.BRAINS.get("smart", config.MODEL)
    coder_tag = config.BRAINS.get("coder", "")
    if not coder_tag or coder_tag == smart_tag:
        return "single local model configured (skipped)", 0.0
    if not _has_model(installed_models, coder_tag):
        raise RuntimeError(
            f"{coder_tag} not pulled — AUTO_BRAIN will fail on coding queries "
            f"(run `ollama pull {coder_tag}` or set AUTO_BRAIN = False)"
        )

    dt_to_coder = 0.0
    dt_to_smart = 0.0
    swapped = False
    try:
        t0 = clock()
        ok, msg = brain.switch_model("coder")
        dt_to_coder = clock() - t0
        if not ok:
            raise RuntimeError(f"smart→coder failed: {msg}")
        swapped = True

        t1 = clock()
        ok, msg = brain.switch_model("smart")
        dt_to_smart = clock() - t1
        if not ok:
            raise RuntimeError(f"coder→smart failed: {msg}")
        swapped = False
    finally:
        if swapped:
            try:
                brain.switch_model("smart")
            except Exception:
                pass

    total = dt_to_coder + dt_to_smart
    detail = f"smart→coder {dt_to_coder:.2f} s, coder→smart {dt_to_smart:.2f} s"
    if dt_to_coder > warn_seconds or dt_to_smart > warn_seconds:
        raise RuntimeError(f"{detail} (> {warn_seconds:.1f} s; consider AUTO_BRAIN = False)")
    return detail, total


def check_notes(index: NotesIndex | None = None) -> str:
    """Verify the notes index loads and can retrieve a note by title."""
    if index is None:
        index = NotesIndex(config.NOTES_DIRS)
    if index.missing and not index.notes:
        raise FileNotFoundError(
            f"Notes folder not found: {', '.join(str(p) for p in index.missing)}"
        )
    if not index.notes:
        raise FileNotFoundError("No markdown notes found.")
    first = next((n for n in index.notes if words(n.title)), index.notes[0])
    hits = index.search(first.title, limit=5, min_score=1)
    if not any(h.path == first.path or h.title == first.title for h in hits):
        raise AssertionError(f"Could not retrieve note {first.title!r} by title.")
    return f"{len(index.notes)} notes indexed ({index.load_ms:.0f} ms)"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    checks: list[Check] = []
    hear: float | None = None
    think: float | None = None
    speak: float | None = None

    def record(c: Check) -> Check:
        checks.append(c)
        print(_format_check(c))
        return c

    # 1. Ollama + model
    installed_models: list[str] = []

    def check_ollama() -> str:
        nonlocal installed_models
        hint = f"Start Ollama, then: ollama pull {config.MODEL}"
        try:
            resp = ollama.Client().list()
        except Exception as e:
            raise RuntimeError(f"{hint} ({e})") from e
        installed_models = _model_names(resp)
        if not _has_model(installed_models, config.MODEL):
            raise RuntimeError(hint)
        return f"{config.MODEL} ready"

    record(run_check("Ollama + model", check_ollama))

    # Checks 2-5 run inside one ConnectionLog to verify zero external traffic.
    synth_audio: np.ndarray | None = None
    synth_rate: int = 24000

    with ConnectionLog() as net_log:
        brain = Brain(SCHEMAS)
        try:
            brain.warm_up()
        except Exception:
            pass

        # 2. Brain, tool choice
        def check_tool_choice() -> str:
            calls, reply = brain.decide("open notepad")
            if not any(name == "open_app" for name, _ in calls):
                raise RuntimeError(f"expected open_app, got calls={calls!r} reply={reply!r}")
            arg = next(args for name, args in calls if name == "open_app")
            return f"open_app({arg.get('name', '')!r})"

        record(run_check("Brain, tool choice", check_tool_choice))

        # 3. Brain, chat (= think)
        def check_chat() -> tuple[str, float]:
            t0 = time.perf_counter()
            _, reply = brain.decide("Suggest a name for a cat.")
            dt = time.perf_counter() - t0
            clean_reply = (reply or "").strip()
            if not clean_reply:
                raise RuntimeError("empty reply from Brain")
            return clean_reply, dt

        c_think = record(run_check("Brain, chat", check_chat))
        if c_think.status == "PASS":
            think = c_think.seconds

        # 3b. Brain swap (smart <-> coder, warn_only)
        record(
            run_check(
                "Brain swap",
                lambda: check_brain_swap(brain, installed_models),
                warn_only=True,
            )
        )

        # 4. Voice (= speak, warn_only)
        def check_voice() -> tuple[str, float]:
            nonlocal synth_audio, synth_rate
            engine = load_kokoro()
            parts = parse_blend(config.VOICE_BLEND)
            style = sum(engine.get_voice_style(n) * w for n, w in parts).astype(np.float32)
            chunks = speech_chunks(speech_text(LOOP_SENTENCE))
            t0 = time.perf_counter()
            first_audio, rate = engine.create(
                chunks[0], voice=style, speed=config.VOICE_SPEED, lang="en-us"
            )
            dt_first = time.perf_counter() - t0
            rest = [
                engine.create(c, voice=style, speed=config.VOICE_SPEED, lang="en-us")[0]
                for c in chunks[1:]
            ]
            synth_audio = np.concatenate([first_audio, *rest]).astype(np.float32)
            synth_rate = int(rate)
            return f"first chunk {dt_first:.2f} s ({len(synth_audio) / synth_rate:.1f} s audio)", dt_first

        c_speak = record(run_check("Voice", check_voice, warn_only=True))
        if c_speak.status == "PASS":
            speak = c_speak.seconds

        # 5. The loop (= hear): WARN if voice or ears are missing, FAIL on a mismatch.
        transcriber: Transcriber | None = None
        if synth_audio is None:
            record(
                run_check(
                    "The loop",
                    lambda: (_ for _ in ()).throw(RuntimeError("voice audio unavailable")),
                    warn_only=True,
                )
            )
        else:
            t_load0 = time.perf_counter()
            try:
                transcriber = Transcriber()
            except Exception as e:
                record(
                    Check(
                        "The loop",
                        "WARN",
                        f"{e.__class__.__name__}: {e}",
                        time.perf_counter() - t_load0,
                    )
                )
            if transcriber is not None:
                audio_16k = _resample(synth_audio, synth_rate, config.SAMPLE_RATE)

                def check_loop() -> tuple[str, float]:
                    assert transcriber is not None
                    t0 = time.perf_counter()
                    heard = transcriber.transcribe(audio_16k)
                    dt = time.perf_counter() - t0
                    score = loop_match(LOOP_SENTENCE, heard)
                    if score < LOOP_MIN:
                        raise AssertionError(
                            f"heard {heard!r} (match {score:.0f}% < {LOOP_MIN}%)"
                        )
                    return f'heard "{heard}" ({score:.0f}%)', dt

                c_hear = record(run_check("The loop", check_loop, warn_only=False))
                if c_hear.status == "PASS":
                    hear = c_hear.seconds

    # 6. Memory round-trip
    def check_memory() -> str:
        mem = Memory(config.MEMORY_DB)
        fact = None
        try:
            fact = mem.add_fact("preflight zebra check")
            found = any(f.id == fact.id for f in mem.search("zebra"))
            if not found:
                raise AssertionError("added fact not found by search('zebra')")
            return f"add/search/delete ok ({Path(config.MEMORY_DB).name})"
        finally:
            try:
                if fact is not None:
                    mem.delete_fact(fact.id)
            finally:
                mem.close()

    record(run_check("Memory round-trip", check_memory))

    # 7. Notes (warn_only)
    record(run_check("Notes", lambda: check_notes(), warn_only=True))

    # 8. Wake word (warn_only)
    def check_wake() -> str:
        model_ref, phrase = wake_model_ref()
        if phrase != config.ASSISTANT_NAME:
            raise RuntimeError("still on 'hey jarvis', see docs/wake_word.md")
        return f"{phrase} ({Path(model_ref).name})"

    record(run_check("Wake word", check_wake, warn_only=True))

    # 9. Microphone (warn_only)
    def check_mic() -> str:
        import sounddevice as sd

        dev = sd.query_devices(kind="input")
        name = dev.get("name", "input device") if isinstance(dev, dict) else str(dev)
        return str(name)

    record(run_check("Microphone", check_mic, warn_only=True))

    # 10. Offline
    def check_offline() -> str:
        if net_log.external:
            raise RuntimeError(f"external connection(s): {', '.join(net_log.external)}")
        return "no external connections during checks 2-5"

    record(run_check("Offline", check_offline))

    print("Needs the internet:")
    for item, desc in NEEDS_INTERNET.items():
        print(f"  - {item}: {desc}")
    print(budget_line(hear, think, speak))
    sum_text, code = summary(checks)
    print(sum_text)
    return code


if __name__ == "__main__":
    sys.exit(main())
