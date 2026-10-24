"""Adeola settings. Edit this file to customise names, aliases and the model."""

import os
from pathlib import Path

# --- Model -----------------------------------------------------------------
MODEL = os.getenv("ADEOLA_MODEL", "qwen3:4b")
KEEP_ALIVE = "30m"      # keep the model loaded so replies stay fast
NUM_CTX = 4096          # fits fully in a 4 GB GPU with qwen3:4b
HISTORY_TURNS = 3       # past exchanges sent to the model for chat context
# "json": model must answer in a short fixed JSON shape (fast, can't ramble).
# "tools": Ollama's native tool calling (more flexible, but small models write a lot).
DECISION_MODE = os.getenv("ADEOLA_MODE", "json")
MAX_OUTPUT_TOKENS = 150  # hard cap on how much the model may write per command

# --- Behaviour -------------------------------------------------------------
# ADEOLA_DRY_RUN=1 prints actions instead of performing them (used by the eval).
DRY_RUN = os.getenv("ADEOLA_DRY_RUN") == "1"
ASSISTANT_NAME = "Adeola"                       # what the assistant calls itself
WAKE_NAMES = (ASSISTANT_NAME.lower(),)  # names stripped from the start of commands
SEARCH_URL = "https://www.google.com/search?q="

# --- Voice (Phase 2) ---------------------------------------------------------
PUSH_TO_TALK_KEY = "ctrl_r"   # hold Right Ctrl and speak. Any pynput key name ("f9", "alt_r") or a letter
STT_MODEL = "base.en"         # Whisper size: "base.en" is fast; "small.en" is more accurate, ~3x slower
STT_THREADS = 4               # CPU threads for Whisper (it stays off the GPU, which the LLM needs)
SAMPLE_RATE = 16000           # what Whisper expects
MIN_RECORD_SECONDS = 0.4      # shorter presses are treated as accidental
MAX_RECORD_SECONDS = 15
BEEPS = True                  # short sounds when listening starts and stops

# Wake word: say the name instead of holding the key (both work with --voice).
WAKE_WORD = True
WAKE_MODEL = "adeola.onnx"    # your trained model, in models/. Until it exists, "hey jarvis" is used
WAKE_FALLBACK = "hey_jarvis"  # built-in openWakeWord model for testing
WAKE_THRESHOLD = 0.5          # 0-1. Raise to 0.6-0.7 if it triggers by itself
END_SILENCE_SECONDS = 0.8     # after the wake word, this much quiet ends your command
NO_SPEECH_TIMEOUT = 4.0       # give up if you say nothing after the wake word
MAX_COMMAND_SECONDS = 10

# --- Spoken replies (Kokoro, runs on the CPU) ------------------------------------
SPEAK_REPLIES = True          # with --voice, Adeola says her replies out loud
# Your own mix of Kokoro voices. Audition blends with: python -m tests.voice_lab
VOICE_BLEND = "af_heart*0.5 + af_bella*0.3 + bf_emma*0.2"
VOICE_SPEED = 1.0
TTS_MODEL = "kokoro-v1.0.fp16.onnx"   # fastest on CPU; the int8 file is ~4x slower
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"  # downloaded on first use
# How to say words the voice gets wrong. Spelling is phonetic, tune it by ear.
SPOKEN_FORMS = {"Adeola": "Ahdeh-ohla"}

# --- Memory (Phase 3) -----------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"   # stays on this PC, not in git
MEMORY_DB = DATA_DIR / "adeola.db"
MEMORY_IN_PROMPT = 5          # how many relevant facts the model sees with each request

TIPS = True                   # teach a shortcut now and then when opening apps ("no more tips" stops them)
TIP_REPEATS = 2               # each tip is retired after being taught this many times
OFFER_TO_REMEMBER = True      # offer to remember things you mention in passing ("keep" / "skip")

# Folders whose subfolders are your projects ("open my Quail project").
PROJECT_ROOTS = [
    "~/AndroidStudioProjects",
    "~/Projects",
    "~/source/repos",
    "~/Documents/GitHub",
    "~/Documents/Projects",
]
# How to open a project folder in each app. The first one found is used; if none is,
# the folder opens in File Explorer.
PROJECT_OPENERS = {
    "android studio": [
        r"%ProgramFiles%\Android\Android Studio\bin\studio64.exe",
        r"%LOCALAPPDATA%\Programs\Android Studio\bin\studio64.exe",
        "studio64",
    ],
    "vs code": ["code", r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"],
}

# What you say -> the name Windows shows in the Start menu.
APP_ALIASES = {
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "code": "visual studio code",
    "chrome": "google chrome",
    "edge": "microsoft edge",
    "word": "word",
    "excel": "excel",
    "terminal": "terminal",
    "android": "android studio",
    "files": "file explorer",
    "my files": "file explorer",
}

# Your go-to app for each kind of job. The model uses these for vague requests
# like "I want to write some code" or "I need to browse the internet".
PREFERRED_APPS = {
    "browsing the web": "Brave",
    "writing code": "Visual Studio Code",
    "Android development": "Android Studio",
    "checking CPU or memory use": "Task Manager",
    "changing display, Wi-Fi, Bluetooth or sound": "Settings",
}

# Apps that always exist on Windows, even if the Start menu scan misses them.
BUILTIN_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "settings": "ms-settings:",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "paint": "mspaint.exe",
}

# If no installed app matches, these names open a website instead.
SITE_SHORTCUTS = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "google": "https://www.google.com",
    "google drive": "https://drive.google.com",
    "whatsapp web": "https://web.whatsapp.com",
}
