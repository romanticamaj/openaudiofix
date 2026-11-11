"""Deterministic speech-like test signals and synthetic defects.

Used by the public test suite and by the GPU self-test, so no audio file (and no licence) is needed.
"""
import numpy as np


def speech_like(sr=48000, seconds=10.0, seed=0, channels=1, level_db=-12.0):
    """Harmonic source with a moving pitch, a crude formant tilt, syllable envelope and pauses."""
    rng = np.random.default_rng(seed)
    n = int(round(sr * seconds))
    t = np.arange(n) / sr
    f0 = 140 + 40 * np.sin(2 * np.pi * 0.31 * t) + 15 * np.sin(2 * np.pi * 1.7 * t + 1.0)
    phase = 2 * np.pi * np.cumsum(f0) / sr
    x = np.zeros(n)
    for k in range(1, 30):
        fk = k * f0
        amp = (1.0 / k) * np.exp(-0.5 * ((fk - 700) / 900) ** 2) + 0.3 / k
        amp = np.where(fk < sr / 2 - 1000, amp, 0.0)
        x += amp * np.sin(k * phase + rng.uniform(0, 2 * np.pi))
    syllables = 0.5 - 0.5 * np.cos(2 * np.pi * 3.7 * t)
    gate = (np.sin(2 * np.pi * 0.23 * t + 0.4) > -0.6).astype(float)
    # ~10 ms raised-cosine ramps: real speech has no 0 -> 1 steps, and a hard step reads as a seam
    ramp = np.hanning(int(sr * 0.010) | 1)
    ramp /= ramp.sum()
    h = len(ramp) // 2
    gate = np.convolve(np.pad(gate, h, mode="edge"), ramp, mode="valid")
    x *= syllables * gate
    x += 10 ** (-60 / 20) * rng.standard_normal(n)
    x *= 10 ** (level_db / 20) / np.abs(x).max()
    cols = [x]
    for c in range(1, channels):
        cols.append(0.8 * x + 10 ** (-62 / 20) * rng.standard_normal(n))
    out = np.stack(cols, axis=1)
    return out * (10 ** (level_db / 20) / np.abs(out).max())


def inject_clipping(x, drive_db=8.0):
    """Drive into a 24-bit converter: quantise, then hard-clip at the integer full scale.

    The signal is scaled so its absolute peak lands `drive_db` dB ABOVE full scale
    (gain = 10**(drive_db/20) / peak), independent of the input level."""
    x = np.asarray(x, dtype=np.float64)
    y = x * (10 ** (drive_db / 20) / np.abs(x).max())
    q = 2.0 ** 23
    y = np.rint(y * q) / q
    return np.clip(y, -1.0, 1.0 - 1.0 / q)


def dropout_positions(sr, seconds, n, grid=64):
    """n positions on the buffer grid, spread over the file and away from the edges."""
    span = seconds - 1.0
    return [int(sr * (0.5 + span * (i + 0.5) / n)) // grid * grid for i in range(n)]


def inject_dropouts(x, positions, gap):
    """Remove `gap` samples so that output sample p jumps straight to source sample p + k*gap.
    `positions` are output indices; the input must be len(positions)*gap samples longer."""
    keep = np.ones(len(x), dtype=bool)
    shift = 0
    for p in sorted(positions):
        keep[p + shift: p + shift + gap] = False
        shift += gap
    return x[keep]


def quantise24(x):
    q = 2.0 ** 23
    return np.clip(np.rint(np.asarray(x) * q) / q, -1.0, 1.0 - 1.0 / q)
