"""pitch_wsola: pitch-synchronous two-sided waveform substitution (speech PLC / WSOLA style).

Per side (left context ending at a, right context starting at b, analysed mirrored):
  * normalised autocorrelation over lags 2.5-12.5 ms with a 1024-sample template gives the
    voicing correlation rmax and a coarse period (shortest local maximum within 90 % of the
    peak = octave guard); a short template (1.5 periods) refines the copy lag.
  * kind 'V' (rmax >= 0.60): pitch-synchronous copy with the refined period.
    kind 'W' (rmax >= 0.35 at the other side's period while that side has rmax >= 0.80):
    copy with the strong side's period.  kind 'U' (unvoiced): WSOLA-style copy with the
    best-correlated lag in 5-12.5 ms (so noise is not repeated at a short period).
  * both sides pitch-derived: cross-side octave consistency check (halve/double the side whose
    alternative correlates best).
Fill:
  * forward extension = repeat the last cycle from the left, backward extension = repeat the
    first cycle from the right; the tail of each copy is overlap-added (raised cosine, 1/4
    period) with the tail of the previous cycle, so the extension has no seams of its own.
  * phase alignment: cross-correlate the two extensions around the gap centre over +-P/2;
    the splice (crossfade centre) is then searched where the two extensions differ least
    (weighted by the crossfade), i.e. usually the quiet part of the cycle.  A limited linear
    time-warp (WARP_RATE_MAX) and a linear period glide (PITCH_TRACK) are implemented but off:
    on the benchmark they hurt/did not help spectral continuity.
  * log-linear envelope ramp from the left source-cycle RMS to the right source-cycle RMS on
    both extensions (attenuation <= 18 dB, amplification <= 6 dB, none for 'U' sides).
  * raised-cosine crossfade left->right (width 0.5 period, >= 32 samples) at the splice with a
    correlation-aware gain so the crossfade stays energy-neutral when the two signals are not
    perfectly coherent.
  * the first/last 24 samples are blended into the two-sided LS-AR(48) fill (the baseline), so
    the joins at a and b are exactly as click-free as the baseline.
Fallback (context too short, or UNVOICED_MODE='sample'): LS-AR(48) conditional mean plus a
deterministic posterior sample (Cholesky of the normal matrix, innovation variance from the
context, edge-tapered).  Deterministic; seed derived from (a, b, context RMS).
"""
import numpy as np
from scipy.linalg import solve_toeplitz, solve, cholesky, solve_triangular
from scipy.signal import lfilter

# ------------------------------------------------------------------ parameters
ORDER = 48            # AR order (baseline)
CTX = 1536            # AR context each side (baseline)
LAG_MIN_MS = 2.5
LAG_MAX_MS = 12.5
TPL_LONG = 1024       # template length for the voiced decision / coarse period
TPL_SHORT_PERIODS = 1.5  # short template = this many coarse periods (copy-lag refinement)
VOICED_THR = 0.60     # normalised correlation threshold for "voiced"
VOICED_THR_WEAK = 0.35  # a side with r >= this counts as voiced if the other side is strongly
VOICED_STRONG = 0.80    # voiced (r >= VOICED_STRONG) and the periods agree
SUB_FRAC = 0.90       # shortest local max with r >= SUB_FRAC*rmax wins (octave guard)
CYCLE_OV_FRAC = 0.25  # OLA length at cycle joins as fraction of the period
EDGE_OV = 24          # samples blended into the LS-AR fill at each join
XFADE_FRAC = 0.5      # crossfade width as fraction of the period
XFADE_MIN = 32
WARP_RATE_MAX = 0.0   # max |rate-1| of the linear time warp (0 = off)
PITCH_TRACK = False   # glide the extension period from PL to PR
PITCH_TRACK_MAX = 0.15  # only when |PR-PL|/P <= this
ENV_ATT_MAX_DB = 18.0 # envelope ramp: max attenuation of a source cycle
ENV_AMP_MAX_DB = 6.0  # envelope ramp: max amplification of a source cycle (two-sided)
ONE_SIDED_AMP_DB = 0.0  # max amplification of the voiced extension in one-sided cases
COH_GAIN = True       # correlation-aware crossfade gain
COH_GAIN_MAX_DB = 4.0
NOISE_GAIN = 1.0      # scale of the posterior sample in the unvoiced fill (0 = plain LS)
SAMPLE_EDGE_TAPER = True  # taper the posterior sample to 0 at the joins over EDGE_OV samples
ONE_SIDED_XFADE_FRAC = 0.5  # crossfade width (fraction of gap) for one-sided voiced
UNVOICED_MODE = 'copy'    # 'sample': LS+posterior sample for unvoiced sides
                          # 'copy1': one-sided cases copy the unvoiced side too (WSOLA style)
                          # 'copy': always copy both sides (never use the sample)
UNV_LAG_MIN_MS = 5.0      # unvoiced sides copy with the best-correlated lag in [this, LAG_MAX]
UNV_XFADE_FULL = False    # both sides unvoiced: crossfade over the whole gap
FORCE_UNVOICED = False    # debug: always use the LS+sample branch

LAST_INFO = {}


# ------------------------------------------------------------------ LS-AR(48) baseline + posterior sample
def _ar_coefs(seg, order=ORDER):
    seg = seg - seg.mean()
    r = np.correlate(seg, seg, 'full')[len(seg) - 1:len(seg) + order]
    if r[0] <= 0:
        return None
    r = r.copy(); r[0] *= 1.0001
    return solve_toeplitz(r[:order], r[1:order + 1])


def _ls_ar(x, a, b, rng, noise_gain):
    """Returns (ls_mean, ls_plus_sample). x has zeros in [a,b)."""
    gap = b - a
    left = x[a - CTX:a]; right = x[b:b + CTX]
    coef = _ar_coefs(np.concatenate([left, right]))
    if coef is None:
        lin = np.linspace(x[a - 1], x[b], gap + 2)[1:-1]
        return lin, lin
    A = np.concatenate([[1.0], -coef]); P = ORDER
    seg = x[a - P:b + P].copy(); n = len(seg); unk = np.arange(P, P + gap); Mrows = n - P
    B = np.zeros((Mrows, n))
    for k in range(P + 1):
        B[np.arange(Mrows), np.arange(Mrows) + P - k] = A[k]
    known = np.ones(n, bool); known[unk] = False
    Bu = B[:, unk]; rhs = -B[:, known] @ seg[known]
    M = Bu.T @ Bu + 1e-9 * np.eye(gap)
    try:
        L = cholesky(M, lower=True)
    except Exception:
        mean = solve(M, Bu.T @ rhs, assume_a='pos')
        return mean, mean
    tmp = solve_triangular(L, Bu.T @ rhs, lower=True)
    mean = solve_triangular(L, tmp, lower=True, trans='T')
    if noise_gain <= 0:
        return mean, mean
    # innovation variance from the context residual
    eL = lfilter(A, [1.0], left - left.mean())[P:]
    eR = lfilter(A, [1.0], right - right.mean())[P:]
    sig2 = 0.5 * (np.mean(eL ** 2) + np.mean(eR ** 2))
    z = rng.standard_normal(gap)
    samp = solve_triangular(L, z, lower=True, trans='T') * np.sqrt(sig2) * noise_gain
    if SAMPLE_EDGE_TAPER:
        e = max(1, min(EDGE_OV, gap // 4))
        tap = np.ones(gap); r_ = _raised(e); tap[:e] = r_; tap[gap - e:] = np.minimum(tap[gap - e:], r_[::-1])
        samp = samp * tap
    return mean, mean + samp


# ------------------------------------------------------------------ pitch analysis
def _lag_corr(seg, tpl, lmin, lmax):
    """seg ends at the edge. r[i] = normalised correlation between the last tpl samples and the
    same-length window lag (lmin+i) earlier."""
    n = len(seg); t = seg[n - tpl:]; et = np.dot(t, t)
    s = seg[n - tpl - lmax:]
    c = np.correlate(s, t, 'valid')                      # index j <-> lag lmax-j
    cs = np.concatenate([[0.0], np.cumsum(s * s)]); e = cs[tpl:] - cs[:-tpl]
    r = c / np.sqrt(et * e + 1e-30)
    r = r[::-1]                                          # index = lag
    return r[lmin:lmax + 1]


def _pick_lag(r, lmin, sub_frac):
    imax = int(np.argmax(r)); rmax = float(r[imax])
    if len(r) < 3:
        return lmin + imax, rmax
    lm = np.flatnonzero((r[1:-1] >= r[:-2]) & (r[1:-1] >= r[2:])) + 1
    if len(lm) == 0:
        return lmin + imax, rmax
    cand = lm[r[lm] >= sub_frac * rmax]
    i0 = int(cand.min()) if len(cand) else imax
    return lmin + i0, rmax


class _Side:
    __slots__ = ("seg", "r", "rmax", "P0", "P", "lmin", "lmax")


def _analyse(seg, lmin, lmax):
    """seg: context ending at the edge (oriented so the edge is at the end)."""
    S = _Side()
    seg = seg - seg.mean()
    S.seg = seg; S.lmin = lmin; S.lmax = lmax
    S.r = _lag_corr(seg, TPL_LONG, lmin, lmax)
    S.P0, S.rmax = _pick_lag(S.r, lmin, SUB_FRAC)
    S.P = _refine(S, S.P0)
    return S


def _refine(S, P0):
    """copy lag: short-template correlation peak within +-15% of P0."""
    tpl_s = int(np.clip(TPL_SHORT_PERIODS * P0, 160, TPL_LONG))
    lo = max(S.lmin, int(P0 * 0.85)); hi = min(S.lmax, int(P0 * 1.15) + 1)
    if hi - lo < 2:
        return int(np.clip(P0, S.lmin, S.lmax))
    rs = _lag_corr(S.seg, tpl_s, lo, hi - 1)
    return lo + int(np.argmax(rs))


def _r_at(S, P):
    """long-template correlation near lag P (max within +-6%)."""
    lo = max(S.lmin, int(P * 0.94)); hi = min(S.lmax, int(P * 1.06) + 1)
    if hi <= lo:
        return -1.0
    return float(np.max(S.r[lo - S.lmin:hi - S.lmin]))


# ------------------------------------------------------------------ periodic extension with cycle OLA
def _periodic_extend(ctxs, L, n_out, ov):
    """ctxs: samples before the edge. Returns n_out samples continuing after the edge by repeating
    the last L samples; the tail of each copy is overlap-added with the tail of the previous
    cycle so the join into the next copy is seamless."""
    m = len(ctxs)
    i = np.arange(n_out); j = i % L
    y = ctxs[m - L + j].copy()
    ov = int(min(ov, L // 2))
    if ov > 1 and m >= 2 * L:
        mask = j >= L - ov
        jj = j[mask]
        w = 0.5 - 0.5 * np.cos(np.pi * (jj - (L - ov) + 0.5) / ov)
        prev = ctxs[m - 2 * L + jj]
        y[mask] = (1.0 - w) * y[mask] + w * prev
    return y


def _interp_cubic(y, t):
    i = np.floor(t).astype(np.int64)
    i = np.clip(i, 1, len(y) - 3)
    f = t - i
    y0 = y[i - 1]; y1 = y[i]; y2 = y[i + 1]; y3 = y[i + 2]
    a0 = -0.5 * y0 + 1.5 * y1 - 1.5 * y2 + 0.5 * y3
    a1 = y0 - 2.5 * y1 + 2.0 * y2 - 0.5 * y3
    a2 = -0.5 * y0 + 0.5 * y2
    return ((a0 * f + a1) * f + a2) * f + y1


def _rms(v):
    return float(np.sqrt(np.mean(v * v)) + 1e-12)


def _raised(n):
    """0->1 raised cosine of n samples (sample centred)."""
    return 0.5 - 0.5 * np.cos(np.pi * (np.arange(n) + 0.5) / n)


def _track_period(ext, P_from, P_to, gap):
    """Re-time a constant-period extension (period P_from, starting at the edge) so that its local
    period glides linearly from P_from at the edge to P_to at the far side of the gap and stays
    P_to beyond.  ext[0] is the first sample after the edge."""
    n = len(ext)
    m = np.arange(n) + 0.5
    Pn = P_from + (P_to - P_from) * np.clip(m / gap, 0.0, 1.0)
    rate = P_from / Pn
    tau = np.concatenate([[0.0], np.cumsum(rate)[:-1]])
    tau = np.minimum(tau, n - 1.001)
    return _interp_cubic(ext, tau)


# ------------------------------------------------------------------ main
def fill(ctx, a, b, sr):
    x = ctx.copy(); gap = b - a; x[a:b] = 0.0
    lmin = int(round(LAG_MIN_MS * 1e-3 * sr)); lmax = int(round(LAG_MAX_MS * 1e-3 * sr))
    info = LAST_INFO; info.clear()

    rmsL = _rms(x[a - 1024:a]); rmsR = _rms(x[b:b + 1024])
    seed = (int(a) * 1000003 + int(b) * 7919 + int(min(rmsL, 1.0) * 1e7) + int(min(rmsR, 1.0) * 1e7) * 31) % (2 ** 31)
    rng = np.random.default_rng(seed)

    ls_mean, ls_samp = _ls_ar(x, a, b, rng, NOISE_GAIN)
    ls_samp = np.where(np.isfinite(ls_samp), ls_samp, 0.0)
    ls_mean = np.where(np.isfinite(ls_mean), ls_mean, 0.0)

    need = TPL_LONG + lmax
    if FORCE_UNVOICED or a - need - 2 * lmax < 0 or b + need + 2 * lmax > len(x):
        info['branch'] = 'unvoiced'
        return ls_samp

    SL = _analyse(x[a - need:a], lmin, lmax)
    SR = _analyse(x[b:b + need][::-1], lmin, lmax)
    rL, rR = SL.rmax, SR.rmax
    PL, PR = SL.P, SR.P
    vL = rL >= VOICED_THR; vR = rR >= VOICED_THR
    info.update(rL=rL, rR=rR, PL=PL, PR=PR)

    kindL = 'V' if vL else 'U'; kindR = 'V' if vR else 'U'
    # --- weak-side hysteresis: a marginal side counts as voiced when the other side is strongly
    #     voiced and this side correlates reasonably at the strong side's period
    if vL != vR:
        strong, weak = (SL, SR) if vL else (SR, SL)
        if strong.rmax >= VOICED_STRONG:
            rw = _r_at(weak, strong.P)
            if rw >= VOICED_THR_WEAK:
                weak.P = _refine(weak, strong.P)
                if vL: vR = True; PR = weak.P; kindR = 'W'
                else: vL = True; PL = weak.P; kindL = 'W'
                info['weak'] = True

    copy_unv = UNVOICED_MODE == 'copy' or (UNVOICED_MODE == 'copy1' and (vL or vR))
    if copy_unv:
        ulo = int(round(UNV_LAG_MIN_MS * 1e-3 * sr))
        if not vL:
            PL = ulo + int(np.argmax(SL.r[ulo - lmin:])); vL = True
        if not vR:
            PR = ulo + int(np.argmax(SR.r[ulo - lmin:])); vR = True
    info.update(kind=kindL + kindR)

    if not vL and not vR:
        info['branch'] = 'unvoiced'
        return ls_samp

    # --- cross-side consistency of the periods (octave errors)
    if vL and vR and kindL != 'U' and kindR != 'U':
        ratio = PL / PR
        if not (0.8 < ratio < 1.25):
            # candidates: halve/double the side whose alternative correlates well
            best = None
            for cL, cR in ((PL // 2, PR), (PL, PR * 2), (PL * 2, PR), (PL, PR // 2), (PL, PR)):
                if not (lmin <= cL <= lmax and lmin <= cR <= lmax):
                    continue
                if not (0.8 < cL / cR < 1.25):
                    continue
                score = min(_r_at(SL, cL), _r_at(SR, cR))
                if best is None or score > best[0]:
                    best = (score, cL, cR)
            if best is not None and best[0] >= VOICED_THR_WEAK:
                PL = _refine(SL, best[1]); PR = _refine(SR, best[2])
                info['octfix'] = (best[1], best[2])
    if vL and vR:
        PL = int(np.clip(PL, lmin, lmax)); PR = int(np.clip(PR, lmin, lmax))
        Ps = min(PL, PR)
    else:
        Ps = PL if vL else PR

    u = (np.arange(gap) + 0.5) / gap
    edge = max(1, min(EDGE_OV, gap // 4))

    # --- envelope targets (RMS of the source cycles)
    RL = _rms(x[a - (PL if vL else Ps):a]); RR = _rms(x[b:b + (PR if vR else Ps)])
    att = 10 ** (ENV_ATT_MAX_DB / 20)
    amp = 10 ** (ENV_AMP_MAX_DB / 20); amp1 = 10 ** (ONE_SIDED_AMP_DB / 20)
    # A goes RL -> RR (gain gA), B goes RR -> RL (gain gB); a copied noise segment (kind 'U') or the
    # voiced extension of a one-sided case is never amplified above its own level
    ampA = amp if (vL and vR and kindL != 'U') else amp1
    ampB = amp if (vL and vR and kindR != 'U') else amp1
    gA = float(np.clip(RR / RL, 1 / att, ampA))
    gB = float(np.clip(RL / RR, 1 / att, ampB))

    margin = lmax + 8
    ext_n = gap + margin
    if vL:
        extL = _periodic_extend(x[:a], PL, ext_n, int(round(CYCLE_OV_FRAC * PL)))
    if vR:
        extR = _periodic_extend(x[b:][::-1], PR, ext_n, int(round(CYCLE_OV_FRAC * PR)))
    if vL and vR and PITCH_TRACK and kindL != 'U' and kindR != 'U' and abs(PR - PL) / Ps <= PITCH_TRACK_MAX and PR != PL:
        extL = _track_period(extL, PL, PR, gap)
        extR = _track_period(extR, PR, PL, gap)
        info['tracked'] = True
    if vR:
        extR = extR[::-1]

    base = a - margin
    if vL:
        YL = np.concatenate([x[a - margin:a], extL])           # index range [a - margin, b + margin)
    if vR:
        YR = np.concatenate([extR, x[b:b + margin]])

    if vL and vR:
        # ---------------- phase alignment around the gap centre
        c = a + gap // 2
        h = Ps // 2
        smax = Ps // 2
        seg_l = YL[c - h - base:c + h - base]
        el = np.dot(seg_l, seg_l)
        lo = c - h - smax - base; hi = c + h + smax - base
        win = YR[lo:hi]
        cc = np.correlate(win, seg_l, 'valid')                   # index k <-> s = k - smax
        cs = np.concatenate([[0.0], np.cumsum(win * win)]); er = cs[2 * h:] - cs[:-2 * h]
        rho = cc / np.sqrt(el * er + 1e-30)
        kbest = int(np.argmax(rho))
        good = np.flatnonzero(rho >= rho[kbest] - 0.02)
        kbest = int(good[np.argmin(np.abs(good - smax))])
        s_star = kbest - smax; rho_star = float(rho[kbest])
        info.update(s_star=s_star, rho_star=rho_star)

        # ---------------- optional limited linear warp, split evenly
        s_w = float(np.clip(-s_star, -WARP_RATE_MAX * gap, WARP_RATE_MAX * gap))
        if s_w != 0.0:
            tauL = (np.arange(gap) + a) + s_w * u
            tauR = (np.arange(gap) + a) - s_w * (1.0 - u)
            A = _interp_cubic(YL, tauL - base) * gA ** u
            Bv = _interp_cubic(YR, tauR - base) * gB ** (1.0 - u)
        else:
            A = YL[margin:margin + gap] * gA ** u
            Bv = YR[margin:margin + gap] * gB ** (1.0 - u)

        # ---------------- splice search
        Wx = int(np.clip(XFADE_FRAC * Ps, XFADE_MIN, max(2, gap - 2 * edge)))
        if UNV_XFADE_FULL and kindL == 'U' and kindR == 'U':
            Wx = max(2, gap - 2 * edge)
        Wx = min(Wx, gap)
        wr = _raised(Wx)
        lo_c = edge + Wx // 2; hi_c = gap - edge - (Wx - Wx // 2)
        if hi_c < lo_c:
            lo_c = hi_c = gap // 2
        d2 = (A - Bv) ** 2
        ww = wr * (1.0 - wr)
        cands = np.arange(lo_c, hi_c + 1)
        if len(cands) > 1:
            conv = np.convolve(d2, ww[::-1], 'valid')            # conv[k] = sum_i d2[k+i] ww[i]
            costs = conv[cands - Wx // 2]
            csel = int(cands[np.argmin(costs)])
        else:
            csel = int(cands[0])
        start = csel - Wx // 2
        w = np.zeros(gap); w[start:start + Wx] = wr; w[start + Wx:] = 1.0
        y = (1.0 - w) * A + w * Bv
        if COH_GAIN and Wx > 4:
            za = A[start:start + Wx]; zb = Bv[start:start + Wx]
            ea = np.dot(za, za); eb = np.dot(zb, zb)
            r = float(np.dot(za, zb) / np.sqrt(ea * eb + 1e-30)) if ea > 0 and eb > 0 else 1.0
            r = max(r, -0.5)
            wl = 1.0 - wr
            gcoh = 1.0 / np.sqrt(wl ** 2 + wr ** 2 + 2.0 * r * wl * wr)
            gcoh = np.minimum(gcoh, 10 ** (COH_GAIN_MAX_DB / 20))
            y[start:start + Wx] *= gcoh
        info.update(branch='two' + kindL + kindR, Wx=Wx, csel=csel, s_w=s_w)
    else:
        # one-sided voiced: voiced extension crossfaded with the LS+sample fill
        Wx = int(np.clip(ONE_SIDED_XFADE_FRAC * gap, 8, gap - 2 * edge)) if gap > 2 * edge + 8 else gap
        wr = _raised(Wx)
        start = (gap - Wx) // 2
        w = np.zeros(gap); w[start:start + Wx] = wr; w[start + Wx:] = 1.0
        if vL:
            A = YL[margin:margin + gap] * gA ** u
            y = (1.0 - w) * A + w * ls_samp
            info['branch'] = 'voicedL'
        else:
            Bv = YR[margin:margin + gap] * gB ** (1.0 - u)
            y = (1.0 - w) * ls_samp + w * Bv
            info['branch'] = 'voicedR'

    # ---------------- blend joins into the LS-AR fill
    we = np.zeros(gap)
    ramp = _raised(edge)
    we[:edge] = 1.0 - ramp; we[gap - edge:] = np.maximum(we[gap - edge:], ramp)
    y = we * ls_mean + (1.0 - we) * y
    if not np.all(np.isfinite(y)):
        return ls_mean
    return y
