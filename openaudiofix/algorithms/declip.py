"""Clipping detection and reconstruction - final version.

Clipping differs from clicks / dropped samples: the true value of a flattened sample is unknown, but its **absolute
value is certainly >= the threshold**. So the problem solved is an inequality-constrained least squares, not hole
interpolation. The final engine is `declip_hybrid()`:
  1. `declip_joint()`: fixed-length frames (8192); all clipped samples in a frame are unknowns at once (each bounded by
     its polarity), reliable samples are fixed, and the AR prediction error is minimized; the AR model is estimated on
     the continuous current estimate, with 3/2/1 iterations depending on the clipped fraction of the frame.
  2. `aspade.declip_aspade()`: A-SPADE (time-frequency sparsity prior).
  3. The two are averaged. Their errors point in opposite directions (AR peaks come out low, A-SPADE overshoots);
     on the benchmark the average gains 2.2-2.4 dB more in dense regions.
Reliable samples (those not flattened) are never modified. A reconstructed peak necessarily exceeds full scale, so the
output must be attenuated or saved as float - the caller decides.
The superseded segment-by-segment solver is used only as a baseline for ablation comparisons on the benchmark.
"""
import numpy as np
from scipy.linalg import solve_toeplitz
from scipy.optimize import lsq_linear

ORDER = 128         # AR order (found by parameter sweep; 256 / 384 give no stable gain on the whole-frame version)
# default iterations of reconstruct_frame; declip_joint uses 3/2/1 by clipped fraction (see J_ITERS_BY_DENSITY below)
ITERS = 5
# a single flat top longer than this is not treated as clipping (too long; reconstruction would be fabrication)
MAX_RUN = 2048
# upper limit of the reconstructed peak relative to the threshold, so numerical divergence cannot make absurd peaks
MAX_GAIN = 8.0


def find_clip_runs(x, thr_pos, thr_neg, max_run=MAX_RUN):
    """Returns [(start, end, sign), ...]; sign=+1 positive clipping, -1 negative. end is exclusive."""
    runs = []
    for sign, mask in ((+1, x >= thr_pos), (-1, x <= thr_neg)):
        if not mask.any():
            continue
        d = np.diff(mask.astype(np.int8))
        starts = np.flatnonzero(d == 1) + 1
        ends = np.flatnonzero(d == -1) + 1
        if mask[0]:
            starts = np.r_[0, starts]
        if mask[-1]:
            ends = np.r_[ends, len(x)]
        for a, b in zip(starts, ends):
            if b - a <= max_run:
                runs.append((int(a), int(b), sign))
    runs.sort()
    return runs


def estimate_threshold(x, full_scale=1.0):
    """Infer the clipping threshold. In most cases it is simply full scale, but the file may have been clipped and then
    normalized down, so the actual flat-top value is found from "whether the top of the amplitude histogram has a
    pile-up" rather than assuming full scale.
    Returns (thr_pos, thr_neg, detected); detected=False means no clipping signature is visible."""
    ax = np.abs(x)
    pk = ax.max()
    if pk <= 0:
        return full_scale, -full_scale, False
    # cut the top 2% of the dynamic range into 40 bins and see whether the last bin is far above its neighbors (the
    # fingerprint of clipping)
    lo = pk * 0.98
    sel = ax[ax >= lo]
    if len(sel) < 20:
        return pk, -pk, False
    h, edges = np.histogram(sel, bins=40, range=(lo, pk))
    top = h[-1]
    neigh = np.median(h[-12:-2]) if len(h) >= 12 else np.median(h[:-1])
    detected = top > max(8 * max(neigh, 1e-9), 8)
    thr = edges[-2]  # lower edge of the last bin as the threshold
    if not detected:
        return pk, -pk, False
    # detect positive and negative separately (some material is clipped on one side only)
    pos = x[x >= thr]; neg = x[x <= -thr]
    thr_pos = float(np.min(pos)) if len(pos) else float(pk)
    thr_neg = float(np.max(neg)) if len(neg) else float(-pk)
    return thr_pos, thr_neg, True


# ============================================================================================
# Whole-frame joint solve
#
# The per-segment solver (reconstruct_run above) has three flaws in densely clipped regions:
#   1. _ar_from_context removes the clipped samples and glues the rest together before computing the autocorrelation
#      -> the time axis is squashed and periodicity is destroyed.
#   2. When solving one segment, neighboring flat tops in the context are treated as "real observations" and pinned at
#      the ceiling -> the model learns that peaks are only that high.
#   3. PAD=1 turns two reliable shoulder samples into free variables.
# With light clipping there are few holes and all three matter little; in dense regions (100+ flat-top segments per
# second) peak height is systematically underestimated.
#
# This version: fixed-length frames in which **all** clipped samples are unknowns at once (lower/upper bounds by
# polarity), with all reliable samples fixed; the AR model is estimated on the "continuous current estimate" (initially
# the clipped frame itself: biased but with the correct time axis) and re-estimated each round from the reconstruction;
# the prediction-error matrix is a sparse diagonal matrix, so frames of 8192 do not exhaust memory.
# ============================================================================================
import scipy.sparse as _sp

J_FRAME = 8192       # frame length (171 ms @48k, about 50 periods of 300 Hz)
# context margin on both sides of the frame: solved but not written back, so every unknown in the core region has enough
# context on both sides
J_MARGIN = 768
# The number of Janssen iterations decreases with the "clipped fraction of the frame" (iterations x density sweep on the
# benchmark, n=12):
#   3%  → iters 1/2/3/5 give SDR_c 20.2/21.4/21.7/21.1 and crest-factor error −0.33/+0.03/+0.24/+0.35 dB → 3 is best
#   6%  → 17.8/17.9/18.6, crest factor −0.40/+0.75/+0.90 → 2 is a compromise between SDR and overshoot
#   12% → iters 1/2/3/5 give SDR_c 14.7/14.6/15.1/15.2, crest-factor error +0.12/+1.38/+1.34/+1.22 → more iterations buy
#     only 0.5 dB SDR,
#          but push the crest factor of loud shouting 1.2–1.4 dB higher (overshoot p90 +0.13 → +0.54); 1 is the only
#            choice without overshoot
# Consistent with the literature (Záviška 2021: iterations raise SDR, but the lower the input SDR, the earlier they
# diverge).
# (upper bound of the clipped fraction in the frame, iterations)
J_ITERS_BY_DENSITY = ((0.04, 3), (0.08, 2), (1.01, 1))


def _iters_for(frac):
    for lim, it in J_ITERS_BY_DENSITY:
        if frac < lim:
            return it
    return 1


def _ar_fft(seg, order):
    """FFT autocorrelation + Yule-Walker. seg must be a continuous signal (not one with samples removed and the rest
    glued together)."""
    m = seg - seg.mean()
    n = len(m)
    nfft = 1 << (2 * n - 1).bit_length()
    F = np.fft.rfft(m, nfft)
    r = np.fft.irfft(F * np.conj(F), nfft)[:order + 1]
    if r[0] <= 0:
        return None
    r = r.copy(); r[0] *= 1.0001
    try:
        return solve_toeplitz(r[:order], r[1:order + 1])
    except Exception:
        return None


def _pred_matrix_sparse(A_filt, n, P):
    """Prediction-error matrix B ((n-P)×n): row m, column m+P-k = A[k]. Sparse diagonal form."""
    diags = [A_filt[P - d] * np.ones(n - P) for d in range(P + 1)]
    return _sp.diags(diags, offsets=list(range(P + 1)), shape=(n - P, n), format="csc")


def reconstruct_frame(seg, unk_mask, pol, thr_pos, thr_neg, order=None, iters=None):
    """seg: continuous current estimate (length n); unk_mask: which samples are clipped; pol: polarity of each sample
    (+1/-1, meaningful only for unknowns). Returns the new seg (unknowns reconstructed), or None on failure."""
    P = ORDER if order is None else order
    n_iter = ITERS if iters is None else iters
    n = len(seg)
    unk = np.flatnonzero(unk_mask)
    if len(unk) == 0 or n <= P * 2 + 8:
        return seg
    cap = max(abs(thr_pos), abs(thr_neg)) * MAX_GAIN
    lb = np.where(pol[unk] > 0, thr_pos, -cap)
    ub = np.where(pol[unk] > 0, cap, thr_neg)
    cur = seg.copy()
    best = None
    for it in range(max(1, n_iter)):
        coef = _ar_fft(cur, P)
        if coef is None:
            break
        A_filt = np.concatenate([[1.0], -coef])
        B = _pred_matrix_sparse(A_filt, n, P)
        # Only a few hundred unknowns: convert Bu to dense ((n-P)×|unk|, 8064×400 ≈ 26 MB) and solve exactly with
        # active-set bvls,
        # one to two orders of magnitude faster than sparse trf + LSMR (the AR prediction matrix is very ill-conditioned
        # and
        # LSMR converges extremely slowly - measured: one second of audio did not finish in 5 minutes)
        Bu = B[:, unk].toarray()
        z = cur.copy(); z[unk] = 0.0
        rhs = -(B @ z)
        # Reduce to an equivalent |unk|×|unk| problem and solve: M = BuᵀBu = LLᵀ, ‖Bu·u − r‖² = ‖Lᵀu − L⁻¹Buᵀr‖² +
        # const.
        # Running trf or bvls directly on (n−P)×|unk| is too slow to use (>4 minutes per frame, measured); the reduction
        # cuts the rows by 20x.
        try:
            M = Bu.T @ Bu
            M[np.diag_indices_from(M)] += 1e-9 * max(np.trace(M) / len(M), 1e-12)
            L = np.linalg.cholesky(M)
            bprime = np.linalg.solve(L, Bu.T @ rhs)
            res = lsq_linear(L.T, bprime, bounds=(lb, ub), method="trf", max_iter=200, tol=1e-9)
        except Exception:
            break
        y = res.x
        if not np.all(np.isfinite(y)) or np.abs(y).max() > cap * 1.001:
            break
        best = y
        cur[unk] = y          # reliable samples never move; only the unknowns are updated
    if best is None:
        return None
    out = seg.copy(); out[unk] = best
    return out


def declip_joint(x, sr, full_scale=1.0, progress=None, order=None, iters=None, frame=J_FRAME, margin=J_MARGIN):
    """Whole-frame joint version of declip. Same interface as declip()."""
    x = np.asarray(x, dtype=np.float64)
    thr_pos, thr_neg, detected = estimate_threshold(x, full_scale)
    if not detected:
        return x.copy(), dict(detected=False, n_runs=0, reason="no clipping detected (no pile-up at the top of the amplitude histogram)")
    mask = (x >= thr_pos) | (x <= thr_neg)
    pol = np.where(x >= 0, 1, -1)
    runs = find_clip_runs(x, thr_pos, thr_neg)
    y = x.copy()
    core = frame - 2 * margin
    n_frames = done = 0
    for c0 in range(0, len(x), core):
        c1 = min(len(x), c0 + core)
        if not mask[c0:c1].any():
            continue
        f0 = max(0, c0 - margin); f1 = min(len(x), c1 + margin)
        n_frames += 1
        it = iters if iters is not None else _iters_for(mask[f0:f1].mean())
        out = reconstruct_frame(y[f0:f1], mask[f0:f1], pol[f0:f1], thr_pos, thr_neg, order, it)
        if out is None:
            continue
        sel = mask[c0:c1]
        # write back only the unknowns in the core region; reliable samples never change
        y[c0:c1][sel] = out[c0 - f0:c1 - f0][sel]
        done += 1
        if progress is not None and n_frames % 10 == 0:
            progress(c0, len(x))
    peak = float(np.abs(y).max())
    return y, dict(detected=True, thr_pos=float(thr_pos), thr_neg=float(thr_neg),
                   n_runs=len(runs), n_reconstructed=len(runs), n_skipped=0, n_frames=n_frames, frames_ok=done,
                   peak=peak, headroom_db=float(20 * np.log10(max(peak, 1e-12) / full_scale)),
                   run_len_median=int(np.median([b - a for a, b, _ in runs])) if runs else 0,
                   run_len_max=int(max([b - a for a, b, _ in runs])) if runs else 0)


# ============================================================================================
# Hybrid: average of the whole-frame joint AR and A-SPADE
#
# The two have errors in opposite directions: the joint AR's peaks are systematically low and it almost never
# overshoots;
# A-SPADE's peak height is unbiased but overshoots (crest factor 0.6–1 dB above the truth). Averaging two estimates with
# opposite error directions wins on both sides on the benchmark:
#   6% density: AR 17.8, A-SPADE 17.6 → average 20.0 dB (worst decile 14.2 → 16.9), crest-factor error −0.16 dB
# Both satisfy clipping consistency (>= threshold), so the average does too; neither moves reliable samples, so neither
# does the average. The literature's suggestion to raise the AR order so the predictor can see the previous pitch period
# (256 / 384) showed no stable gain when measured; the order stays at 128.
# ============================================================================================
def declip_hybrid(x, sr, full_scale=1.0, progress=None):
    from . import aspade as _SP
    x = np.asarray(x, dtype=np.float64)
    thr_pos, thr_neg, detected = estimate_threshold(x, full_scale)
    if not detected:
        return x.copy(), dict(detected=False, n_runs=0, reason="no clipping detected (no pile-up at the top of the amplitude histogram)")
    p1 = (lambda i, n: progress(i // 2, n)) if progress else None
    p2 = (lambda i, n: progress(n // 2 + i // 2, n)) if progress else None
    yj, info = declip_joint(x, sr, full_scale, progress=p1)
    ys, _ = _SP.declip_aspade(x, sr, thr_pos, thr_neg, progress=p2)
    m = (x >= thr_pos) | (x <= thr_neg)
    y = x.copy()
    y[m] = 0.5 * (yj[m] + ys[m])
    y[x >= thr_pos] = np.maximum(y[x >= thr_pos], thr_pos)
    y[x <= thr_neg] = np.minimum(y[x <= thr_neg], thr_neg)
    peak = float(np.abs(y).max())
    info.update(peak=peak, headroom_db=float(20 * np.log10(max(peak, 1e-12) / full_scale)), method="hybrid")
    return y, info
