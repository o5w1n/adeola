# Training the "Adeola" wake word

Adeola listens for her name with [openWakeWord](https://github.com/dscripka/openWakeWord).
There's no ready-made "Adeola" model, so you train one once, for free, on Google Colab.
Until you do, `--voice` uses the built-in **"hey jarvis"** model so you can test everything.

## 1. Train

**Easiest: on a Mac.** One Terminal command, no Colab disconnects. See
[tools/wakeword_mac/README.md](../tools/wakeword_mac/README.md):

```bash
bash tools/wakeword_mac/run.sh
```

**Or on Google Colab** (about 2–3 hours, mostly waiting):

Use the adapted notebook in this repo, **`tools/train_adeola_wakeword.ipynb`**. It's based on the
maintained [openwakeword-colab-2026](https://github.com/alfiedennen/openwakeword-colab-2026)
notebook, with fixes for the problems we hit:

- Downloads about 3 GB instead of about 25 GB. The full-size download is where free Colab kept
  disconnecting, and training only ever used a slice of it.
- Runs on a **CPU** runtime, so it doesn't use your free GPU allowance (a GPU is faster if you have one).
- Settings already filled in for Adeola.
- Saves `adeola.onnx` to your Google Drive as well as downloading it.

Steps:

1. Go to [colab.research.google.com](https://colab.research.google.com), then **File → Upload notebook**,
   and choose `tools/train_adeola_wakeword.ipynb` from your project folder.
2. **Runtime → Change runtime type**:
   - **Runtime version: 2026.07**, not "Latest". Latest is Python 3.13, which the speech packages
     don't support yet. Connecting to an older version takes 2–3 minutes.
   - **Hardware accelerator: CPU** (or T4 GPU if you have allowance).
3. **Runtime → Run all.** The first cell asks for Google Drive access, so approve it. Then keep the
   tab open and in front. Free Colab may disconnect a background tab or show an "are you still
   there?" prompt.
4. When it finishes, `adeola.onnx` is in your Drive at `MyDrive/adeola.onnx`.

The wake phrase is set as `['ah day ola', 'ah day oh la']`: her name spelled with ordinary English
words so the training voices say AH-day-OH-luh. Don't change it to "adeola": words outside the
English dictionary crash the notebook with `No module named 'dp'`.

### If a cell fails

- **`No matching distribution found for piper-phonemize-cross`** or **`No module named 'piper_phonemize'`**:
  the runtime is Python 3.13. Set Runtime version to 2026.07 and Run all again.
- **"Cannot connect to GPU backend"**: your free GPU allowance is used up for now (it resets after
  a day or so). Choose CPU instead.
- **Disconnected**: Colab wiped the session, so use Run all again. The downloads are now small, so
  a retry costs minutes, not half an hour.
- Anything else: copy the error and the cell number.

## 2. Install

Put `adeola.onnx` in the project's `models/` folder (next to the Kokoro files), then:

```powershell
python -m tests.wake_check
```

Say "Adeola" several times, then talk normally for a minute. Each utterance prints its peak score.
You want "Adeola" to score clearly above `WAKE_THRESHOLD` (0.5) and everything else well below it.

- Fires on other words or by itself → raise `WAKE_THRESHOLD` to 0.6–0.7 in `config.py`.
- Misses you → lower it to 0.4, or retrain with a spelling closer to how you say it.

Then `python main.py --voice -v` and say "Adeola, open Brave". Holding Right Ctrl still works too.

## How it behaves

- After the wake word, Adeola beeps and records until you've been quiet for 0.8 s
  (`END_SILENCE_SECONDS`). Say nothing for 4 s and she gives up.
- You can say it in one breath ("Adeola, open Brave") or pause after her name.
- The wake word is ignored while she's talking and for a moment after, so she can't trigger
  herself by saying her own name. Hold Right Ctrl to interrupt her.
- `models/` is ignored by git, so your trained model stays on your machine.
