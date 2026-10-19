# Training the "Adeola" wake word

Adeola listens for her name with [openWakeWord](https://github.com/dscripka/openWakeWord).
There's no ready-made "Adeola" model, so you train one once, for free, on Google Colab.
Until you do, `--voice` uses the built-in **"hey jarvis"** model so you can test everything.

## 1. Train (about 1.5–2.5 hours, mostly waiting)

The original openWakeWord Colab notebook no longer runs, so use the maintained 2026 version:
**https://github.com/alfiedennen/openwakeword-colab-2026**

1. Open `train_wakeword.ipynb` there and click **Open in Colab**.
2. **Runtime → Change runtime type → GPU.** The free T4 works (about 2.5 h); an L4 on Colab Pro
   takes about 75–90 min.
3. In the cell with the settings (cell 10), set:
   ```python
   TARGET_PHRASE = 'ahdeh ohla'
   MODEL_NAME = 'adeola'
   ```
   The phrase is spelled the way you *say* it, not the way it's written. Training uses
   computer-generated voices, and "adeola" spelled normally comes out as "uh-DEE-oh-luh".
   "ahdeh ohla" comes out as "AH-day-OH-luh". If you say it differently, adjust the spelling.
4. **Runtime → Run all**, and keep the tab open and in front. On the free tier Colab can
   disconnect a background tab.
5. The last cell downloads **`adeola.onnx`**.

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
