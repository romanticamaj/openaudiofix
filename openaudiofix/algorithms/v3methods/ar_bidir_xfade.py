"""ar_bidir_xfade -- two-sided high-order AR extrapolation joined by a raised-cosine crossfade.

Idea: an LS-AR interpolation is the *minimum-energy* signal consistent with an AR model, so it
shrinks (dull, quiet fill).  Instead we fit a long AR model (order P=512, context NCTX=8192,
autocorrelation method, half-Hann taper on the far half) on the LEFT context and run it forward
through the hole, fit another on the RIGHT context and run it backward, and blend the two with a
raised cosine.  Each one-sided continuation keeps the waveform, pitch and timbre of its own side
and starts exactly from its own boundary samples.

Extra care (all deterministic; RNG seeded from a, b, gap and a context statistic):
  * excitation: the zero-input response (ZIR) is the conditional mean and loses its 2-8 kHz
    content within tens of samples.  We add a zero-state response (ZSR) driven by a deterministic
    excitation with the residual's spectral colour (AR(16) on the residual next to the hole) and
    its pitch-synchronous temporal envelope, ramped in over 16 samples at the join.
  * complementary gain: the excitation is scaled per fill so that, over the crossfade-weighted
    span, power(ZIR) + g^2 power(ZSR) in 1.5-12 kHz equals the context's own band power (times the
    envelope trend, +COMP_BIAS dB); g is capped at 1, i.e. never above the model's stationary
    level.  Measured on this speaker the ZIR still holds 30-50 % of the HF power, so a fixed
    excitation would overshoot by 1-3 dB; this rule keeps LF deterministic and fills HF only as
    far as needed.  An over-large ZSR realisation is dropped (pure ZIR), not passed on.
  * envelope drift: each continuation's short-time RMS (WENV=384) is steered to the log-linear
    trend between the two sides (gain clamped to 1/4..4, ramped in over 64 samples).
  * crossfade loss: with weights w,1-w two partly-correlated signals lose power in the middle;
    we measure their weighted correlation rho and apply s=1/sqrt(w^2+(1-w)^2+2 rho w(1-w)),
    clamped to [1, SMAX=2]; s is exactly 1 at both joins.
  * final level match: one smooth gain (ramped over 32 samples at each join, clamped +-3 dB)
    makes the fill RMS equal the geometric mean of its equal-length neighbours' RMS.
  * fallback: AR fit failure / non-finite / unstable ZIR -> baseline LS-AR(48) (copy in
    ar_bidir_xfade_ls.py).  PREEMPH (pre-emphasis) is available but off: it made no difference.
Parameters can be overridden through ABX_* environment variables (used only for tuning).
"""
import os, sys
import numpy as np
from scipy.linalg import solve_toeplitz, LinAlgError
from scipy.signal import lfilter
from .ar_bidir_xfade_ls import ls_fill

def _env(name, default, cast=float):
    v = os.environ.get("ABX_" + name)
    return cast(v) if v is not None else default

P        = _env("P", 512, int)       # AR order (must exceed the longest pitch period, 436)
NCTX     = _env("NCTX", 8192, int)   # fitting context per side
TAPER    = _env("TAPER", 0.5)        # fraction of the far end of the context under a half-Hann rise
REG      = _env("REG", 1e-5)         # white-noise correction on r[0]
PREEMPH  = _env("PREEMPH", 0.0)      # pre-emphasis coefficient (0 = off)
NOISE    = _env("NOISE", 1.0)        # excitation level relative to residual RMS (0 = pure extrapolation)
NRES     = _env("NRES", 1536, int)   # samples next to the hole used for the residual statistics
RESCOL   = _env("RESCOL", 16, int)   # AR order used to colour the excitation like the residual (0 = white)
PSHAPE   = _env("PSHAPE", 1, int)    # 1 = pitch-synchronous temporal envelope on the excitation
EXC_RAMP = _env("EXC_RAMP", 16, int) # excitation fade-in length at the join (samples)
EXC_MODE = _env("EXC_MODE", "comp", str) # "comp": excitation scaled so ZIR+ZSR matches the context HF power; "fixed": NOISE
COMP_LO  = _env("COMP_LO", 1500.0)   # complementary band (Hz)
COMP_HI  = _env("COMP_HI", 12000.0)
COMP_TAIL= _env("COMP_TAIL", 1024, int) # context samples used for the target band power
COMP_MAX = _env("COMP_MAX", 1.0)     # cap on the complementary gain (1 = never above the model's stationary level)
COMP_BIAS= _env("COMP_BIAS", 3.0)    # dB added to the HF target of the complementary rule
ENV_RAMP = _env("ENV_RAMP", 64, int) # envelope-steering gain ramp-in length at the join (samples)
POST     = _env("POST", 1, int)      # 1 = final RMS match to the equal-length neighbours (smooth, clamped gain)
POST_NB  = _env("POST_NB", 96, int) # minimum neighbour length used for the final RMS match
POST_MAX = _env("POST_MAX", 3.0)     # clamp of the final gain in dB (both directions)
POST_FRAC= _env("POST_FRAC", 1.0)    # fraction of the measured dB deficit that is corrected
POST_RAMP= _env("POST_RAMP", 32, int) # ramp of the final gain at each join (samples)
NFFT     = 4096
WENV     = _env("WENV", 384, int)    # short-time RMS window (8 ms ~ 1 pitch period)
GMAX     = _env("GMAX", 4.0)         # per-side envelope gain clamp (and 1/GMAX)
SMAX     = _env("SMAX", 2.0)         # crossfade power-compensation clamp
ENVFIX   = _env("ENVFIX", 1, int)    # 1 = envelope steering on
RHOFIX   = _env("RHOFIX", 1, int)    # 1 = crossfade power compensation on
DEBUG    = _env("DEBUG", 0, int)
UNSTABLE = 3.0                       # |continuation| > UNSTABLE * max|context tail|  -> fallback
TMIN, TMAX = 180, 520                # pitch-period search range (92-267 Hz)

# ---------------------------------------------------------------- helpers
def _autocorr(x, nlag):
    n = len(x); nfft = 1 << int(np.ceil(np.log2(2 * n)))
    X = np.fft.rfft(x, nfft)
    return np.fft.irfft(X * np.conj(X), nfft)[:nlag + 1]

def _fit_ar(seg, order, taper, reg):
    """Autocorrelation-method AR fit on `seg` (far end first, hole end last).  Returns
    predictor coefficients a (x[n] ~ sum a_k x[n-k]) or None."""
    n = len(seg)
    w = np.ones(n); nt = int(taper * n)
    if nt > 1:
        w[:nt] = 0.5 * (1 - np.cos(np.pi * np.arange(nt) / nt))
    r = _autocorr(seg * w, order)
    if not np.isfinite(r[0]) or r[0] <= 0:
        return None
    r = r / r[0]; r[0] = 1.0 + reg
    try:
        a = solve_toeplitz(r[:order], r[1:order + 1])
    except (LinAlgError, ValueError):
        return None
    if not np.all(np.isfinite(a)):
        return None
    return a

def _extrapolate(past, a, n):
    """Zero-input continuation of the AR recursion for n samples after `past`."""
    p = len(a); ar = a[::-1]
    buf = np.empty(p + n); buf[:p] = past[-p:]
    for i in range(n):
        buf[p + i] = buf[i:i + p] @ ar
    return buf[p:]

def _st_rms(x, W):
    w = np.hanning(W); w /= w.sum()
    return np.sqrt(np.convolve(x * x, w, 'same') + 1e-30)

def _pitch(tail):
    """Normalised-autocorrelation pitch period of `tail` and its voicing strength."""
    n = len(tail); r = _autocorr(tail, TMAX)
    if r[0] <= 0: return None, 0.0
    # normalise by the energies of the two overlapping parts (approximate, cheap)
    lags = np.arange(TMIN, TMAX + 1)
    c2 = np.concatenate([[0.0], np.cumsum(tail * tail)])
    e0 = c2[n - lags] - c2[0]; e1 = c2[n] - c2[lags]
    nr = r[lags] / (np.sqrt(e0 * e1) + 1e-30)
    k = int(np.argmax(nr)); return int(lags[k]), float(nr[k])

def _excitation(res, n_out, rng):
    """Deterministic excitation with the residual's RMS, spectral colour and (if voiced)
    pitch-synchronous temporal envelope.  `res` is the residual next to the hole (hole end last)."""
    sig = np.sqrt(np.mean(res ** 2)) + 1e-30
    e = rng.standard_normal(n_out)
    if PSHAPE:
        T, v = _pitch(res[-2048:] if len(res) >= 2048 else res)
        vs = float(np.clip((v - 0.35) / 0.35, 0.0, 1.0)) if T is not None else 0.0
        if vs > 0 and len(res) >= 2 * T:
            win = np.hanning(49); win /= win.sum()
            env = np.sqrt(np.convolve(res * res, win, 'same') + 1e-30)
            # fold the last two periods into one and continue it periodically from the join
            envT = 0.5 * (env[-T:] + env[-2 * T:-T])
            envT = envT / (np.sqrt(np.mean(envT ** 2)) + 1e-30)
            shape = envT ** vs
            shape = shape / (np.sqrt(np.mean(shape ** 2)) + 1e-30)
            e = e * shape[np.arange(n_out) % T]
    if RESCOL > 0:
        c = _fit_ar(res, RESCOL, 0.0, 1e-6)
        if c is not None:
            Ac = np.concatenate([[1.0], -c])
            e = lfilter([1.0], Ac, e)
            resw = lfilter(Ac, [1.0], res)[RESCOL:]          # whitened residual sets the gain
            sig = np.sqrt(np.mean(resw ** 2)) + 1e-30
    e = e * (NOISE * sig)
    if EXC_RAMP > 0:
        nr = min(EXC_RAMP, n_out)
        e[:nr] *= 0.5 * (1 - np.cos(np.pi * (np.arange(nr) + 0.5) / nr))
    return e

def _band_power(x, win, sel):
    """Per-sample power of x*win inside the FFT-bin selection sel (same NFFT for all callers)."""
    X = np.abs(np.fft.rfft(x * win, NFFT)) ** 2
    return X[sel].sum() / (np.sum(win ** 2) + 1e-30)

def _comp_gain(zir, zsr, seg, gap, trend2, sr):
    """Scale factor for the excitation so that, over the crossfade-weighted span of the hole,
    band power(ZIR) + g^2 band power(ZSR) = band power of the context tail * envelope trend."""
    f = np.fft.rfftfreq(NFFT, 1.0 / sr); sel = (f >= COMP_LO) & (f < COMP_HI)
    w = 0.5 * (1 + np.cos(np.pi * (np.arange(gap) + 1) / (gap + 1)))
    Z = _band_power(zir[:gap], w, sel); S = _band_power(zsr[:gap], w, sel)
    nt = min(COMP_TAIL, len(seg)); T = _band_power(seg[-nt:], np.hanning(nt), sel) * trend2 * 10 ** (COMP_BIAS / 10)
    return float(np.sqrt(np.clip((T - Z) / (S + 1e-30), 0.0, COMP_MAX ** 2)))

def _one_side(seg, n_out, rng, gap=None, trend2=1.0, sr=48000):
    """seg: mean-removed context with the hole end LAST.  Returns n_out samples of continuation
    (zero-input + excited zero-state) in the ORIGINAL domain, or None."""
    if PREEMPH > 0:
        segp = lfilter([1.0, -PREEMPH], [1.0], seg)
    else:
        segp = seg
    a = _fit_ar(segp, P, TAPER, REG)
    if a is None:
        return None
    cont = _extrapolate(segp, a, n_out)
    tail_max = np.max(np.abs(segp[-2048:])) + 1e-12
    if not np.all(np.isfinite(cont)) or np.max(np.abs(cont)) > UNSTABLE * tail_max:
        return None
    if NOISE > 0:
        A = np.concatenate([[1.0], -a])
        res = lfilter(A, [1.0], segp[-(NRES + P):])[P:]        # residual next to the hole
        exc = _excitation(res, n_out, rng)
        zsr = lfilter([1.0], A, exc)
        if EXC_MODE == "comp" and gap is not None and np.all(np.isfinite(zsr)):
            zsr = zsr * _comp_gain(cont, zsr, segp, gap, trend2, sr)
        # an over-large excited part (residual spike, resonant realisation) is dropped, not
        # allowed to trigger the LS fallback: the pure extrapolation is still a smooth fill
        if np.all(np.isfinite(zsr)) and np.max(np.abs(zsr)) <= UNSTABLE * tail_max:
            cont = cont + zsr
        elif DEBUG:
            print("[abx] excitation dropped (too large)", file=sys.stderr)
    if PREEMPH > 0:
        cont = lfilter([1.0], [1.0, -PREEMPH], cont, zi=[PREEMPH * seg[-1]])[0]
        tail_max = np.max(np.abs(seg[-2048:])) + 1e-12
        if not np.all(np.isfinite(cont)) or np.max(np.abs(cont)) > UNSTABLE * tail_max:
            return None
    return cont

def _steer_envelope(cont, past, target):
    """Scale `cont` (len n_out >= gap) so that its short-time RMS follows `target` (len gap).
    Gain is clamped, and ramped in from exactly 1 at the join."""
    gap = len(target)
    seq = np.concatenate([past[-WENV:], cont])
    env = _st_rms(seq, WENV)[WENV:WENV + gap]
    g = np.clip(target / env, 1.0 / GMAX, GMAX)
    nr = min(ENV_RAMP, gap)
    ramp = np.ones(gap); ramp[:nr] = 0.5 * (1 - np.cos(np.pi * np.arange(nr) / nr))
    g = 1.0 + (g - 1.0) * ramp
    return cont[:gap] * g

def _post_match(y, left0, right0, gap):
    """Single smooth gain so that the fill RMS matches the geometric mean of the RMS of the
    neighbours (length max(gap, POST_NB)); gain is 1 at both joins, clamped to +-POST_MAX dB."""
    n = max(gap, POST_NB)
    rl = np.sqrt(np.mean(left0[-n:] ** 2)); rr = np.sqrt(np.mean(right0[:n] ** 2))
    tgt = np.sqrt(rl * rr) + 1e-12
    cur = np.sqrt(np.mean(y ** 2)) + 1e-12
    want_db = np.clip(POST_FRAC * 20 * np.log10(tgt / cur), -POST_MAX, POST_MAX)
    if abs(want_db) < 0.05:
        return y
    tgt = cur * 10 ** (want_db / 20)
    nr = max(1, min(POST_RAMP, gap // 4))
    sh = np.ones(gap); r = 0.5 * (1 - np.cos(np.pi * (np.arange(nr) + 1) / (nr + 1)))
    sh[:nr] = r; sh[gap - nr:] = r[::-1]
    y2 = y * y
    A = np.mean(y2 * sh * sh); B = np.mean(y2 * sh * (1 - sh)); C = np.mean(y2 * (1 - sh) ** 2)
    # solve A G^2 + 2 B G + C = tgt^2 for G > 0
    disc = B * B - A * (C - tgt ** 2)
    if A <= 0 or disc < 0:
        return y
    G = (-B + np.sqrt(disc)) / A
    G = float(np.clip(G, 10 ** (-POST_MAX / 20), 10 ** (POST_MAX / 20)))
    return y * (1.0 + (G - 1.0) * sh)

# ---------------------------------------------------------------- main entry
def fill(ctx, a, b, sr):
    gap = b - a
    try:
        y = _fill(ctx, a, b, sr)
        if y is not None and y.shape == (gap,) and np.all(np.isfinite(y)):
            return y
        if DEBUG: print(f"[abx] fallback (None/non-finite) a={a} b={b}", file=sys.stderr)
    except Exception as ex:
        if DEBUG: print(f"[abx] fallback (exception {ex!r}) a={a} b={b}", file=sys.stderr)
    return ls_fill(ctx, a, b, sr)

def _fill(ctx, a, b, sr):
    gap = b - a
    nL = min(NCTX, a); nR = min(NCTX, len(ctx) - b)
    if nL < 2 * P or nR < 2 * P:
        return None
    left = np.asarray(ctx[a - nL:a], dtype=np.float64)
    right = np.asarray(ctx[b:b + nR], dtype=np.float64)
    if not (np.all(np.isfinite(left)) and np.all(np.isfinite(right))):
        return None
    mL = left.mean(); mR = right.mean()
    left0 = left - mL; right0 = right - mR
    # deterministic seed from the hole position and simple context statistics
    stat = int(min(np.sum(np.abs(left0[-1024:])) + np.sum(np.abs(right0[:1024])), 1e9) * 1e6) & 0xFFFFFFFF
    rng = np.random.default_rng([a, b, stat, gap])

    # target envelope: log-linear between RMS of the last WENV of left (anchored at a-WENV/2)
    # and RMS of the first WENV of right (anchored at b+WENV/2)
    rL = np.sqrt(np.mean(left0[-WENV:] ** 2)) + 1e-9
    rR = np.sqrt(np.mean(right0[:WENV] ** 2)) + 1e-9
    tpos = (np.arange(gap) + 0.5 + WENV / 2) / (gap + WENV)
    target = np.exp(np.log(rL) + (np.log(rR) - np.log(rL)) * tpos)
    w = 0.5 * (1 + np.cos(np.pi * (np.arange(gap) + 1) / (gap + 1)))   # crossfade weight of the left side
    trendL = float(np.sum(w * (target / rL) ** 2) / np.sum(w))          # expected power change over the span each side serves
    trendR = float(np.sum((1 - w) * (target / rR) ** 2) / np.sum(1 - w))

    n_out = gap + WENV                      # extrapolate a bit past the hole for the envelope
    cL = _one_side(left0, n_out, rng, gap, trendL, sr)
    cR = _one_side(right0[::-1], n_out, rng, gap, trendR, sr)
    if cL is None or cR is None:
        return None
    cR = cR[::-1]                           # forward time: [extension (WENV) | gap]

    if ENVFIX:
        fL = _steer_envelope(cL, left0, target)
        fR = _steer_envelope(cR[::-1], right0[::-1], target[::-1])[::-1]
    else:
        fL = cL[:gap]; fR = cR[-gap:]

    # raised-cosine crossfade, w=1 at the virtual sample a-1 and 0 at b
    y = w * fL + (1 - w) * fR
    if RHOFIX:
        wt = w * (1 - w)
        num = np.sum(wt * fL * fR)
        den = np.sqrt(np.sum(wt * fL * fL) * np.sum(wt * fR * fR)) + 1e-30
        rho = float(np.clip(num / den, -1.0, 1.0))
        s = 1.0 / np.sqrt(np.maximum(w * w + (1 - w) ** 2 + 2 * rho * w * (1 - w), 1e-6))
        y = y * np.clip(s, 1.0, SMAX)
    if POST:
        y = _post_match(y, left0, right0, gap)
    return y + (w * mL + (1 - w) * mR)
