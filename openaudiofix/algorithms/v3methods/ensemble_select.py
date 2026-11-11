"""ensemble_select - analysis-by-synthesis selector.
For each hole, several candidate fills are computed at once (pitch_wsola / ar_ls_excite_js / ar_bidir_xfade_js /
ar48_ls),
scored by a continuity cost that "looks only at data outside the hole", and the lowest cost wins. The cost deliberately
penalizes heavily: being louder than the neighboring segments or having spikes (overshoot, pre-echo, repeated glottal
pulses), a seam more abrupt than the neighbors (tick risk), and abnormal spikes inside; it also counts the gap to the
neighbors in energy / band / spectrum (muffled, dips).
Each candidate method has different known failure modes; the selector's role is to avoid any one method's failure case
on that hole."""
import numpy as np
from scipy.signal import butter, sosfiltfilt, stft
from . import pitch_wsola, ar_ls_excite_js, ar_bidir_xfade_js, ar48_ls

CANDS = [("pitch_wsola", pitch_wsola.fill), ("ar_ls_excite_js", ar_ls_excite_js.fill),
         ("ar_bidir_xfade_js", ar_bidir_xfade_js.fill), ("ar48_ls", ar48_ls.fill)]
W = {"energy": 1.0, "louder_extra": 0.8, "mid": 0.5, "hf": 0.5, "spec": 0.6, "edge": 1.5, "peak": 2.0, "inside_hi": 3.0, "inside_lo": 1.0}
NP_, HOP = 128, 32
_sos = {}
def sos(sr, lo, hi):
    k = (sr, lo, hi)
    if k not in _sos: _sos[k] = butter(4, [lo, hi], btype='band', fs=sr, output='sos')
    return _sos[k]
def rms(v): return float(np.sqrt(np.mean(v ** 2)) + 1e-12)
def be_cf(seg, s):
    y = seg * np.hanning(len(seg)); y = np.concatenate([np.zeros(1024), y, np.zeros(1024)]); return float(np.sum(sosfiltfilt(s, y) ** 2) + 1e-18)
def logmag(A, floor): return 20 * np.log10(A + floor)
def frames(x, a, b, sr):
    pad = NP_ - HOP; seg = x[a - pad:b + pad]
    _, _, Z = stft(seg, fs=sr, nperseg=NP_, noverlap=NP_ - HOP, boundary=None, padded=False)
    keep = [i for i in range(Z.shape[1]) if i * HOP + NP_ > pad and i * HOP < pad + (b - a)]
    return np.abs(Z[:, keep])
def side_spec(seg, sr):
    _, _, Z = stft(seg, fs=sr, nperseg=NP_, noverlap=NP_ - HOP, boundary=None, padded=False); return np.abs(Z)

LAST = {}
def fill(ctx, a, b, sr):
    L = b - a; Ln = max(L, 96); Ls = max(L, 512)
    x = ctx.copy(); x[a:b] = 0.0
    left = x[a - Ln:a]; right = x[b:b + Ln]
    nb_rms = (rms(left) + rms(right)) / 2
    s_mid, s_hf = sos(sr, 300, 2000), sos(sr, 2000, 8000)
    nb_mid = (be_cf(left, s_mid) + be_cf(right, s_mid)) / 2; nb_hf = (be_cf(left, s_hf) + be_cf(right, s_hf)) / 2
    nb_peak = max(np.abs(left).max(), np.abs(right).max()) + 1e-12
    ZL = side_spec(x[a - Ls:a], sr); ZR = side_spec(x[b:b + Ls], sr)
    # reference join / inside roughness from the neighbours
    d2c = np.abs(x[2:] - 2 * x[1:-1] + x[:-2])
    scale = max(np.median(np.concatenate([d2c[a - 2048:a - 8], d2c[b + 8:b + 2048]])), 1e-9)
    e_ref = float(np.max(d2c[np.r_[np.arange(a - Ln - 5, a - Ln + 4), np.arange(b + Ln - 5, b + Ln + 4)]]) / scale)
    i_ref = float((np.max(d2c[a - Ln + 4:a - 5]) + np.max(d2c[b + 4:b + Ln - 5])) / 2 / scale) if Ln > 12 else 0.0
    best = None; scores = {}
    for name, f in CANDS:
        try:
            y = np.asarray(f(ctx, a, b, sr), dtype=np.float64)
            if y.shape != (L,) or not np.all(np.isfinite(y)): raise ValueError("bad fill")
        except Exception:
            scores[name] = float("inf"); continue
        xf = x.copy(); xf[a:b] = y
        e_db = 20 * np.log10(rms(y) / nb_rms)
        mid = 10 * np.log10(be_cf(y, s_mid) / nb_mid); hf = 10 * np.log10(be_cf(y, s_hf) / nb_hf)
        peak = 20 * np.log10((np.abs(y).max() + 1e-12) / nb_peak)
        A = frames(xf, a, b, sr); floor = max(A.max(), ZL.max(), ZR.max()) * 1e-4
        ref = (logmag(ZL, floor).mean(axis=1) + logmag(ZR, floor).mean(axis=1)) / 2
        spec = float(np.sqrt(np.mean((logmag(A, floor).mean(axis=1) - ref) ** 2)))
        d2 = np.abs(xf[2:] - 2 * xf[1:-1] + xf[:-2])
        edge = float(np.max(d2[np.r_[np.arange(a - 5, a + 4), np.arange(b - 5, b + 4)]]) / scale)
        inside = float(np.max(d2[a + 4:b - 5]) / scale) if L > 12 else 0.0
        ins_dev = float(np.log2((inside + 0.5) / (i_ref + 0.5)))
        cost = (W["energy"] * abs(e_db) + W["louder_extra"] * max(0.0, e_db) + W["mid"] * abs(mid) + W["hf"] * abs(hf)
                + W["spec"] * spec + W["edge"] * max(0.0, edge - e_ref) + W["peak"] * max(0.0, peak - 1.0)
                + W["inside_hi"] * max(0.0, ins_dev) + W["inside_lo"] * max(0.0, -ins_dev - 0.5))
        scores[name] = cost
        if best is None or cost < best[0]: best = (cost, name, y)
    LAST.clear(); LAST.update(chosen=best[1], scores=scores)
    return best[2]
