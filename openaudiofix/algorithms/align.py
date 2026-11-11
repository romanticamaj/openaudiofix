"""Resampling and fixed-latency alignment for model outputs (copied from separation/runners/_common.py)."""
import numpy as np

MAX_LAG = 4096          # alignment search range (+-samples); the model delay is usually within a few hundred samples


def resample(x, sr_in, sr_out):
    if sr_in == sr_out:
        return x
    from scipy.signal import resample_poly
    from math import gcd
    g = gcd(sr_in, sr_out)
    return resample_poly(x, sr_out // g, sr_in // g).astype(np.float32)


def align_to(ref, y):
    """Align the model output y to the original ref: compensate a fixed delay and trim to equal length.
    Returns (y_aligned, lag). lag>0 means y is lag samples later than ref. Found by cross-correlating the whole
    signal; speech-enhancement models do not change the time axis, so there should be only one fixed delay, and a
    peak that is not prominent enough is treated as 0 (no spurious shifting)."""
    n = min(len(ref), len(y))
    a = ref[:n].astype(np.float64); b = y[:n].astype(np.float64)
    L = 1 << int(np.ceil(np.log2(2 * n)))
    A = np.fft.rfft(a, L); B = np.fft.rfft(b, L)
    c = np.fft.irfft(np.conj(A) * B, L)
    cand = np.r_[c[:MAX_LAG + 1], c[-MAX_LAG:]]
    lags = np.r_[np.arange(0, MAX_LAG + 1), np.arange(-MAX_LAG, 0)]
    k = int(np.argmax(cand))
    lag = int(lags[k])
    if cand[k] < 3 * np.median(np.abs(cand)):
        lag = 0
    if lag > 0:
        y = np.r_[y[lag:], np.zeros(lag, np.float32)]
    elif lag < 0:
        y = np.r_[np.zeros(-lag, np.float32), y[:lag]]
    if len(y) < len(ref):
        y = np.r_[y, np.zeros(len(ref) - len(y), np.float32)]
    return y[:len(ref)].astype(np.float32), lag

