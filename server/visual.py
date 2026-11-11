"""Waveform peaks and spectrogram PNGs computed from the stage .npy files for any time window."""
import re
import struct
import zlib

import numpy as np

CHUNK = 1 << 21
NFFT = 2048
_ANCHORS = np.array([[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66],
                     [245, 125, 21], [250, 193, 39], [252, 255, 164]], dtype=float)
LUT = np.stack([np.interp(np.linspace(0, 1, 256), np.linspace(0, 1, len(_ANCHORS)), _ANCHORS[:, c])
                for c in range(3)], axis=1).astype(np.uint8)


def resolve(storage, project, src_id):
    """-> (get(i0, i1) -> (m, ch) array, n_samples, gain). Sources share the preview gain; diffs use their own."""
    pid, mods = project["id"], project["modules"]
    res = project.get("result") or {}
    src = storage.load(storage.source_path(pid))
    stages = {}

    def stage(k):
        if k not in stages:
            stages[k] = src if k < 0 else storage.load(storage.stage_path(pid, k, mods[k]))
        return stages[k]

    pg = 10 ** (float(res.get("preview_gain_db") or 0.0) / 20)
    diff_gain = {d["id"]: d.get("gain_db") for d in res.get("diffs", [])}
    if src_id == "original":
        return (lambda i0, i1: src[i0:i1]), len(src), pg
    if src_id == "final":
        src_id = f"s{len(mods) - 1}"
    m = re.fullmatch(r"([sd])(0|[1-9][0-9]*)", src_id)
    if m and m.group(1) == "s" and int(m.group(2)) < len(mods):
        a = stage(int(m.group(2)))
        return (lambda i0, i1: a[i0:i1]), len(a), pg
    g = 10 ** (float(diff_gain.get(src_id) or 0.0) / 20)
    if src_id == "d_chain":
        a, b = src, stage(len(mods) - 1)
        return (lambda i0, i1: np.asarray(a[i0:i1]) - np.asarray(b[i0:i1])), len(a), g
    if m and m.group(1) == "d" and int(m.group(2)) < len(mods):
        k = int(m.group(2))
        a, b = stage(k - 1), stage(k)
        return (lambda i0, i1: np.asarray(a[i0:i1]) - np.asarray(b[i0:i1])), len(b), g
    raise KeyError(src_id)


def peaks_minmax(get, i0, i1, buckets, gain=1.0):
    """Interleaved max/min per bucket. i0 is clamped to >= 0; the caller keeps i1 <= n."""
    i0 = max(0, int(i0))
    n = max(0, i1 - i0)
    if n == 0:
        return []
    buckets = max(1, min(int(buckets), n))
    edges = np.linspace(i0, i1, buckets + 1).astype(np.int64)
    mx, mn = np.empty(buckets), np.empty(buckets)
    b = 0
    while b < buckets:
        e = b + 1
        while e < buckets and edges[e + 1] - edges[b] <= CHUNK:
            e += 1
        seg = np.asarray(get(int(edges[b]), int(edges[e])), dtype=np.float64).mean(axis=1) * gain
        idx = edges[b:e] - edges[b]
        mx[b:e] = np.maximum.reduceat(seg, idx)
        mn[b:e] = np.minimum.reduceat(seg, idx)
        b = e
    return np.round(np.column_stack([mx, mn]).ravel(), 4).tolist()


def png_rgb(rgb):
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def spectrogram_png(get, n, sr, i0, i1, width, height=256, gain=1.0):
    width, height = max(1, int(width)), max(8, int(height))
    win = np.hanning(NFFT)
    norm = win.sum() / 2
    centers = (i0 + (np.arange(width) + 0.5) * (i1 - i0) / width).astype(np.int64)
    mags = np.zeros((width, NFFT // 2 + 1))
    for c, ctr in enumerate(centers):
        a = int(max(0, ctr - NFFT // 2))
        b = int(min(n, a + NFFT))
        seg = np.asarray(get(a, b), dtype=np.float64).mean(axis=1) * gain
        frame = np.zeros(NFFT)
        frame[: len(seg)] = seg
        mags[c] = np.abs(np.fft.rfft(frame * win)) / norm
    freqs = np.fft.rfftfreq(NFFT, 1 / sr)
    fmin, fmax = 40.0, sr / 2
    edges = fmin * (fmax / fmin) ** (np.arange(height + 1) / height)
    bins = np.searchsorted(freqs, edges)
    img = np.zeros((height, width), dtype=np.uint8)
    for r in range(height):
        lo = min(bins[r], len(freqs) - 1)
        hi = max(bins[r + 1], lo + 1)
        db = 20 * np.log10(mags[:, lo:hi].max(axis=1) + 1e-10)
        img[height - 1 - r] = np.clip((db + 100) / 80 * 255, 0, 255).astype(np.uint8)
    return png_rgb(LUT[img])
