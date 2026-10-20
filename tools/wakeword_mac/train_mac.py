#!/usr/bin/env python3
"""Train a custom openWakeWord model on a Mac (or any machine without Colab).

A port of the openwakeword-colab-2026 notebook (MIT, Alfie Dennen), which itself
drives openWakeWord's own train.py. Every step checks its own output first, so if
the run is interrupted, running it again picks up where it stopped.

Usually started by run.sh, which sets up Python 3.12 and keeps the Mac awake:
    tools/wakeword_mac/run.sh                      # "Adeola" (default)
    tools/wakeword_mac/run.sh --name kiara --phrase "kee ah ruh" --phrase "key ah ruh"
"""

from __future__ import annotations

import argparse
import copy
import math
import os
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))           # for lean_download.py

# Pinned so an upstream change can't break a long run.
OWW_SHA = "368c03716d1e92591906a84949bc477f3a834455"   # dscripka/openWakeWord main, 2026-10
PSG_SHA = "c9d824c0e2cce8bdeb000c219dc9cbc84df086ea"   # rhasspy/piper-sample-generator, the notebook's pin
LIBRITTS_URL = ("https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/"
                "en_US-libritts_r-medium.pt")
FEATURE_MODELS_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
RIR_REPO = "davidscripka/MIT_environmental_impulse_responses"
SR = 16000

DEFAULT_NAME = "adeola"
DEFAULT_PHRASES = ["ah day ola", "ah day oh la"]   # AH-day-OH-luh, dictionary words only


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------
class Step:
    """Prints a header and how long the step took."""
    n = 0

    def __init__(self, title):
        Step.n += 1
        self.title = title

    def __enter__(self):
        self.t0 = time.time()
        print(f"\n[{Step.n}] {self.title}", flush=True)
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            print(f"    done in {(time.time() - self.t0) / 60:.1f} min", flush=True)


def download(url: str, dest: Path, min_bytes: int = 1000) -> Path:
    if dest.exists() and dest.stat().st_size >= min_bytes:
        print(f"    cached: {dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(5):
        try:
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(4 << 20):
                        f.write(chunk)
            break
        except requests.RequestException as e:
            wait = 10 * (attempt + 1)
            print(f"    download failed ({e.__class__.__name__}), retrying in {wait}s")
            time.sleep(wait)
    else:
        raise RuntimeError(f"Could not download {url}")
    tmp.replace(dest)
    print(f"    downloaded: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def fetch_repo(owner_repo: str, sha: str, dest: Path, must_exist: str) -> None:
    if (dest / must_exist).exists():
        print(f"    cached: {dest.name}")
        return
    shutil.rmtree(dest, ignore_errors=True)
    try:
        tgz = download(f"https://codeload.github.com/{owner_repo}/tar.gz/{sha}",
                       dest.parent / f"{dest.name}.tar.gz")
    except RuntimeError:
        if not shutil.which("git"):
            raise
        print("    tarball download failed; trying git")
        subprocess.run(["git", "clone", "-q", f"https://github.com/{owner_repo}", str(dest)], check=True)
        subprocess.run(["git", "-C", str(dest), "checkout", "-q", sha], check=True)
        assert (dest / must_exist).exists(), f"{owner_repo} is missing {must_exist}"
        print(f"    fetched {owner_repo} @ {sha[:7]} with git")
        return
    with tarfile.open(tgz) as t:
        top = t.getmembers()[0].name.split("/")[0]
        t.extractall(dest.parent, filter="data")
    (dest.parent / top).rename(dest)
    tgz.unlink()
    assert (dest / must_exist).exists(), f"{owner_repo} is missing {must_exist}"
    print(f"    fetched {owner_repo} @ {sha[:7]}")


def patch_file(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"    {label}: already applied")
    elif old in text:
        path.write_text(text.replace(old, new), encoding="utf-8")
        print(f"    {label}: applied")
    else:
        print(f"    {label}: not needed (pattern not found)")


def env_with_oww(work: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(work / "openwakeword"),
                                                      env.get("PYTHONPATH", "")]))
    env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"   # any op the Mac GPU lacks runs on CPU instead
    env.setdefault("HF_HUB_ETAG_TIMEOUT", "120")
    env.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
    return env


def run_train_py(work: Path, cfg_path: Path, flag: str) -> None:
    cmd = [sys.executable, str(work / "openwakeword/openwakeword/train.py"),
           "--training_config", str(cfg_path), flag]
    print("    $ " + " ".join(cmd[1:]), flush=True)
    subprocess.run(cmd, check=True, env=env_with_oww(work), cwd=str(work))


def count(folder: Path, suffix: str) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for p in folder.rglob(f"*{suffix}"))


# -----------------------------------------------------------------------------
# steps
# -----------------------------------------------------------------------------
def check_phrases(phrases: list[str]) -> None:
    import pronouncing
    missing = [w for p in phrases for w in p.lower().split() if not pronouncing.phones_for_word(w)]
    if missing:
        sys.exit(f"Not in the pronunciation dictionary: {missing}\n"
                 "Spell the name with ordinary English words, e.g. Adeola -> 'ah day ola', "
                 "Kiara -> 'kee ah ruh'. (Words outside the dictionary make openWakeWord need an "
                 "extra phonemizer that isn't installed.)")
    print(f"    all words are in the dictionary: {phrases}")


def fetch_code_and_models(work: Path) -> None:
    fetch_repo("dscripka/openWakeWord", OWW_SHA, work / "openwakeword", "openwakeword/train.py")
    fetch_repo("rhasspy/piper-sample-generator", PSG_SHA, work / "piper-sample-generator",
               "generate_samples.py")
    download(LIBRITTS_URL, work / "piper-sample-generator/models/en_US-libritts_r-medium.pt",
             min_bytes=100_000_000)
    res = work / "openwakeword/openwakeword/resources/models"
    for name in ("embedding_model.onnx", "melspectrogram.onnx",
                 "embedding_model.tflite", "melspectrogram.tflite"):
        download(f"{FEATURE_MODELS_URL}/{name}", res / name)


TORCHAUDIO_SHIM_MARKER = "# -- PATCH (wake-word trainer): soundfile-based info()/load() --"
TORCHAUDIO_SHIM = f'''

{TORCHAUDIO_SHIM_MARKER}
# torchaudio 2.9+ removed info() and moved load() to torchcodec. Every file the
# wake-word trainer reads is a WAV, so soundfile covers both.
def info(file_path, *args, **kwargs):
    import soundfile as _sf
    si = _sf.info(str(file_path))
    return type("_TorchaudioInfo", (), {{
        "num_frames": si.frames, "sample_rate": si.samplerate,
        "num_channels": si.channels, "bits_per_sample": 16, "encoding": "PCM_S",
    }})()


def load(uri, frame_offset=0, num_frames=-1, normalize=True, channels_first=True, *args, **kwargs):
    import soundfile as _sf
    import torch as _torch
    data, sr = _sf.read(str(uri), start=frame_offset, frames=num_frames,
                        dtype="float32", always_2d=True)
    data = data.T if channels_first else data
    return _torch.from_numpy(data.copy()), sr
'''


def apply_patches(work: Path) -> None:
    import importlib.util
    site = Path(sysconfig.get_paths()["purelib"])

    # A: torch_audiomentations calls torchaudio.set_audio_backend, removed in torchaudio 2.x
    spec = importlib.util.find_spec("torch_audiomentations")
    io_py = Path(spec.origin).parent / "utils/io.py"
    if io_py.exists():
        patch_file(io_py, 'torchaudio.set_audio_backend("soundfile")', "pass  # patched",
                   "A torch_audiomentations set_audio_backend")

    # B: torchaudio.info/load via soundfile (no torchcodec/FFmpeg needed)
    init = Path(importlib.util.find_spec("torchaudio").origin)
    text = init.read_text(encoding="utf-8")
    if TORCHAUDIO_SHIM_MARKER not in text:
        init.write_text(text + TORCHAUDIO_SHIM, encoding="utf-8")
        print("    B torchaudio info()/load() shim: applied")
    else:
        print("    B torchaudio info()/load() shim: already applied")

    # C: replace the unmaintained `acoustics` package with the one function used
    pkg = site / "acoustics"
    if pkg.exists() and not (pkg / "_wakeword_shim").exists():
        shutil.rmtree(pkg)
    pkg.mkdir(exist_ok=True)
    shutil.copy(HERE / "acoustics_shim.py", pkg / "__init__.py")
    (pkg / "_wakeword_shim").touch()
    print("    C acoustics noise-generator shim: installed")

    # D: generate_samples needs a default model path (train.py doesn't pass one), and
    #    train.py imports it from the openwakeword package directory
    psg = work / "piper-sample-generator"
    gs = psg / "generate_samples.py"
    model_path = psg / "models/en_US-libritts_r-medium.pt"
    patch_file(gs, "model: Union[str, Path],", f'model: Union[str, Path] = "{model_path}",',
               "D generate_samples default model")
    shutil.copy(gs, work / "openwakeword/openwakeword/generate_samples.py")

    # E: train.py validation dtype
    patch_file(work / "openwakeword/openwakeword/train.py",
               "val_predictions = self.model(x_val)\n",
               "val_predictions = self.model(x_val.float())\n", "E train.py val dtype")


def preflight(work: Path) -> None:
    code = ("import torch, torchaudio, torchinfo, torchmetrics, scipy, numpy, yaml, mutagen, "
            "pronouncing, audiomentations, torch_audiomentations, speechbrain, acoustics, onnx, "
            "onnxruntime, soundfile, huggingface_hub, piper\n"
            "from piper import PiperVoice, SynthesisConfig\n"
            "from piper.phonemize_espeak import EspeakPhonemizer\n"
            "import openwakeword\n"
            "from openwakeword.data import generate_adversarial_texts, augment_clips, mmap_batch_generator\n"
            "from openwakeword.utils import compute_features_from_generator, AudioFeatures\n"
            "assert hasattr(torchaudio, 'info') and hasattr(torchaudio, 'load')\n"
            "acoustics.generator.noise(1000, color='pink')\n"
            "mps = getattr(torch.backends, 'mps', None)\n"
            "print(f'    torch {torch.__version__}, torchaudio {torchaudio.__version__}, '\n"
            "      f'Apple GPU: {bool(mps and mps.is_available())}')\n")
    subprocess.run([sys.executable, "-c", code], check=True, env=env_with_oww(work), cwd=str(work))
    print("    all imports OK")


def fetch_rirs(work: Path) -> None:
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    out = work / "mit_rirs"
    if count(out, ".wav") >= 250:
        print(f"    cached: {count(out, '.wav')} room impulse responses")
        return
    from huggingface_hub import snapshot_download
    src = Path(snapshot_download(repo_id=RIR_REPO, repo_type="dataset",
                                 local_dir=str(work / "mit_rirs_src"), max_workers=4))
    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in (".wav", ".flac"))
    if not files:
        raise RuntimeError(f"No audio files found in {src}")
    out.mkdir(exist_ok=True)
    for i, p in enumerate(files):
        data, sr = sf.read(str(p), dtype="float32", always_2d=True)
        data = data.mean(axis=1)
        if sr != SR:
            data = resample_poly(data, SR, sr)
        sf.write(str(out / f"{i:04d}.wav"), np.clip(data, -1, 1), SR, subtype="PCM_16")
    print(f"    wrote {len(files)} room impulse responses")


def fetch_negatives(work: Path, n_fma: int) -> None:
    import lean_download as L
    L.fetch_acav(str(work / "acav_train_subset.npy"), str(work / "acav_val_subset.npy"))
    if count(work / "fma_wav", ".wav") >= n_fma:
        print(f"    cached: {count(work / 'fma_wav', '.wav')} background-music WAVs")
        return
    L.fetch_fma(str(work / "fma"), n_fma)


def convert_fma(work: Path, n_fma: int) -> None:
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from tqdm import tqdm
    out = work / "fma_wav"
    out.mkdir(exist_ok=True)
    if count(out, ".wav") >= n_fma:
        print(f"    cached: {count(out, '.wav')} WAVs")
        return
    mp3s = sorted((work / "fma").rglob("*.mp3"))[:n_fma]
    if len(mp3s) < 100:
        raise RuntimeError(f"Only {len(mp3s)} background-music MP3s found")
    ffmpeg = shutil.which("ffmpeg")
    failed = 0
    for i, mp3 in enumerate(tqdm(mp3s, desc="mp3 -> wav")):
        dst = out / f"{i:05d}.wav"
        if dst.exists():
            continue
        try:
            data, sr = sf.read(str(mp3), dtype="float32", always_2d=True)   # libsndfile >= 1.1 reads MP3
            data = data.mean(axis=1)
            if sr != SR:
                data = resample_poly(data, SR, sr)
            sf.write(str(dst), np.clip(data, -1, 1), SR, subtype="PCM_16")
        except Exception:
            if ffmpeg:
                subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(mp3),
                                "-ac", "1", "-ar", str(SR), str(dst)], check=False)
            if not dst.exists():
                failed += 1   # a few FMA tracks are known to be corrupt
    print(f"    {count(out, '.wav')} WAVs ready ({failed} unreadable tracks skipped)")


def write_config(work: Path, name: str, phrases: list[str], n_samples: int, steps: int) -> Path:
    import yaml
    out = work / f"{name}_output"
    feat = out / name
    cfg = {
        "target_phrase": phrases,
        "model_name": name,
        "custom_negative_phrases": [],
        "n_samples": n_samples,
        "n_samples_val": max(1, n_samples // 2),
        "tts_batch_size": 50,
        "piper_sample_generator_path": str(work / "piper-sample-generator"),
        "augmentation_rounds": 1,
        "augmentation_batch_size": 16,
        "steps": steps,
        "max_negative_weight": 1500,
        "target_accuracy": 0.7,
        "target_recall": 0.5,
        "target_false_positives_per_hour": 0.5,
        "batch_size": 128,
        "learning_rate": 1e-4,
        "model_type": "dnn",
        "layer_dim": 128,
        "layer_size": 128,
        "n_blocks": 1,
        "model_input_shape": [16, 96],
        "n_classes": 1,
        "batch_n_per_class": {"ACAV100M_sample": 1024, "adversarial_negative": 50, "positive": 50},
        "background_paths": [str(work / "fma_wav")],
        "background_paths_duplication_rate": [1],
        "rir_paths": [str(work / "mit_rirs")],
        "false_positive_validation_data_path": str(work / "acav_val_subset.npy"),
        "feature_data_files": {"ACAV100M_sample": str(work / "acav_train_subset.npy")},
        "output_dir": str(out),
        "tflite_export": False,
        "onnx_export": True,
        "positive_clips_train_dir": str(feat / "positive_train"),
        "positive_clips_test_dir": str(feat / "positive_test"),
        "negative_clips_train_dir": str(feat / "negative_train"),
        "negative_clips_test_dir": str(feat / "negative_test"),
        "feature_save_dir": str(feat),
    }
    out.mkdir(parents=True, exist_ok=True)
    path = work / f"{name}_config.yaml"
    old = yaml.safe_load(path.read_text()) if path.exists() else None
    if old and (old["target_phrase"] != phrases or old["n_samples"] != n_samples):
        print("    settings changed since the last run: clearing old clips and features")
        shutil.rmtree(out)
        out.mkdir(parents=True)
    path.write_text(yaml.dump(cfg, sort_keys=False))
    print(f"    {name}: {phrases}, {n_samples} samples, {steps} steps")
    return path


def clip_dirs(cfg: dict) -> dict:
    return {
        "positive_train": (Path(cfg["positive_clips_train_dir"]), int(cfg["n_samples"] * 0.75)),
        "positive_test": (Path(cfg["positive_clips_test_dir"]), int(cfg["n_samples_val"] * 0.75)),
        "negative_train": (Path(cfg["negative_clips_train_dir"]), int(cfg["n_samples"] * 0.75)),
        "negative_test": (Path(cfg["negative_clips_test_dir"]), int(cfg["n_samples_val"] * 0.75)),
    }


def generate_clips(work: Path, cfg_path: Path, cfg: dict) -> None:
    dirs = clip_dirs(cfg)
    have = {k: count(p, ".wav") for k, (p, _) in dirs.items()}
    if all(have[k] >= n for k, (_, n) in dirs.items()):
        print(f"    cached: {have}")
        return
    run_train_py(work, cfg_path, "--generate_clips")
    for k, (p, n) in dirs.items():
        got = count(p, ".wav")
        assert got >= n, f"clip generation left {k} with {got} clips (expected at least {n})"
    print(f"    clips: { {k: count(p, '.wav') for k, (p, _) in dirs.items()} }")


def resample_clips(cfg: dict) -> None:
    import soundfile as sf
    from scipy.signal import resample_poly
    from tqdm import tqdm
    root = Path(cfg["output_dir"])
    changed = False
    for d in sorted({p.parent for p in root.rglob("*.wav")}):
        files = sorted(d.glob("*.wav"))
        if all(sf.info(str(f)).samplerate == SR for f in files[:20]):
            continue
        for f in tqdm(files, desc=f"resample {d.name}", leave=False):
            data, sr = sf.read(str(f), dtype="float32")
            if sr != SR:
                sf.write(str(f), resample_poly(data, SR, sr), SR)
                changed = True
    if changed:   # features made from the old sample rate are stale
        for f in root.rglob("*.npy"):
            f.unlink()
    print(f"    all clips at {SR} Hz{' (resampled; old features cleared)' if changed else ''}")


FEATURE_FILES = ["positive_features_train.npy", "negative_features_train.npy",
                 "positive_features_test.npy", "negative_features_test.npy"]


def augment(work: Path, cfg_path: Path, cfg: dict) -> None:
    feat = Path(cfg["feature_save_dir"])
    if all((feat / n).exists() for n in FEATURE_FILES):
        print("    cached: all 4 feature files")
        return
    run_train_py(work, cfg_path, "--augment_clips")
    for n in FEATURE_FILES:
        assert (feat / n).exists(), f"augmentation didn't produce {n}"
        print(f"    {n}: {(feat / n).stat().st_size / 1e6:.1f} MB")


# -- training: the notebook's hand-rolled trainer, unchanged except that validation
#    windows are built chunk by chunk instead of all at once (that needed ~5 GB RAM) --
def train_and_export(cfg: dict, out_file: Path) -> Path:
    import numpy as np
    import torch
    import torch.nn as nn
    import torch.optim as optim

    torch.set_num_threads(max(1, os.cpu_count() or 1))
    device = "cpu"   # the model is tiny; CPU is fast and avoids GPU-specific surprises
    feat = Path(cfg["feature_save_dir"])
    load = lambda n: torch.from_numpy(np.load(feat / n).astype(np.float32)).to(device)  # noqa: E731
    pos_train, neg_train = load("positive_features_train.npy"), load("negative_features_train.npy")
    pos_test, neg_test = load("positive_features_test.npy"), load("negative_features_test.npy")
    print(f"    pos_train={tuple(pos_train.shape)} neg_train={tuple(neg_train.shape)} "
          f"pos_test={tuple(pos_test.shape)} neg_test={tuple(neg_test.shape)}")

    acav_train = np.load(cfg["feature_data_files"]["ACAV100M_sample"], mmap_mode="r")
    acav_val = np.load(cfg["false_positive_validation_data_path"], mmap_mode="r")
    M = acav_val.shape[0]
    val_hours = M * 0.08 / 3600.0
    n_win_val = M - 16 + 1
    win_view = np.lib.stride_tricks.sliding_window_view(acav_val, (16, 96))[:, 0, :, :]
    VAL_BATCH = 4096
    print(f"    ACAV train {acav_train.shape}, validation {val_hours:.2f} h of audio")

    class WakewordModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.flatten = nn.Flatten()
            self.layer1 = nn.Linear(16 * 96, 128)
            self.layernorm1 = nn.LayerNorm(128)
            self.relu1 = nn.ReLU()
            self.layer2 = nn.Linear(128, 1)

        def forward(self, x):
            return self.layer2(self.relu1(self.layernorm1(self.layer1(self.flatten(x)))))

    model = WakewordModel().to(device)
    loss_fn = nn.BCEWithLogitsLoss(reduction="none")
    THRESH = 0.5
    history = {"val_recall": [], "val_accuracy": [], "val_fp_per_hour": [], "val_n_fp": [], "loss": []}
    best_models = []
    B_POS, B_ANEG, B_ACAV = 32, 32, 64

    def acav_batch(k):
        N, T, F = acav_train.shape
        rows = np.random.randint(0, N, size=k)
        starts = np.random.randint(0, T - 16 + 1, size=k)
        out = np.empty((k, 16, F), dtype=np.float32)
        for i, (r, s) in enumerate(zip(rows, starts)):
            out[i] = acav_train[r, s:s + 16, :].astype(np.float32)
        return out

    def build_batch():
        p = pos_train[torch.randint(0, pos_train.shape[0], (B_POS,))]
        an = neg_train[torch.randint(0, neg_train.shape[0], (B_ANEG,))]
        x = torch.cat([p, an, torch.from_numpy(acav_batch(B_ACAV))], dim=0)
        y = torch.cat([torch.ones(B_POS), torch.zeros(B_ANEG + B_ACAV)])
        return x, y

    @torch.no_grad()
    def validate(label):
        model.eval()
        p_preds = torch.sigmoid(model(pos_test)).squeeze(-1)
        n_preds = torch.sigmoid(model(neg_test)).squeeze(-1)
        recall = (p_preds >= THRESH).float().mean().item()
        accuracy = (((p_preds >= THRESH).sum() + (n_preds < THRESH).sum()).item()
                    / (pos_test.shape[0] + neg_test.shape[0]))
        n_fp = 0
        for i in range(0, n_win_val, VAL_BATCH):
            chunk = torch.from_numpy(np.ascontiguousarray(win_view[i:i + VAL_BATCH], dtype=np.float32))
            n_fp += (torch.sigmoid(model(chunk)).squeeze(-1) >= THRESH).sum().item()
        fp_h = n_fp / max(val_hours, 1e-6)
        for k, v in (("val_recall", recall), ("val_accuracy", accuracy),
                     ("val_fp_per_hour", fp_h), ("val_n_fp", n_fp)):
            history[k].append(v)
        saved = False
        if len(history["val_n_fp"]) >= 3:
            if n_fp <= np.percentile(history["val_n_fp"], 50) and \
               recall >= np.percentile(history["val_recall"], 5):
                best_models.append((copy.deepcopy(model.state_dict()),
                                    {"val_recall": recall, "val_accuracy": accuracy,
                                     "val_fp_per_hour": fp_h, "val_n_fp": n_fp}))
                saved = True
        print(f"    [{label}] recall={recall:.3f} acc={accuracy:.3f} fp/hr={fp_h:.2f} "
              f"saved={'+' if saved else '-'}", flush=True)
        model.train()

    def run_stage(idx, n_steps, lr, max_neg_w, val_window_frac=1.0):
        optimizer = optim.Adam(model.parameters(), lr=lr)
        weights = np.linspace(1.0, max_neg_w, n_steps)
        val_start = int(n_steps * (1.0 - val_window_frac))
        val_steps = set(np.linspace(val_start, n_steps - 1, 20).astype(int))
        warmup, hold = max(1, n_steps // 5), n_steps // 3
        acc = []
        print(f"\n    === stage {idx}: {n_steps} steps, lr={lr}, max_neg_w={max_neg_w} ===", flush=True)
        t0 = time.time()
        for step in range(n_steps):
            if step < warmup:
                lr_now = lr * (step + 1) / warmup
            elif step < warmup + hold:
                lr_now = lr
            else:
                t = (step - warmup - hold) / max(1, n_steps - warmup - hold)
                lr_now = lr * 0.5 * (1.0 + math.cos(math.pi * min(1.0, t)))
            for pg in optimizer.param_groups:
                pg["lr"] = lr_now
            x, y = build_batch()
            logits = model(x).squeeze(-1)
            preds = torch.sigmoid(logits)
            keep = ((y == 0) & (preds >= 0.001)) | ((y == 1) & (preds < 0.999))
            if keep.sum() > 0:
                w = torch.where(y[keep] > 0.5, torch.tensor(1.0),
                                torch.tensor(float(weights[step])))
                acc.append((logits[keep], y[keep], w))
                if sum(t[0].shape[0] for t in acc) >= 128:
                    loss = (loss_fn(torch.cat([t[0] for t in acc]), torch.cat([t[1] for t in acc]))
                            * torch.cat([t[2] for t in acc])).mean()
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()
                    history["loss"].append(loss.item())
                    acc.clear()
            if step in val_steps:
                validate(f"stage {idx} step {step}/{n_steps}, {(time.time() - t0) / 60:.1f} min, "
                         f"neg_w={weights[step]:.0f}")

    total, max_w, target_fp = cfg["steps"], cfg["max_negative_weight"], cfg["target_false_positives_per_hour"]
    run_stage(1, total, 1e-4, max_w, val_window_frac=0.25)
    if history["val_fp_per_hour"] and min(history["val_fp_per_hour"]) > target_fp:
        max_w *= 2
        print(f"    [adapt] too many false alarms: negative weight doubled to {max_w}")
    run_stage(2, max(2000, total // 10) if total >= 2000 else max(1, total // 10), 1e-5, max_w)
    if history["val_fp_per_hour"] and min(history["val_fp_per_hour"]) > target_fp:
        max_w *= 2
        print(f"    [adapt] too many false alarms: negative weight doubled to {max_w}")
    run_stage(3, max(2000, total // 10) if total >= 2000 else max(1, total // 10), 1e-6, max_w)

    # -- ensemble the best checkpoints, then export (same as the notebook) --
    if not best_models:
        final = {k: v.clone() for k, v in model.state_dict().items()}
        print("    no checkpoints saved; using the final model")
    else:
        accs = [s["val_accuracy"] for _, s in best_models]
        rcs = [s["val_recall"] for _, s in best_models]
        fps = [s["val_fp_per_hour"] for _, s in best_models]
        a90, r90, f10 = np.percentile(accs, 90), np.percentile(rcs, 90), np.percentile(fps, 10)
        q = [(sd, s) for sd, s in best_models if s["val_accuracy"] >= a90
             and s["val_recall"] >= r90 and s["val_fp_per_hour"] <= f10]
        if not q:
            q = [sorted(best_models, key=lambda t: (t[1]["val_fp_per_hour"], -t[1]["val_recall"]))[0]]
        final = {k: torch.stack([sd[k].float() for sd, _ in q]).mean(dim=0) for k in q[0][0]}
        print(f"    ensemble of {len(q)} checkpoint(s): recall={np.mean([s['val_recall'] for _, s in q]):.3f}, "
              f"fp/hr={np.mean([s['val_fp_per_hour'] for _, s in q]):.2f}")
    model.load_state_dict(final)
    model.eval()

    class Exportable(nn.Module):
        def __init__(self, base):
            super().__init__()
            self.base = base

        def forward(self, x):
            return torch.sigmoid(self.base(x))

    onnx_path = feat / f"{cfg['model_name']}.onnx"
    torch.onnx.export(Exportable(model), torch.randn(1, 16, 96), str(onnx_path),
                      input_names=["onnx::Flatten_0"], output_names=["output"],
                      dynamic_axes={"onnx::Flatten_0": {0: "batch"}, "output": {0: "batch"}},
                      opset_version=14, dynamo=False)

    import onnxruntime as ort
    sess = ort.InferenceSession(str(onnx_path))
    scores = sess.run(None, {sess.get_inputs()[0].name: pos_test.numpy()})[0].flatten()
    print(f"    exported {onnx_path.name} ({onnx_path.stat().st_size / 1e3:.0f} KB); "
          f"recall on held-out clips at 0.5: {(scores >= 0.5).mean():.3f}")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(onnx_path, out_file)
    return out_file


# -----------------------------------------------------------------------------
def main() -> None:
    import yaml
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default=DEFAULT_NAME, help="model name, also the output file name")
    ap.add_argument("--phrase", action="append", help="wake phrase spelled in dictionary words; repeatable")
    ap.add_argument("--work", type=Path, default=Path.home() / "wakeword_work",
                    help="folder for downloads and intermediate files (about 5 GB)")
    ap.add_argument("--out", type=Path, default=None, help="where to put the finished .onnx")
    ap.add_argument("--n-samples", type=int, default=2000, help="synthetic clips per set (default 2000)")
    ap.add_argument("--steps", type=int, default=20000, help="training steps (default 20000)")
    ap.add_argument("--n-fma", type=int, default=1500, help="background-music tracks (default 1500)")
    ap.add_argument("--stop-after", default=None, help=argparse.SUPPRESS)  # for testing
    args = ap.parse_args()

    phrases = args.phrase or (DEFAULT_PHRASES if args.name == DEFAULT_NAME else None)
    if not phrases:
        sys.exit("Give the wake phrase with --phrase, e.g. --phrase \"kee ah ruh\"")
    work = args.work.expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    out = (args.out or Path.home() / "Desktop" / f"{args.name}.onnx").expanduser()
    t_start = time.time()
    print(f"Training wake word '{args.name}' {phrases}\nWorking folder: {work}")

    steps = [
        ("Check the wake phrase", lambda: check_phrases(phrases)),
        ("Download training code and voice model (~250 MB)", lambda: fetch_code_and_models(work)),
        ("Apply compatibility fixes", lambda: apply_patches(work)),
        ("Check everything imports", lambda: preflight(work)),
        ("Room impulse responses (~8 MB)", lambda: fetch_rirs(work)),
        ("Negative examples: speech features + background music (~3 GB)",
         lambda: fetch_negatives(work, args.n_fma)),
        ("Convert background music to 16 kHz WAV", lambda: convert_fma(work, args.n_fma)),
    ]
    for title, fn in steps:
        with Step(title):
            fn()
        if args.stop_after and args.stop_after.lower() in title.lower():
            return

    with Step("Write training settings"):
        cfg_path = write_config(work, args.name, phrases, args.n_samples, args.steps)
        cfg = yaml.safe_load(cfg_path.read_text())
    with Step("Generate spoken examples with synthetic voices (slowest step)"):
        generate_clips(work, cfg_path, cfg)
    with Step("Resample clips to 16 kHz"):
        resample_clips(cfg)
    with Step("Augment clips and compute features"):
        augment(work, cfg_path, cfg)
    with Step("Train and export"):
        result = train_and_export(cfg, out)

    print(f"\nDone in {(time.time() - t_start) / 3600:.1f} h. Your wake word model: {result}")
    print("Copy it into Adeola's models/ folder (and set WAKE_MODEL in config.py if the name changed).")


if __name__ == "__main__":
    main()
