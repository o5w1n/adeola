#!/bin/bash
# Train the wake word on a Mac (no Colab, no admin rights needed).
#
#   tools/wakeword_mac/run.sh                         # Adeola
#   tools/wakeword_mac/run.sh --name kiara --phrase "kee ah ruh" --phrase "key ah ruh"
#
# Safe to run again after an interruption: every step resumes where it stopped.
# Everything goes in ~/wakeword_work (about 5 GB). Delete that folder afterwards to free space.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WAKEWORD_WORK:-$HOME/wakeword_work}"
mkdir -p "$WORK"

# 1. uv: installs Python 3.12 and packages into your home folder (no admin needed)
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv into ~/.local/bin ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# 2. A separate Python 3.12 environment just for training (your Python 3.14 is untouched)
VENV="$WORK/venv"
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
  uv venv --python 3.12 "$VENV"
fi
STAMP="$VENV/.deps-$(shasum "$HERE/requirements.txt" | cut -c1-12)"
if [ ! -f "$STAMP" ]; then
  echo "Installing training packages (first run only, a few minutes)..."
  uv pip install --python "$PY" -r "$HERE/requirements.txt"
  uv pip install --python "$PY" --no-deps piper-tts
  touch "$STAMP"
fi

# 3. Train, keeping the Mac awake (the screen may still turn off) and logging to a file
KEEP_AWAKE=()
if command -v caffeinate >/dev/null 2>&1; then KEEP_AWAKE=(caffeinate -ims); fi
echo "Log: $WORK/train.log"
${KEEP_AWAKE[@]+"${KEEP_AWAKE[@]}"} "$PY" -u "$HERE/train_mac.py" --work "$WORK" "$@" 2>&1 | tee -a "$WORK/train.log"
