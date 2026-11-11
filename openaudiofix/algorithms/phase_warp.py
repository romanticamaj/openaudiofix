"""phase_warp - phase-aligned patching (the core).

Problem restated: a seam is a splice caused by dropped samples; the left side x[:za] and the right side x[zb:] are
both intact real recordings, except that the right side is ahead of the left by d samples (d unknown). For voiced speech
with period P, the glottal-pulse phases of the two sides differ by (d mod P).
Any fixed-length short-hole interpolation has to squeeze one abnormally long period (a one-off large jitter) into the
hole; that is why "the voice does not flow through".

Method (precedent: Zopf/Thyssen/Chen 2007 "Time-warping and re-phasing in packet loss concealment", G.722 App. III):
  1. Estimate the period on each side (normalized autocorrelation) and the phase offset s: delaying the right side by
     s samples puts it in phase with the left.
  2. Distribute s over the known signal on both sides: a smooth time warp (raised-cosine ramp, instantaneous rate
     deviation <= RATE_MAX) moves the left content by -αs and the right content by +(1-α)s; the farther from the seam,
     the smaller the shift, and outside W it is bit-exact unchanged.
  3. After warping, the two sides are in phase at the junction: when s<0 the real data of the two sides overlap →
     a short crossfade where the difference is smallest;
     when s>0 a small hole of <= about 0.4 P remains, which is then a standard "real gap" interpolation problem →
     handed to the per-hole selector.
Total output length is unchanged. Joint multi-track decision: tracks of the same source use the same s (one time
map), preserving the inter-track phase relationships.
"""
import numpy as np
from scipy.linalg import solve_toeplitz

LAG_MIN_MS, LAG_MAX_MS = 2.5, 12.5
TPL_LONG = 1024
SUB_FRAC = 0.90
VOICED_THR = 0.60
PERIOD_TOL = 0.15
RHO_MIN = 0.45          # lower bound of the mean NCC of the two directions at the best shift
MATCH_H = 0.75          # phase-matching window length (in periods)
RATE_MAX = 0.03         # target upper bound on the warp's instantaneous rate deviation
RATE_HARD = 0.05        # maximum allowed when the available width is insufficient; above this, no warping
W_MIN, W_MAX = 720, 6000
W_AVAIL_MIN = 360
XF_FRAC, XF_MIN, XF_MAX = 0.35, 48, 160
XF_MARGIN = 24
EXT_FADE = 64
MIN_HOLE = 32
S_MAX_FRAC = 0.80       # upper limit of |s| (in periods)
WARP_WEIGHT = 1.0       # choosing the s branch: J = hole length + WARP_WEIGHT*|s|
S_ABS_MAX = 380         # absolute upper limit of |s| (determined by W_MAX and RATE_HARD)
# when one side has no room to warp, the warped side needs at least this many clean samples for a one-sided warp
ONE_SIDED_ROOM = 500
# its own s within this distance of the dominant track's s -> adopt the dominant track's s directly (one time map)
FOLLOW_DS = 4
# a non-voiced track at least this many dB below the dominant track -> follow the dominant track's time map
FOLLOW_LEVEL_DB = 10.0
SAME_SRC_P_TOL = 0.06
GUARD_D2_ABS, GUARD_D2_REL = 15.0, 2.5
SINC_HALF = 16
CT = 4096               # unwarped context attached on each side when a small hole is handed to the selector
NEED = TPL_LONG + int(LAG_MAX_MS * 48) + 8

# ---------------------------------------------------------------- pitch analysis (same estimator as pitch_wsola)
def _lag_corr(seg, tpl, lmin, lmax):
    n = len(seg); t = seg[n - tpl:]; et = np.dot(t, t)
    s = seg[n - tpl - lmax:]
    c = np.correlate(s, t, 'valid'); cs = np.concatenate([[0.0], np.cumsum(s * s)]); e = cs[tpl:] - cs[:-tpl]
    return (c / np.sqrt(et * e + 1e-30))[::-1][lmin:lmax + 1]

def _pick_lag(r, lmin):
    imax = int(np.argmax(r)); rmax = float(r[imax])
    lm = np.flatnonzero((r[1:-1] >= r[:-2]) & (r[1:-1] >= r[2:])) + 1
    cand = lm[r[lm] >= SUB_FRAC * rmax] if len(lm) else np.array([], int)
    i0 = int(cand.min()) if len(cand) else imax
    return lmin + i0, rmax

def _refine(seg, P0, lmin, lmax):
    tpl = int(np.clip(1.5 * P0, 160, TPL_LONG)); lo = max(lmin, int(P0 * 0.85)); hi = min(lmax, int(P0 * 1.15) + 1)
    if hi - lo < 2: return int(P0)
    return lo + int(np.argmax(_lag_corr(seg, tpl, lo, hi - 1)))

def analyse_side(seg, sr):
    """seg ends at the boundary (reverse the right side first). Returns (P, rmax)"""
    lmin = int(round(LAG_MIN_MS * 1e-3 * sr)); lmax = int(round(LAG_MAX_MS * 1e-3 * sr))
    seg = seg - seg.mean(); r = _lag_corr(seg, TPL_LONG, lmin, lmax)
    P0, rmax = _pick_lag(r, lmin)
    return _refine(seg, P0, lmin, lmax), rmax

# ---------------------------------------------------------------- helpers
def _raised(n): return 0.5 - 0.5 * np.cos(np.pi * (np.arange(n) + 0.5) / n)
def _ncc(a, b): return float(np.dot(a, b) / np.sqrt(np.dot(a, a) * np.dot(b, b) + 1e-30))

_k = np.arange(-SINC_HALF + 1, SINC_HALF + 1)
def sinc_interp(x, t):
    """Value of x at real-valued position t (Kaiser-windowed sinc, 32 taps). At integer positions returns the original
    sample."""
    i = np.floor(t).astype(np.int64); f = t - i
    arg = _k[None, :] - f[:, None]
    w = np.i0(9.0 * np.sqrt(np.clip(1 - (arg / SINC_HALF) ** 2, 0, 1))) / np.i0(9.0)
    h = np.sinc(arg) * w; h /= h.sum(axis=1, keepdims=True)
    return np.einsum('ij,ij->i', x[i[:, None] + _k[None, :]], h)

def _ar_extrap(seg, n, order=32):
    """AR-predict n samples forward from the end of seg: a continuous extension for sinc interpolation beyond the
    boundary, avoiding boundary ringing and never reading across to the other side of the seam."""
    m = seg.mean(); v = seg - m
    r = np.correlate(v, v, 'full')[len(v) - 1:len(v) + order]
    if r[0] <= 0: return np.full(n, seg[-1])
    r = r.copy(); r[0] *= 1.0001
    try: a = solve_toeplitz(r[:order], r[1:order + 1])
    except Exception: return np.full(n, seg[-1])
    buf = list(v[-order:])
    for _ in range(n): buf.append(float(np.dot(a, buf[:-order - 1:-1])))
    out = np.array(buf[order:]) + m
    ok = np.all(np.isfinite(out)) and np.max(np.abs(out)) < 4 * (np.max(np.abs(seg[-256:])) + 1e-9)
    return out if ok else np.full(n, seg[-1])

def estimate_shift(x, za, zb, PL, PR):
    """Returns (ss, rho): rho[k] is the mean NCC of the two sides (each periodically extended) near the junction when
    "the right side is delayed by ss[k] samples"."""
    P = int(round((PL + PR) / 2)); h = int(round(MATCH_H * P)); smax = int(min(max(S_MAX_FRAC * P, (zb - za) + 0.6 * P), S_ABS_MAX)) + 2
    def EL(u): return np.where(u < za, x[np.minimum(u, za - 1)], x[za - PL + ((u - za) % PL)])
    def ER(u): return np.where(u >= zb, x[np.maximum(u, zb)], x[zb + ((u - zb) % PR)])
    uR = np.arange(zb, zb + h); uL = np.arange(za - h, za); segR = x[uR]; segL = x[uL]
    ss = np.arange(-smax, smax + 1); rho = np.empty(len(ss))
    for k, s in enumerate(ss): rho[k] = 0.5 * (_ncc(segR, EL(uR + s)) + _ncc(segL, ER(uL - s)))
    return ss, rho

def choose_shift(ss, rho, P, dead):
    """Among local maxima where rho is high enough (>= max-0.03), pick the one with the smallest
    J = hole length + WARP_WEIGHT*|s|; refine with a parabola."""
    kmax = int(np.argmax(rho)); good = np.flatnonzero(rho >= rho[kmax] - 0.03)
    loc = [k for k in good if (k == 0 or rho[k] >= rho[k - 1]) and (k == len(ss) - 1 or rho[k] >= rho[k + 1])] or [kmax]
    kb = min(loc, key=lambda k: max(0, dead + ss[k]) + WARP_WEIGHT * abs(ss[k])); s = float(ss[kb])
    if 0 < kb < len(ss) - 1:
        y0, y1, y2 = rho[kb - 1], rho[kb], rho[kb + 1]; den = y0 - 2 * y1 + y2
        if den < 0: s += float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
    return s, float(rho[kb]), float(rho[len(ss) // 2])

def analyse_track(x, za, zb, sr):
    PL, rL = analyse_side(x[za - NEED:za], sr); PR, rR = analyse_side(x[zb:zb + NEED][::-1], sr)
    level = 10 * np.log10(np.mean(np.concatenate([x[za - 1024:za], x[zb:zb + 1024]]) ** 2) + 1e-14)
    out = dict(PL=int(PL), PR=int(PR), rL=float(rL), rR=float(rR), level=float(level), conf=False)
    if rL < VOICED_THR or rR < VOICED_THR or abs(PL - PR) / min(PL, PR) > PERIOD_TOL: return out
    ss, rho = estimate_shift(x, za, zb, PL, PR); s, r, r0 = choose_shift(ss, rho, 0.5 * (PL + PR), zb - za)
    out.update(s=s, rho=r, rho0=r0, conf=bool(r >= RHO_MIN)); return out

# ---------------------------------------------------------------- rendering
def _warp_side(x, edge, W, D, left):
    """Smoothly shift the known signal of W samples next to edge by D (the whole neighborhood of the junction shifts by
    D, 0 beyond W).
    left=True: data is x[:edge], returns (u0, y), y covers output positions u0..end of the shifted data.
    left=False: data is x[edge:], y starts from the start of the shifted data, returns (beg, y)."""
    if W <= 0:                                  # this side is not warped: take the original samples directly
        K = 2048
        return (edge - K, x[edge - K:edge].copy()) if left else (edge, x[edge:edge + K + 1].copy())
    if left:
        u1 = edge - 1 + min(D, 0.0); u0 = int(np.floor(u1 - W)); end = int(np.floor(edge - 1 + D))
        u = np.arange(u0, end + 1, dtype=np.float64)
        r = np.clip((u - u0) / (u1 - u0), 0, 1); r = 0.5 - 0.5 * np.cos(np.pi * r)
        t = np.minimum(u - D * r, edge - 1.0); base = u0 - SINC_HALF - 2
        pad = np.concatenate([x[base:edge], _ar_extrap(x[edge - 1024:edge], SINC_HALF + 1)])
        return u0, sinc_interp(pad, t - base)
    u1 = edge + max(D, 0.0); beg = int(np.ceil(edge + D)); uend = int(np.ceil(u1 + W))
    u = np.arange(beg, uend + 1, dtype=np.float64)
    r = np.clip((uend - u) / (uend - u1), 0, 1); r = 0.5 - 0.5 * np.cos(np.pi * r)
    t = np.maximum(u - D * r, float(edge)); off = edge - SINC_HALF - 1
    pad = np.concatenate([_ar_extrap(x[edge:edge + 1024][::-1], SINC_HALF + 1)[::-1], x[edge:uend + SINC_HALF + 4]])
    return beg, sinc_interp(pad, t - off)

# the warped side's width must be at least this multiple of that side's pitch period (+ a fixed margin); see the note
# below
PERIOD_SAFETY = 1.5


def plan_warp(s, WL_av, WR_av, PL=0, PR=0, roomL=10 ** 9, roomR=10 ** 9):
    """Returns (WL, WR, DL, DR, rate) or None (not enough room, or geometrically unsafe - see below).
    W_av: warpable width; room: number of clean samples up to the neighboring patch zone; PL/PR: pitch periods of the
    two sides, used to block a boundary case that only occurs on real recordings with dense defects: it never triggered
    when validating a whole long real recording, and was found later when this core algorithm was used on ordinary
    uploaded files (seams denser, neighboring patch windows squeezing the usable width below the pitch period).
    The periodic extension in `render()` (repeating the W-sample warped result with period PL/PR toward the junction)
    assumes W has at least one period of slack; with W stuck between 0 and one period (e.g. WR=426 but PR=472) the
    index goes past the actual array length and raises. The only two safe states are "do not warp that side at all"
    (W=0, which takes the fixed-window branch of `_warp_side` below and is naturally long enough) or "warp width
    clearly greater than one period"; anything in between is treated as unsafe, and the caller falls back to the safe
    path (`ensemble_select`) rather than forcing a warp."""
    okL = WL_av >= W_AVAIL_MIN; okR = WR_av >= W_AVAIL_MIN
    if not okL and not okR: return None
    if (not okL and roomL < ONE_SIDED_ROOM) or (not okR and roomR < ONE_SIDED_ROOM): return None
    if okL and okR:
        Wn = int(np.clip(abs(s) / 2 * (np.pi / 2) / RATE_MAX, W_MIN, W_MAX))
        WL = int(min(Wn, WL_av)); WR = int(min(Wn, WR_av))
        # one side limited → the other side takes on more
        if WL < Wn and WR_av > WR: WR = int(min(2 * Wn - WL, WR_av, W_MAX))
        if WR < Wn and WL_av > WL: WL = int(min(2 * Wn - WR, WL_av, W_MAX))
    else:
        Wn = int(np.clip(abs(s) * (np.pi / 2) / RATE_MAX, W_MIN, W_MAX))
        WL = int(min(Wn, WL_av)) if okL else 0; WR = int(min(Wn, WR_av)) if okR else 0
    if 0 < WL < PL * PERIOD_SAFETY + 32: return None
    if 0 < WR < PR * PERIOD_SAFETY + 32: return None
    rate = abs(s) * (np.pi / 2) / (WL + WR)
    if rate > RATE_HARD: return None
    a = WL / (WL + WR); return WL, WR, -a * s, (1 - a) * s, float(rate)

def render(x, za, zb, sr, s, PL, PR, plan):
    """Warp and handle the junction with the given s and periods. Returns (lo, y, info)."""
    WL, WR, DL, DR, rate = plan; P = 0.5 * (PL + PR)
    u0L, yL = _warp_side(x, za, WL, DL, True); bR, yR = _warp_side(x, zb, WR, DR, False)
    eL = u0L + len(yL) - 1; uendR = bR + len(yR) - 1
    Wx0 = int(np.clip(XF_FRAC * P, XF_MIN, XF_MAX)); m = XF_MARGIN
    hole = bR - (eL + 1)                      # >0 small hole; <0 real data of both sides overlap
    if hole >= 0: Wx = hole + 2 * m; g0 = eL + 1 - m; g1 = bR + m; st = 0
    elif -hole >= Wx0: Wx = Wx0; g0 = bR; g1 = eL + 1; st = None       # find the best position within the overlap
    else: Wx = max(-hole + 2 * m, XF_MIN); ex = (Wx + hole) // 2; g0 = bR - ex; g1 = g0 + Wx; st = 0
    n = g1 - g0; u = np.arange(g0, g1)
    A = yL[u - PL * np.ceil(np.maximum(u - eL, 0) / PL).astype(int) - u0L]
    B = yR[u + PR * np.ceil(np.maximum(bR - u, 0) / PR).astype(int) - bR]
    # continuity correction at the start of the periodic extension (value + slope), so the jump between "previous period
    # vs this period" does not become a step
    fade = lambda d: np.where(d < EXT_FADE, 0.5 + 0.5 * np.cos(np.pi * np.clip(d, 0, EXT_FADE) / EXT_FADE), 0.0)
    kA = u > eL
    if kA.any():
        e = yL[-8:] - yL[-8 - PL:len(yL) - PL]; sl = np.polyfit(np.arange(-7, 1), e, 1)
        d = (u[kA] - eL).astype(float); A[kA] += (sl[1] + np.clip(sl[0], -abs(sl[1]) / 8, abs(sl[1]) / 8) * d) * fade(d)
    kB = u < bR
    if kB.any():
        e = yR[:8] - yR[PR:PR + 8]; sl = np.polyfit(np.arange(0, 8), e, 1)
        d = (bR - u[kB]).astype(float); B[kB] += (sl[1] - np.clip(sl[0], -abs(sl[1]) / 8, abs(sl[1]) / 8) * d) * fade(d)
    wr = _raised(Wx)
    if st is None:
        ww = wr * (1 - wr); cost = np.convolve((A - B) ** 2, ww[::-1], 'valid')
        mid = (n - Wx) / 2; cost = cost * (1 + 0.5 * (np.abs(np.arange(len(cost)) - mid) / max(P, 1)) ** 2)
        st = int(np.argmin(cost))
    w = np.zeros(n); w[st:st + Wx] = wr; w[st + Wx:] = 1.0
    lo = u0L; hi = uendR + 1; y = np.empty(hi - lo)
    y[:g0 - lo] = yL[:g0 - u0L]; y[g0 - lo:g1 - lo] = (1 - w) * A + w * B; y[g1 - lo:] = yR[g1 - bR:]
    info = dict(s=float(s), P=float(P), PL=int(PL), PR=int(PR), WL=WL, WR=WR, rate=rate, eL=int(eL), bR=int(bR), hole=int(hole),
                lo=int(lo), hi=int(hi), junction="xfade", jlo=int(g0 + st), jhi=int(g0 + st + Wx))
    if hole >= 0:
        from .v3methods import ensemble_select
        ha, hb = eL + 1, bR
        if hb - ha < MIN_HOLE: ex = MIN_HOLE - (hb - ha); ha -= ex // 2; hb += ex - ex // 2
        z = x[lo - CT:hi + CT].copy()
        z[CT:CT + (ha - lo)] = yL[:ha - u0L]; z[CT + (hb - lo):CT + len(y)] = yR[hb - bR:]; z[CT + (ha - lo):CT + (hb - lo)] = np.nan
        xf = y[ha - lo:hb - lo].copy(); base = list(ensemble_select.CANDS)
        try:
            ensemble_select.CANDS = base + [("warp_xfade", lambda ctx, a, b, sr_: xf)]
            fillv = ensemble_select.fill(z, CT + ha - lo, CT + hb - lo, sr)
        finally:
            ensemble_select.CANDS = base
        y[:ha - lo] = yL[:ha - u0L]; y[hb - lo:] = yR[hb - bR:]; y[ha - lo:hb - lo] = fillv
        info.update(junction=str(ensemble_select.LAST.get("chosen")), jlo=int(ha), jhi=int(hb))
    return lo, y, info

def _guard_ok(x, lo, y, info):
    """The second difference near the junction must not clearly exceed the natural level within the same warp zone
    (farther from the junction)."""
    d2 = np.abs(y[2:] - 2 * y[1:-1] + y[:-2]); sc = max(np.median(d2), 1e-12)
    j0 = max(info["jlo"] - lo - 12, 1); j1 = min(info["jhi"] - lo + 12, len(d2) - 1)
    jun = d2[j0:j1].max() / sc
    far = np.concatenate([d2[:max(j0 - 200, 1)], d2[j1 + 200:]]); ref = np.percentile(far, 99.9) / sc if len(far) > 500 else 8.0
    info.update(guard_j=float(jun), guard_ref=float(ref))
    return jun <= max(GUARD_D2_ABS, GUARD_D2_REL * ref)

def fallback_fill(x, a, b, sr):
    from .v3methods import ensemble_select
    o = max(a - 16384, 0); ctx = x[o:b + 16384].copy(); ctx[a - o:b - o] = np.nan
    y = ensemble_select.fill(ctx, a - o, b - o, sr)
    return a, np.asarray(y, dtype=np.float64), dict(mode="fallback", junction=str(ensemble_select.LAST.get("chosen")), lo=int(a), hi=int(b))

def repair_joint(X, za, zb, subs, sr, WL_av=W_MAX, WR_av=W_MAX, roomL=10 ** 9, roomR=10 ** 9, force_fallback=False):
    """X: signal of each track (local or full), in the same index coordinates. Returns one (lo, y, info) per track.
    subs=[(a,b),...] are the intervals for the fallback (per-hole interpolation) (several when clusters are merged,
    filled in order); [za,zb) is the discard zone (for a single seam za==zb==cut point)."""
    T = len(X); out = [None] * T
    A = [analyse_track(x, za, zb, sr) for x in X] if not force_fallback else [dict(conf=False, level=-99.0) for _ in range(T)]
    conf = [t for t in range(T) if A[t]["conf"]]
    dom = max(conf, key=lambda t: A[t]["level"]) if conf else None
    for t in range(T):
        x = X[t]; an = A[t]; use = None
        if dom is not None:
            ad = A[dom]; Pd = 0.5 * (ad["PL"] + ad["PR"])
            if an["conf"]:
                ds = abs(an["s"] - ad["s"])
                use = (ad["s"], an["PL"], an["PR"], "follow-same-s") if (t != dom and ds <= FOLLOW_DS) else (an["s"], an["PL"], an["PR"], "own")
            elif an["level"] <= ad["level"] - FOLLOW_LEVEL_DB and min(an["rL"], an["rR"]) < VOICED_THR:
                # this track is non-voiced here (mostly noise floor + crosstalk) → follow the dominant track's time map
                use = (ad["s"], ad["PL"], ad["PR"], "follow-dominant")
        res = None
        if use is not None:
            plan = plan_warp(use[0], WL_av, WR_av, use[1], use[2], roomL, roomR)
            if plan is not None:
                lo, y, info = render(x, za, zb, sr, use[0], use[1], use[2], plan)
                if np.all(np.isfinite(y)) and _guard_ok(x, lo, y, info):
                    # unwarped side: report only the range actually changed
                    if plan[0] == 0: c0 = max(info["jlo"] - 16 - lo, 0); y = y[c0:]; lo += c0; info["lo"] = int(lo)
                    if plan[1] == 0: c1 = max(lo + len(y) - (info["jhi"] + 16), 0); y = y[:len(y) - c1]; info["hi"] = int(lo + len(y))
                    info.update(mode="warp", role=use[3], rho=an.get("rho"), rL=an.get("rL"), rR=an.get("rR"), level=an["level"]); res = (lo, y, info)
                else: an["rejected"] = "guard"
            else: an["rejected"] = "no-room"
        if res is None:
            lo0 = subs[0][0]; hi0 = subs[-1][1]; o = max(lo0 - 20000, 0); xx = x[o:hi0 + 20000].copy(); js = []
            for (a, b) in subs:
                _, yy, inf = fallback_fill(xx, a - o, b - o, sr); xx[a - o:b - o] = yy; js.append(inf["junction"])
            info = dict(mode="fallback", junction="+".join(js), lo=int(lo0), hi=int(hi0), level=an.get("level"), rejected=an.get("rejected"), rL=an.get("rL"), rR=an.get("rR"), rho=an.get("rho"))
            res = (lo0, xx[lo0 - o:hi0 - o].copy(), info)
        out[t] = res
    return out

def repair(x, za, zb, sr):
    """Single-track convenience interface (for the benchmark): returns (lo, y, info) on a successful warp, otherwise
    None."""
    an = analyse_track(x, za, zb, sr)
    if not an["conf"]: return None
    plan = plan_warp(an["s"], W_MAX, W_MAX, an["PL"], an["PR"])
    if plan is None: return None
    lo, y, info = render(x, za, zb, sr, an["s"], an["PL"], an["PR"], plan)
    if not _guard_ok(x, lo, y, info): return None
    info.update(mode="warp", rho=an["rho"], rL=an["rL"], rR=an["rR"]); return lo, y, info
