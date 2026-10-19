"""Adeola settings. Edit this file to customise names, aliases and the model."""

import os

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
