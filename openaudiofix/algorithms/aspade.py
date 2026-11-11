"""A-SPADE (analysis sparse audio declipper), for a direct comparison with the whole-frame joint AR
(declip.declip_joint).

Algorithm: Kitić, Bertin, Gribonval 2015; ADMM form and parameters follow Záviška et al. 2019/2021.
Each frame (after windowing) solves
    min ||A x||_0   s.t.  x ∈ Γ
A = 2x-redundant DFT (Parseval tight frame: A^H A = I), Γ = clipping-consistent set:
    reliable samples x = y; positive clipping x ≥ y; negative clipping x ≤ y (y is the windowed observation).
ADMM:
    z = H_k(A x + u)            hard threshold, keep only the k largest coefficients
    x = P_Γ(A^H (z − u))        project back onto the consistent set
    stop if ||A x − z|| ≤ ε, otherwise u += A x − z, and every r iterations k += s
The fundamental difference from the AR family: the prior is "time-frequency sparsity", not "linear predictability".
For periodic speech, harmonics are already sparse in frequency, so in theory it suits dense clipping; the cost is many
iterations.

Frame synthesis: each frame is solved in the windowed domain (x_w ≈ g·x), multiplied by g again when overlap-adding,
and finally divided by Σg², which is correct for any overlap ratio.
Only frames containing clipping are processed; regions without clipping are unchanged sample by sample.
"""
import numpy as np

# frame length (43 ms): speech is shorter than the music Záviška tested; start here, sweep later on the benchmark
W = 2048
HOP = 512         # 75% overlap
REDUND = 2        # DFT redundancy factor
S = 1             # sparsity relaxed at each step
R = 1             # relax once every R iterations
EPS = 0.1         # stopping criterion ||Ax − z|| (same as Záviška; relative to the scale of the windowed signal)
MAX_IT = 1000


def _A(x, n):
    return np.fft.fft(x, n, norm="ortho")


def _AH(z, w):
    return np.real(np.fft.ifft(z, norm="ortho"))[:w]


def _hard(z, k):
    """Keep the k coefficients with the largest magnitude (conjugate pairs counted together: the threshold is decided
    from the first half of the spectrum, which keeps the symmetry of a real signal)."""
    a = np.abs(z)
    if k >= len(a):
        return z
    thr = np.partition(a, len(a) - k)[len(a) - k]
    return np.where(a >= thr, z, 0)


def aspade_frame(y, rel, cp, cn, eps=EPS, s=S, r=R, max_it=MAX_IT):
    """y: windowed frame; rel/cp/cn: reliable / positive-clip / negative-clip masks.
    Returns the reconstructed frame (in the windowed domain)."""
    w = len(y); n = REDUND * w
    x = y.copy(); u = np.zeros(n, dtype=complex)
    k = s
    for it in range(1, max_it + 1):
        Ax = _A(x, n)
        z = _hard(Ax + u, k)
        v = _AH(z - u, w)
        x = v.copy()                      # projection onto Γ
        x[rel] = y[rel]
        x[cp] = np.maximum(v[cp], y[cp])
        x[cn] = np.minimum(v[cn], y[cn])
        Ax = _A(x, n)
        res = np.linalg.norm(Ax - z)
        if res <= eps:
            break
        u = u + Ax - z
        if it % r == 0:
            k += 2 * s                    # 2 = conjugate pair; makes the "true" sparsity grow by s each time
    return x, it


def declip_aspade(x, sr, thr_pos=None, thr_neg=None, w=W, hop=HOP, eps=EPS, max_it=MAX_IT, progress=None):
    """Process the whole signal. If thr is not given, it is inferred with declip.estimate_threshold. Returns (y,
    info)."""
    x = np.asarray(x, dtype=np.float64)
    if thr_pos is None:
        from . import declip as _D
        thr_pos, thr_neg, det = _D.estimate_threshold(x)
        if not det:
            return x.copy(), dict(detected=False)
    cp_all = x >= thr_pos; cn_all = x <= thr_neg
    clipped = cp_all | cn_all
    g = np.hanning(w + 2)[1:-1]
    num = np.zeros(len(x)); den = np.zeros(len(x))
    its = []
    starts = range(-w + hop, len(x), hop)
    for st in starts:
        a, b = st, st + w
        a0, b0 = max(a, 0), min(b, len(x))
        if not clipped[a0:b0].any():
            continue
        seg = np.zeros(w); seg[a0 - a:b0 - a] = x[a0:b0]
        cp = np.zeros(w, bool); cn = np.zeros(w, bool)
        cp[a0 - a:b0 - a] = cp_all[a0:b0]; cn[a0 - a:b0 - a] = cn_all[a0:b0]
        rel = ~(cp | cn)
        yw = seg * g
        xr, it = aspade_frame(yw, rel, cp, cn, eps=eps, max_it=max_it)
        its.append(it)
        if progress is not None and len(its) % 25 == 0:
            progress(max(st, 0), len(x))
        num[a0:b0] += (xr * g)[a0 - a:b0 - a]
        den[a0:b0] += (g * g)[a0 - a:b0 - a]
    y = x.copy()
    m = clipped & (den > 1e-9)
    y[m] = num[m] / den[m]
    # Consistency: reconstructed values must not fall back inside the threshold (numerical error at window edges can
    # cause tiny violations)
    y[cp_all] = np.maximum(y[cp_all], thr_pos)
    y[cn_all] = np.minimum(y[cn_all], thr_neg)
    return y, dict(detected=True, n_frames=len(its), iters_median=int(np.median(its)) if its else 0,
                   peak=float(np.abs(y).max()))
