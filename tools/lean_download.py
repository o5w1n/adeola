# Lean replacement for the wake-word notebook's "Download FMA + ACAV" step.
#
# The original downloads ~25 GB (17 GB ACAV features + 8 GB FMA zip), but training
# only ever uses the first 11% of ACAV rows and 1,500 FMA tracks. This streams just
# those bytes (about 3 GB) and stops, producing exactly the same files the later
# cells expect:
#   /content/acav_train_subset.npy   rows [0, N//10)              shape (n, 16, 96)
#   /content/acav_val_subset.npy     rows [N//10, N//10 + N//100) flattened to (m, 96)
#   /content/fma/**.mp3              1,500 tracks
# If the lean FMA path fails for any reason, it falls back to the full download.

import io
import os
import zipfile

import numpy as np
import requests

ACAV_URL = ("https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/"
            "openwakeword_features_ACAV100M_2000_hrs_16bit.npy")
FMA_URL = "https://os.unil.cloud.switch.ch/fma/fma_small.zip"
CHUNK = 4 * 1024 * 1024


def _progress(total, desc):
    try:
        from tqdm.auto import tqdm
        return tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024, desc=desc)
    except ImportError:
        class _Null:
            def update(self, n): pass
            def close(self): pass
        return _Null()


def parse_npy_header(buf: bytes):
    """Return (shape, dtype, data_offset) from the start of a .npy file, or None if buf is too short."""
    f = io.BytesIO(buf)
    try:
        version = np.lib.format.read_magic(f)
        reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                  else np.lib.format.read_array_header_2_0)
        shape, fortran, dtype = reader(f)
    except (ValueError, EOFError):
        return None
    if fortran:
        raise ValueError("Fortran-ordered .npy is not supported")
    return shape, dtype, f.tell()


def acav_subsets_from_stream(chunks, train_dst, val_dst, fraction_train=10, fraction_val=100):
    """Write the train/val slices from an iterator of byte chunks, then stop reading."""
    buf = b""
    header = None
    chunks = iter(chunks)
    while header is None:
        buf += next(chunks)
        header = parse_npy_header(buf) if len(buf) >= 16 else None
    shape, dtype, offset = header
    n_total = shape[0]
    row_bytes = int(np.prod(shape[1:])) * dtype.itemsize
    n_train, n_val = n_total // fraction_train, n_total // fraction_val
    print(f"  ACAV source shape {shape}, {dtype}; keeping {n_train} train + {n_val} val rows")

    train = np.lib.format.open_memmap(train_dst, mode="w+", dtype=dtype,
                                      shape=(n_train,) + tuple(shape[1:]))
    val = np.lib.format.open_memmap(val_dst, mode="w+", dtype=dtype,
                                    shape=(n_val * int(np.prod(shape[1:-1])), shape[-1]))
    targets = [train.reshape(-1).view(np.uint8), val.reshape(-1).view(np.uint8)]
    need = [n_train * row_bytes, n_val * row_bytes]

    data = memoryview(buf)[offset:]
    t, pos = 0, 0
    bar = _progress(sum(need), "ACAV (only the part used)")
    while t < 2:
        while len(data) and t < 2:
            take = min(len(data), need[t] - pos)
            targets[t][pos:pos + take] = np.frombuffer(data[:take], dtype=np.uint8)
            pos += take
            data = data[take:]
            bar.update(take)
            if pos == need[t]:
                t, pos = t + 1, 0
        if t < 2:
            try:
                data = memoryview(next(chunks))
            except StopIteration:
                raise RuntimeError("ACAV download ended early")
    bar.close()
    train.flush()
    val.flush()
    del train, val
    return n_train, n_val


def fetch_acav(train_dst="/content/acav_train_subset.npy", val_dst="/content/acav_val_subset.npy"):
    done = train_dst + ".done"
    if os.path.exists(done) and os.path.exists(val_dst):
        print("  cached: ACAV subsets")
        return
    with requests.get(ACAV_URL, stream=True, timeout=120) as r:
        r.raise_for_status()
        acav_subsets_from_stream(r.iter_content(CHUNK), train_dst, val_dst)
    open(done, "w").close()
    print(f"  ✓ ACAV train {os.path.getsize(train_dst)/1e9:.2f} GB, "
          f"val {os.path.getsize(val_dst)/1e6:.0f} MB")


def mp3s_from_zip_stream(chunks, out_dir, n_wanted):
    """Stream-unzip and keep the first n_wanted .mp3 files, then stop reading."""
    from stream_unzip import stream_unzip
    n = 0
    for name, _size, file_chunks in stream_unzip(chunks):
        name = name.decode("utf-8", "replace")
        if name.lower().endswith(".mp3") and n < n_wanted:
            path = os.path.join(out_dir, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                for c in file_chunks:
                    f.write(c)
            n += 1
            if n >= n_wanted:
                break
        else:
            for _ in file_chunks:   # must be consumed before the next entry
                pass
    return n


def count_mp3(folder):
    return sum(f.lower().endswith(".mp3") for _, _, fs in os.walk(folder) for f in fs)


def fetch_fma(out_dir="/content/fma", n_wanted=1500):
    os.makedirs(out_dir, exist_ok=True)
    have = count_mp3(out_dir)
    if have >= n_wanted:
        print(f"  cached: {have} FMA mp3s")
        return
    try:
        with requests.get(FMA_URL, stream=True, timeout=120) as r:
            r.raise_for_status()
            bar = _progress(None, "FMA (streaming)")

            def counted():
                for c in r.iter_content(CHUNK):
                    bar.update(len(c))
                    yield c
            n = mp3s_from_zip_stream(counted(), out_dir, n_wanted)
            bar.close()
        if n < n_wanted:
            raise RuntimeError(f"only {n} mp3s in stream")
        print(f"  ✓ FMA: {n} mp3s")
    except Exception as e:
        print(f"  lean FMA download failed ({type(e).__name__}: {e}); falling back to full 8 GB zip")
        zpath = "/content/fma_small.zip"
        with requests.get(FMA_URL, stream=True, timeout=120) as r, open(zpath, "wb") as f:
            r.raise_for_status()
            bar = _progress(int(r.headers.get("content-length", 0)) or None, "FMA (full)")
            for c in r.iter_content(CHUNK):
                f.write(c)
                bar.update(len(c))
            bar.close()
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(out_dir)
        os.remove(zpath)
        print(f"  ✓ FMA: {count_mp3(out_dir)} mp3s")
