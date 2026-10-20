# Train the wake word on a Mac

A Terminal version of the Colab training notebook, for when Colab keeps disconnecting.
It runs on any Mac (Apple Silicon or Intel), needs no admin rights, leaves your own Python
alone, keeps the Mac awake while it works, and resumes where it stopped if interrupted.

## Run it

Open **Terminal**, go to the folder that contains `tools/` (the Adeola project, or the unzipped
trainer), and run:

```bash
bash tools/wakeword_mac/run.sh
```

That trains **Adeola** (wake phrase `ah day ola` / `ah day oh la`). For a different name:

```bash
bash tools/wakeword_mac/run.sh --name kiara --phrase "kee ah ruh" --phrase "key ah ruh"
```

The first run installs `uv` and a private Python 3.12 into your home folder, then the training
packages (a few minutes). Then it works through 12 steps and prints progress. Expect roughly
**1–2 hours**, mostly the ~3 GB download and generating the spoken examples.

When it finishes, the model is on your **Desktop** as `adeola.onnx` (or `<name>.onnx`). Copy it
into Adeola's `models/` folder on your Windows PC.

## Good to know

- **Interrupted?** Run the same command again. Finished steps are skipped.
- **Leave it plugged in.** It stops the Mac from sleeping while it runs (the screen can still turn
  off), but closing a MacBook lid will still sleep it.
- **Log:** everything is also written to `~/wakeword_work/train.log`.
- **Disk:** about 5 GB in `~/wakeword_work`. Delete that folder afterwards to get the space back.
- **Wake phrase rule:** every word must be an ordinary English word (the script checks this first).
  Spell the name the way it sounds: Adeola → `ah day ola`, Kiara → `kee ah ruh`.

## What's different from the Colab notebook

Same training recipe, same openWakeWord code (pinned versions), with fixes so it runs on a Mac:

- Python 3.12 via `uv` (the speech packages don't support 3.13+ yet); `torch`/`torchaudio` 2.11.
- Downloads only the data training uses (~3 GB instead of ~25 GB).
- `acoustics` (unmaintained, breaks on current SciPy) is replaced by an exact copy of the one
  noise function training uses.
- `torchaudio.load/info` are routed through `soundfile`, so no FFmpeg/torchcodec is needed.
- MP3s are decoded with `soundfile`, and repos are fetched without `git`, so there are no Homebrew
  or Xcode requirements.
- Validation builds its audio windows in chunks instead of all at once (~5 GB less memory).

The end-to-end pipeline was tested on Linux with Python 3.12 and the pinned packages: setup,
voice generation, augmentation, training, and loading the exported model in openWakeWord.
