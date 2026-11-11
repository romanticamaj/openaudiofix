"""Fallback for ar_bidir_xfade: a verbatim copy of the baseline (AR(48) least-squares
interpolation, 1536-sample two-sided context).  Kept separate so the candidate never
depends on the shared baseline file."""
import numpy as np
from scipy.linalg import solve_toeplitz, solve
ORDER = 48; CTX = 1536

def _ar_coefs(seg, order=ORDER):
    seg = seg - seg.mean(); r = np.correlate(seg, seg, 'full')[len(seg)-1:len(seg)+order]
    if r[0] <= 0: return None
    r = r.copy(); r[0] *= 1.0001; return solve_toeplitz(r[:order], r[1:order+1])

def ls_fill(ctx, a, b, sr):
    x = ctx.copy(); gap = b - a; x[a:b] = 0.0
    left = x[a-CTX:a]; right = x[b:b+CTX]
    coef = _ar_coefs(np.concatenate([left, right]))
    if coef is None: return np.linspace(x[a-1], x[b], gap+2)[1:-1]
    A = np.concatenate([[1.0], -coef]); P = ORDER
    seg = x[a-P:b+P].copy(); n = len(seg); unk = np.arange(P, P+gap); Mrows = n - P
    B = np.zeros((Mrows, n))
    for k in range(P+1): B[np.arange(Mrows), np.arange(Mrows)+P-k] = A[k]
    known = np.ones(n, bool); known[unk] = False
    Bu = B[:, unk]; rhs = -B[:, known] @ seg[known]
    y = solve(Bu.T@Bu + 1e-9*np.eye(gap), Bu.T@rhs, assume_a='pos')
    if not np.all(np.isfinite(y)): y = np.linspace(x[a-1], x[b], gap+2)[1:-1]
    return y
