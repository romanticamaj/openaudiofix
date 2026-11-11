"""Seam-smoothing post-processing: after the fill is complete, only the N samples of the hole closest to its two ends
are changed; they are re-interpolated with a low-order LS-AR between "the known samples outside the hole" and "the body
of the fill", which guarantees AR-smooth seams at both ends while keeping the body of the fill."""
import numpy as np
from scipy.linalg import solve_toeplitz, solve
def _ar(seg, order):
    seg = seg - seg.mean(); r = np.correlate(seg, seg, 'full')[len(seg)-1:len(seg)+order]
    if r[0] <= 0: return None
    r = r.copy(); r[0] *= 1.0001; return solve_toeplitz(r[:order], r[1:order+1])
def _ls(x, a, b, order, ctx):
    coef = _ar(np.concatenate([x[a-ctx:a], x[b:b+ctx]]), order)
    if coef is None: return x[a:b]
    A = np.concatenate([[1.0], -coef]); P = order; gap = b - a
    seg = x[a-P:b+P].copy(); n = len(seg); unk = np.arange(P, P+gap); M = n - P
    B = np.zeros((M, n))
    for k in range(P+1): B[np.arange(M), np.arange(M)+P-k] = A[k]
    known = np.ones(n, bool); known[unk] = False
    Bu = B[:, unk]; rhs = -B[:, known] @ seg[known]
    return solve(Bu.T @ Bu + 1e-9*np.eye(gap), Bu.T @ rhs, assume_a='pos')
def smooth_joins(ctx, a, b, y, n=12, order=24, ctxlen=768):
    """ctx: excerpt with NaN hole; y: proposed fill. Returns y with its first/last n samples re-interpolated."""
    x = ctx.copy(); x[a:b] = y; gap = b - a
    n = min(n, gap // 3)
    if n < 4: return y
    y2 = y.copy()
    y2[:n] = _ls(x, a, a+n, order, ctxlen)          # left join: known = outside-left + fill body
    x[a:b] = y2
    y2[gap-n:] = _ls(x, b-n, b, order, ctxlen)      # right join
    return y2
def wrap(base_fill, **kw):
    def fill(ctx, a, b, sr):
        y = np.asarray(base_fill(ctx, a, b, sr), dtype=np.float64)
        return smooth_joins(ctx, a, b, y, **kw)
    return fill
