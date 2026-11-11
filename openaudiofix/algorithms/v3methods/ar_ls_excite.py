"""ar_ls_excite -- LS-AR interpolation + excitation / energy restoration.

Idea
----
The baseline LS-AR fill is the *minimum residual-energy* interpolant: it is the
conditional MEAN of the missing samples under the AR model, so it has almost no
excitation inside the hole and sounds dull (3-13 dB too quiet, 8-19 dB too quiet
in 2-8 kHz).  Speech is (envelope filter) x (excitation); LS threw away the
excitation.  We put it back:

  1. AR(P=64) envelope from the two-sided context (2048 per side,
     autocorrelation method, Hann window).
  2. y_ls = baseline LS-AR fill (same Janssen normal equations as ar48_ls).
  3. Residual (excitation) on the left / right context: e = A(z) x.
  4. Build a *target excitation* e_t for the hole rows (positions a .. b+P-1),
     from the left context and from the right context, then cross-fade them
     (equal-power raised cosine, left -> right):
       - voiced side (normalised autocorrelation >= 0.55, lag 190..470):
         pitch-synchronous copy of that side's residual, i.e. the last (first)
         pitch period repeated, so the glottal pulse train continues through
         the hole with that side's exact local phase;
       - unvoiced side: copy of the residual segment immediately adjacent to
         the hole (same local statistics, deterministic, no RNG needed).
     ("warp" = a single time-warped pulse train between the last left and the
     first right pulse, and "noise" = seeded Gaussian, are kept as options but
     measured worse: pulse timing errors from the far side cost SNR.)
  5. Solve the SAME LS normal equations with e_t as right-hand side:
        d = (Bu'Bu)^-1 Bu' e_t   ==>   y = y_ls + g*d
     This is "filter e_t through the AR synthesis filter, consistent with the
     known samples on both sides": d is the conditional response to the
     excitation, tied down by the AR model at both joins (no explicit window is
     needed, the joins stay click-free), and y_ls + d is a sample of the AR
     posterior when e_t is white noise.
  6. Gain g: solve ||y_ls + g d||^2 = gap * r_t^2 where r_t is the neighbour
     RMS -- for a voiced side taken pitch-synchronously (same pitch phase one
     period away) so a short hole between two pulses is not pumped up to the
     average level; soften towards the natural level (g = g_raw**0.7) and
     clamp to [0.7, 1.5]; then a peak guard against overshoot relative to the
     neighbour peak.

Fallback: if the AR model cannot be estimated or anything is non-finite, the
plain baseline LS-AR fill (or a linear ramp) is returned, so we are never worse
than the baseline.  Deterministic: the only RNG (unused with the default
UNVOICED_SRC="copy") is seeded from a, b and the context samples.
"""
import hashlib
import numpy as np
from scipy.linalg import solve_toeplitz, cho_factor, cho_solve
from scipy.signal import lfilter

# ------------------------------------------------------------------ parameters
ORDER = 64            # AR order (envelope)
CTX = 2048            # context per side for the AR estimate
RES_LEN = 1536        # residual length per side used for pitch / level / copying
LVL_LEN = 480         # samples (10 ms) used for the residual RMS estimate
PITCH_MIN = 190       # lags searched for the pitch period (48 kHz): 253 .. 102 Hz
PITCH_MAX = 470
VOICED_NAC = 0.55     # normalised autocorrelation threshold for "voiced"
EXC_MODE = "pitch"    # "pitch" (two-sided copy + cross-fade) | "warp" | "noise"
XFADE = "cos"         # cross-fade shape between left- and right-derived excitation
G_MIN, G_MAX = 0.7, 1.5   # clamp for the excitation gain after energy matching
G_SOFT = 0.7          # g = g_raw ** G_SOFT  (1 = full match, 0 = natural level)
TARGET_MEAN = "arith" # neighbour RMS combination: "arith" | "geo"
TARGET_MODE = "sync"  # "sync": pitch-synchronous neighbour energy for voiced sides | "plain"
PULSE_TOL = 5         # +-T/PULSE_TOL search window when locating the adjacent pulse
NB_LEN_MIN = 384      # min neighbour length for the RMS target
PEAK_MARGIN = 1.0     # fill peak may not exceed neighbour peak * this
HF_PREEMPH = 0.0      # first-order pre-emphasis of the target excitation (0 = off)
UNVOICED_SRC = "copy"  # unvoiced side excitation: "copy" (adjacent residual) | "noise" (seeded Gaussian)
LAST = {}             # diagnostics of the last call (dev only; harmless)


# ------------------------------------------------------------------ helpers
def _rms(v):
    return float(np.sqrt(np.mean(v * v)) + 1e-12)


def _lin_fallback(x, a, b):
    return np.linspace(x[a - 1], x[b], b - a + 2)[1:-1]


def _short_context(x, a, b):
    """Not enough context for the excitation model: baseline-style LS-AR with
    whatever context is available (never used by the benchmark geometry)."""
    gap = b - a
    P = min(ORDER, 48)
    c = min(CTX, a - P - 1, len(x) - b - P - 1)
    if c < 4 * P:
        return _lin_fallback(x, a, b)
    coef = _ar_coefs(np.concatenate([x[a - c:a], x[b:b + c]]), P)
    if coef is None or not np.all(np.isfinite(coef)):
        return _lin_fallback(x, a, b)
    A = np.concatenate([[1.0], -coef])
    Bu, rhs0 = _build_system(x, a, b, A)
    G = Bu.T @ Bu
    G[np.diag_indices_from(G)] += 1e-9 * (np.trace(G) / gap + 1e-30)
    try:
        y = cho_solve(cho_factor(G, lower=False, check_finite=False), Bu.T @ rhs0, check_finite=False)
    except Exception:
        return _lin_fallback(x, a, b)
    return y if np.all(np.isfinite(y)) else _lin_fallback(x, a, b)


def _ar_coefs(seg, order):
    seg = seg - seg.mean()
    s = seg * np.hanning(len(seg))
    r = np.correlate(s, s, 'full')[len(s) - 1:len(s) + order]
    if r[0] <= 0 or not np.all(np.isfinite(r)):
        return None
    r = r.copy()
    r[0] *= 1.0 + 1e-4
    try:
        return solve_toeplitz(r[:order], r[1:order + 1])
    except Exception:
        return None


def _build_system(x, a, b, A):
    """Baseline Janssen/LS-AR normal equations. Returns (Bu, rhs0)."""
    P = len(A) - 1
    gap = b - a
    seg = x[a - P:b + P].copy()
    n = len(seg)
    rows = n - P
    B = np.zeros((rows, n))
    ar = np.arange(rows)
    for k in range(P + 1):
        B[ar, ar + P - k] = A[k]
    unk = np.arange(P, P + gap)
    known = np.ones(n, bool)
    known[unk] = False
    Bu = B[:, unk]
    rhs0 = -B[:, known] @ seg[known]
    return Bu, rhs0


def _pitch(sig):
    """Normalised autocorrelation pitch estimate. Returns (lag, nac)."""
    s = sig - sig.mean()
    n = len(s)
    if n < 2 * PITCH_MAX or _rms(s) < 1e-7:
        return 0, 0.0
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    S = np.fft.rfft(s, nfft)
    ac = np.fft.irfft(S * np.conj(S), nfft)[:PITCH_MAX + 1]
    c = np.concatenate([[0.0], np.cumsum(s * s)])
    lags = np.arange(PITCH_MIN, PITCH_MAX + 1)
    e1 = c[n - lags]            # energy of s[0:n-lag]
    e2 = c[n] - c[lags]         # energy of s[lag:n]
    nac = ac[lags] / (np.sqrt(e1 * e2) + 1e-18)
    j = int(np.argmax(nac))
    lag = int(lags[j])
    best = float(nac[j])
    half = lag // 2             # octave check: prefer the shorter period if nearly as good
    if half >= PITCH_MIN:
        jh = half - PITCH_MIN
        lo = max(jh - 3, 0)
        cand = nac[lo:jh + 4]
        if cand.size and cand.max() > 0.85 * best:
            lag = int(lags[int(np.argmax(cand)) + lo])
            best = max(best, float(cand.max()))
    return lag, best


def _seed(a, b, x):
    h = hashlib.blake2b(digest_size=8)
    h.update(np.ascontiguousarray(x[a - 256:a]).tobytes())
    h.update(np.ascontiguousarray(x[b:b + 256]).tobytes())
    h.update(f"{a}:{b}".encode())
    return int.from_bytes(h.digest(), 'little')


def _preemph(e, k):
    if k <= 0:
        return e
    out = e.copy()
    out[1:] -= k * e[:-1]
    return out


def _xfade(rows):
    t = (np.arange(rows) + 0.5) / rows
    if XFADE == "cos":
        return 0.5 * (1.0 + np.cos(np.pi * t))
    return 1.0 - t


def _pulse_pos(e2s, lo, hi):
    """index of the strongest (smoothed) residual energy in e2s[lo:hi]"""
    return lo + int(np.argmax(e2s[lo:hi]))


def _smooth_energy(e):
    k = np.hanning(11)
    k /= k.sum()
    return np.convolve(e * e, k, mode='same')


def _resample_period(tmpl, frac):
    """sample a periodic template at fractional phase frac in [0,1) (linear, wrapped)"""
    T = len(tmpl)
    p = frac * T
    i0 = np.floor(p).astype(int) % T
    i1 = (i0 + 1) % T
    f = p - np.floor(p)
    return (1.0 - f) * tmpl[i0] + f * tmpl[i1]


# ------------------------------------------------------------------ main
def fill(ctx, a, b, sr):
    x = ctx.copy()
    gap = b - a
    x[a:b] = 0.0
    P = ORDER
    need = max(CTX, RES_LEN + 2 * PITCH_MAX) + P + 8
    if a < need or len(x) - b < need:
        return _short_context(x, a, b)
    coef = _ar_coefs(np.concatenate([x[a - CTX:a], x[b:b + CTX]]), P)
    if coef is None or not np.all(np.isfinite(coef)):
        return _lin_fallback(x, a, b)
    A = np.concatenate([[1.0], -coef])

    # ---- baseline LS-AR fill
    Bu, rhs0 = _build_system(x, a, b, A)
    G = Bu.T @ Bu
    G[np.diag_indices_from(G)] += 1e-9 * (np.trace(G) / gap + 1e-30)
    try:
        cf = cho_factor(G, lower=False, check_finite=False)
    except Exception:
        return _lin_fallback(x, a, b)
    y_ls = cho_solve(cf, Bu.T @ rhs0, check_finite=False)
    if not np.all(np.isfinite(y_ls)):
        return _lin_fallback(x, a, b)

    try:
        y = _excite(x, a, b, A, Bu, cf, y_ls)
        if y is not None and y.shape == (gap,) and np.all(np.isfinite(y)):
            return y
    except Exception:
        pass
    return y_ls


def _target_excitation(x, a, b, P, eL, eR, sigL, sigR, rng):
    gap = b - a
    rows = gap + P
    pos = np.arange(rows)                  # row i <-> position a+i

    TL, nL = _pitch(x[a - RES_LEN:a])
    TR, nR = _pitch(x[b + P:b + P + RES_LEN])
    voicedL = EXC_MODE != "noise" and nL >= VOICED_NAC
    voicedR = EXC_MODE != "noise" and nR >= VOICED_NAC
    mode = "noise"

    if EXC_MODE == "warp" and voicedL and voicedR:
        # --- single time-warped pulse train from the last left pulse to the first right pulse
        e2L = _smooth_energy(eL)
        e2R = _smooth_energy(eR)
        iL = _pulse_pos(e2L, RES_LEN - TL, RES_LEN)      # last left pulse (index in eL)
        iR = _pulse_pos(e2R, 0, TR)                      # first right pulse (index in eR)
        tol = max(TL, TR) // PULSE_TOL
        iL0 = _pulse_pos(e2L, iL - TL - tol, iL - TL + tol + 1)   # previous left pulse
        iR2 = _pulse_pos(e2R, iR + TR - tol, iR + TR + tol + 1)   # next right pulse
        TLa = iL - iL0                                   # actual local periods
        TRa = iR2 - iR
        pL = iL - RES_LEN                                # position rel. to a (negative)
        pR = rows + iR                                   # position rel. to a (>= rows)
        D = pR - pL
        Tavg = 0.5 * (TLa + TRa)
        m_lo = max(1, int(np.floor(D / Tavg)))
        cands = [m for m in (m_lo, m_lo + 1) if m >= 1]
        m = min(cands, key=lambda mm: abs(np.log(D / (mm * Tavg))))
        Tw = D / m
        tmplL = eL[iL0:iL]                               # previous period, pulse at index 0
        tmplR = eR[iR:iR2]                               # next period, pulse at index 0
        theta = (pos - pL) / Tw
        frac = theta - np.floor(theta)
        sL = _resample_period(tmplL, frac)
        sR = _resample_period(tmplR, frac)
        w = _xfade(rows)
        et = np.sqrt(w) * sL + np.sqrt(1.0 - w) * sR
        mode = "warp"
        LAST.update(m=m, Tw=Tw, D=D, pL=pL, pR=pR)
    else:
        if voicedL:
            mm = (pos // TL) + 1
            etL = eL[RES_LEN + pos - mm * TL]            # always < a
        elif UNVOICED_SRC == "copy":
            etL = eL[RES_LEN - rows:]                    # the residual just before a
        else:
            etL = sigL * rng.standard_normal(rows)
        if voicedR:
            kk = ((rows - 1 - pos) // TR) + 1
            etR = eR[pos + kk * TR - rows]               # always >= b+P
        elif UNVOICED_SRC == "copy":
            etR = eR[:rows]                              # the residual just after b+P
        else:
            etR = sigR * rng.standard_normal(rows)
        w = _xfade(rows)
        et = np.sqrt(w) * etL + np.sqrt(1.0 - w) * etR
        mode = "pitch" if (voicedL or voicedR) else "unvoiced"
    LAST.update(TL=TL, TR=TR, nL=nL, nR=nR, mode=mode)
    return _preemph(et, HF_PREEMPH), (TL, nL, TR, nR)


def _excite(x, a, b, A, Bu, cf, y_ls):
    P = len(A) - 1
    gap = b - a

    # ---- residual (excitation) on both contexts
    eL = lfilter(A, [1.0], x[a - RES_LEN - P:a])[P:]      # positions a-RES_LEN .. a-1
    eR = lfilter(A, [1.0], x[b:b + RES_LEN + P])[P:]      # positions b+P .. b+P+RES_LEN-1
    sigL = _rms(eL[-LVL_LEN:])
    sigR = _rms(eR[:LVL_LEN])
    if not (np.isfinite(sigL) and np.isfinite(sigR)):
        return None

    rng = np.random.default_rng(_seed(a, b, x))
    et, (TL, nL, TR, nR) = _target_excitation(x, a, b, P, eL, eR, sigL, sigR, rng)

    # ---- conditional response to the excitation
    d = cho_solve(cf, Bu.T @ et, check_finite=False)
    if not np.all(np.isfinite(d)):
        return None

    # ---- energy matching against the neighbour level
    L = max(gap, NB_LEN_MIN)
    rL = _rms(x[a - L:a])
    rR = _rms(x[b:b + L])
    if TARGET_MODE == "sync":
        # voiced side: energy of the same pitch phase one (or more) periods away
        if nL >= VOICED_NAC and TL > 0:
            k = int(np.ceil(gap / TL))
            rL = _rms(x[a - k * TL:b - k * TL])
        if nR >= VOICED_NAC and TR > 0:
            k = int(np.ceil(gap / TR))
            rR = _rms(x[a + k * TR:b + k * TR])
    r_t = np.sqrt(rL * rR) if TARGET_MEAN == "geo" else 0.5 * (rL + rR)
    target_E = gap * r_t * r_t
    c0 = float(y_ls @ y_ls)
    c1 = float(y_ls @ d)
    c2 = float(d @ d) + 1e-30
    disc = c1 * c1 - c2 * (c0 - target_E)
    if disc >= 0:
        g_raw = max((-c1 + np.sqrt(disc)) / c2, 0.0)
    else:
        g_raw = G_MIN
    g = float(np.clip(g_raw ** G_SOFT if g_raw > 0 else 0.0, G_MIN, G_MAX))

    # ---- peak guard: never exceed the neighbour peak by more than the margin
    nb_peak = max(np.abs(x[a - L:a]).max(), np.abs(x[b:b + L]).max()) * PEAK_MARGIN
    y = y_ls + g * d
    if np.abs(y).max() > nb_peak > 0:
        lo, hi = 0.0, g
        for _ in range(12):
            mid = 0.5 * (lo + hi)
            if np.abs(y_ls + mid * d).max() > nb_peak:
                hi = mid
            else:
                lo = mid
        g = lo
        y = y_ls + g * d
    LAST.update(g=g, g_raw=g_raw, sigL=sigL, sigR=sigR, rL=rL, rR=rR,
                e_ls=np.sqrt(c0 / gap), e_d=np.sqrt(c2 / gap))
    return y
