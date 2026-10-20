# Training the "Adeola" wake word

Adeola listens for her name with [openWakeWord](https://github.com/dscripka/openWakeWord).
There's no ready-made "Adeola" model, so you train one once, for free, on Google Colab.
Until you do, `--voice` uses the built-in **"hey jarvis"** model so you can test everything.

## 1. Train (about 1.5–2.5 hours, mostly waiting)

The original openWakeWord Colab notebook no longer runs, so use the maintained 2026 version:
**https://github.com/alfiedennen/openwakeword-colab-2026**

1. Open `train_wakeword.ipynb` there and click **Open in Colab**.
2. **Runtime → Change runtime type**, then set both:
   - **Hardware accelerator: GPU** (T4 GPU on the free tier, about 2.5 h; an L4 on Colab Pro
     takes about 75–90 min). Without it the first cell shows `cuda: False`.
   - **Runtime version: 2026.07**, not "Latest". The latest runtime uses Python 3.13, and the
     notebook's speech packages only exist up to Python 3.12, so installs fail with
     `No matching distribution found for piper-phonemize-cross`. Connecting to an older version
     takes 2–3 minutes. Check the first cell prints `Python: 3.12...` and `cuda: True`.
3. In the settings cell (cell 20, marked "EDIT THESE TWO LINES"), set exactly:
   ```python
   TARGET_PHRASE = ['ah day ola', 'ah day oh la']
   MODEL_NAME    = 'adeola'
   ```
   - It must be a **list** (square brackets), even for one phrase.
   - The phrases are spelled the way you *say* her name (AH-day-OH-luh), using only ordinary
     English words. Training uses computer-generated voices, which would read "adeola" as
     "uh-DEE-oh-luh".
   - Every word must be in the English pronunciation dictionary. A made-up word like "adeola"
     or "ahdeh" makes the notebook crash with `No module named 'dp'`
     ([known bug](https://github.com/alfiedennen/openwakeword-colab-2026/issues/1)).
4. **Runtime → Run all**, and keep the tab open and in front. On the free tier Colab can
   disconnect a background tab.
5. The last cell downloads **`adeola.onnx`**.

### If a cell fails

- **`No module named 'dp'`**: your phrase has a word outside the dictionary. Use the phrases above,
  then add a new cell and run `!rm -rf /content/adeola_output`, and run again from cell 20 down.
  Cells 1–19 (installs and dataset downloads) don't need repeating unless the runtime restarted.
- **`No matching distribution found for piper-phonemize-cross`** or **`No module named 'piper_phonemize'`**:
  the runtime is Python 3.13. Set Runtime version to 2026.07 (step 2) and Run all again.
- **`cuda: False`** in the first cell: no GPU selected (step 2).
- **Runtime disconnected**: Colab wiped everything, so use Run all again.
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
