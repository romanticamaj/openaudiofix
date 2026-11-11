"""Phase-jump detection for dropped samples in a single uploaded file.

Adapted from an earlier pipeline: the same second-difference local-scale detection, cross-channel simultaneous (±TOL)
verification, and automatic buffer-grid inference. The difference is that "track" becomes "channel" here: a mono
upload has only 1 track, so the cross-track rule degrades to relying on the grid prior alone (more conservative, see the
note on `infer_grid`); a stereo upload has 2 tracks and can use the same "seen on >=2 tracks at once" rule as a
multi-track recording, which is much more accurate.

Looks for this one defect only: dropped samples caused by a driver/ASIO buffer underrun, where the signal jumps to
another phase within one sample and normal audio follows on both sides - not clipping, not an arbitrary click, not zero
insertion.
"""
import numpy as np
from scipy.ndimage import maximum_filter1d

BLOCK = 2048        # block length for the local scale (median)
THRESH = 12.0        # d2 / local median above this value makes a candidate
MERGE = 64           # candidates within <= MERGE samples count as the same event; keep the strongest
TOL = 4              # tolerance (samples) for being "simultaneous" across channels
GAP0 = 96            # default gap (samples)
GAP_BIG = 160        # gap when the phase step is large
BIG_STEP = 40.0      # |x[p]-x[p-1]| / local median of the first difference above this counts as a "large phase step"
# a candidate seen on only one track/channel must exceed this strength and lie on the grid to be accepted
SINGLE_MIN_STRENGTH = 40.0
GRID_CANDIDATES = (32, 64, 128, 256, 512)
# infer_grid concentration below this ratio -> distrust the grid, use only the cross-channel rule
GRID_CONFIDENCE_MIN = 4.0
# for mono grid inference, filter out weak candidates first (see the note in detect_seams below)
GRID_STRENGTH_MIN = 24.0


def detect(x):
    """Returns (array of event positions, strength of each event). Strength = second difference / local median."""
    d2 = np.abs(x[2:] - 2 * x[1:-1] + x[:-2])  # d2[i] corresponds to sample i+1
    n = len(d2) // BLOCK * BLOCK
    if n == 0:
        return np.array([], int), np.array([])
    med = np.median(d2[:n].reshape(-1, BLOCK), axis=1)
    med = maximum_filter1d(np.maximum(med, 1e-9), 3)
    sc = np.concatenate([np.repeat(med, BLOCK), np.full(len(d2) - n, med[-1] if len(med) else 1e-9)])
    r = d2 / sc
    cand = np.where(r > THRESH)[0] + 1
    if not len(cand):
        return np.array([], int), np.array([])
    ev, st = [], []
    peak, pv, last = cand[0], r[cand[0] - 1], cand[0]
    for c in cand[1:]:
        if c - last <= MERGE:
            if r[c - 1] > pv:
                peak, pv = c, r[c - 1]
        else:
            ev.append(peak); st.append(pv); peak, pv = c, r[c - 1]
        last = c
    ev.append(peak); st.append(pv)
    return np.array(ev), np.array(st)


def nearest_dist(a, b):
    if not len(b):
        return np.full(len(a), np.inf)
    j = np.searchsorted(b, a)
    j0 = np.clip(j - 1, 0, len(b) - 1)
    j1 = np.clip(j, 0, len(b) - 1)
    return np.minimum(np.abs(b[j0] - a), np.abs(b[j1] - a))


def coincident_all(evs, tol=TOL):
    """Events that appear within ±tol on all channels (positions taken from the first channel). Returned unchanged for
    mono."""
    if len(evs) <= 1:
        return evs[0]
    a = evs[0]
    m = np.ones(len(a), bool)
    for b in evs[1:]:
        m &= nearest_dist(a, b) <= tol
    return a[m]


def infer_grid(pos, cands=GRID_CANDIDATES):
    """Infer the buffer length: for each candidate L, compute the share of "the two adjacent bins where the residues
    are most concentrated". Returns (L, residue set, share, ratio relative to a uniform distribution)."""
    if len(pos) == 0:
        return (cands[0], set(), 0.0, 0.0)
    stats = []
    for L in cands:
        h = np.bincount(pos % L, minlength=L)
        top = np.argsort(h)[::-1][:2]
        stats.append((L, set(int(t) for t in top), h[top].sum() / max(len(pos), 1)))
    fmax = max(f for _, _, f in stats)
    L, res, f = max((s for s in stats if s[2] >= 0.8 * fmax), key=lambda s: s[0])
    return (L, res, f, f / (2 / L))


def build_seam_list(evs, sts, grid, tol=TOL):
    """Cluster each channel's candidate events into a seam list: seen on >=2 tracks across channels at once, or on one
    track, on the grid and strong enough. Returns [(position, n_tracks_matched, on_grid, max_strength), ...]."""
    allp = np.sort(np.concatenate(evs)) if any(len(e) for e in evs) else np.array([], int)
    if not len(allp):
        return []
    L, residues, _, _ = grid
    clusters, cur = [], [allp[0]]
    for p in allp[1:]:
        if p - cur[-1] <= tol:
            cur.append(p)
        else:
            clusters.append(cur); cur = [p]
    clusters.append(cur)
    rows = []
    for c in clusters:
        p = int(min(c)); ntr = 0; smax = 0.0
        for e, s in zip(evs, sts):
            if not len(e):
                continue
            d = np.abs(e - p); j = int(np.argmin(d))
            if d[j] <= tol:
                ntr += 1; smax = max(smax, float(s[j]))
        ongrid = (p % L) in residues if residues else False
        if ntr >= 2 or (ongrid and smax > SINGLE_MIN_STRENGTH):
            rows.append((p, ntr, ongrid, smax))
    return rows


def step_ratio(x, p):
    lo, hi = max(0, p - 1024), min(len(x), p + 1024)
    d1 = np.abs(np.diff(x[lo:hi]))
    return float(abs(x[p] - x[p - 1]) / (np.median(d1) + 1e-12)) if 0 < p < len(x) else 0.0


def cut_of(p, grid):
    """Convert a detected candidate position into the exact cut point "first real sample on the right" (accurate only
    when the grid is trusted; otherwise returns p itself, and the repair side later adds a safety margin, treating it
    as a tiny gap rather than a single-point splice)."""
    L, residues, _, conf = grid
    if conf >= GRID_CONFIDENCE_MIN and len(residues) >= 2:
        lo = min(residues)
        if p % L == lo:
            return p + 1
    return p


def detect_seams(channels, sr):
    """channels: list of 1D float64 array (one per channel, equal length).
    Returns dict: {position: {"gap": int, "za": int, "zb": int, "ntracks": int, "ongrid": bool,
                            "strength": float, "grid_confident": bool}}, plus the grid info (for the front end to
                            display)."""
    evs, sts = [], []
    for x in channels:
        e, s = detect(x)
        evs.append(e); sts.append(s)
    tri = coincident_all(evs)
    if len(channels) > 1:
        # simultaneous appearance across channels is strong evidence in itself, use it directly (real candidates are
        # generally far more numerous than in the mono case, so the SNR is high enough)
        grid = infer_grid(tri)
    else:
        # Mono: most of all candidates are ordinary transients (THRESH=12 is very loose), which dilute the residue
        # distribution
        # of the grid until it cannot be inferred. Grid inference uses only strong candidates (less noise), but once the
        # grid is
        # trustworthy, a weak candidate that lies on the grid can still enter build_seam_list (see the single-track
        # branch
        # below), so it is not blocked by this filter.
        grid = infer_grid(evs[0][sts[0] >= GRID_STRENGTH_MIN])
    if grid[3] < GRID_CONFIDENCE_MIN and len(channels) == 1:
        # grid concentration still low: do not guess hard, to avoid taking ordinary transients for seams
        grid = (grid[0], set(), 0.0, 0.0)
    rows = build_seam_list(evs, sts, grid)
    grid_confident = grid[3] >= GRID_CONFIDENCE_MIN
    seams = {}
    for p, ntr, ongrid, strength in rows:
        gap = GAP_BIG if any(step_ratio(x, p) > BIG_STEP for x in channels) else GAP0
        cp = cut_of(p, grid) if grid_confident else p
        # when the grid is not trusted, leave a small gap instead of assuming a single-point splice
        margin = 0 if grid_confident else 2
        seams[int(p)] = dict(gap=gap, za=int(cp - margin), zb=int(cp + margin), ntracks=ntr,
                              ongrid=bool(ongrid), strength=round(float(strength), 1), force=False)
    return seams, dict(L=grid[0], residues=sorted(grid[1]), coverage=round(grid[2], 4),
                        confidence=round(grid[3], 2), confident=grid_confident,
                        n_candidates=[int(len(e)) for e in evs], n_coincident=int(len(tri)))
