"""Repair of phase jumps from dropped samples (single file): detect, then phase-aligned micro-warp / per-hole
interpolation, then verify the residue.

"Track" here corresponds to "channel" (mono=1, stereo=2).
Uses the same logic as the whole-file pipeline for a long real recording: adjacent/overlapping
seams are first merged into one discard zone and solved at once; after repair the detector is rerun, residues first
fall back to the safe path and, if that still fails, are widened, and every round reapplies all patches
from the original file. The detector is the generic single-file version (`declick_detect.py`).
"""
import time
import numpy as np

from . import declick_detect as D, phase_warp

MAX_PASS = 4
CLUSTER_MARGIN = 330    # seams closer than this are merged into one discard zone and solved at once
RESID_NEAR = 200         # residual events within ± this range of an existing group count as the same spot
MAX_GAP = 960


def merge_groups(seams, margin=CLUSTER_MARGIN):
    """seams: {p: {gap, za, zb, ...}} → merge nearby / overlapping windows [za-gap/2, zb+gap/2) into groups.
    Returns a list of dict(za, zb, subs=[(a,b),...], positions=[p,...], force=False), sorted by position."""
    items = []
    for p, s in sorted(seams.items()):
        half = s["gap"] // 2
        a = min(s["za"] - half, s["za"] - 1)
        b = max(s["zb"] + half, s["zb"] + 1)
        if b - a < s["gap"]:
            b = a + s["gap"]
        items.append(dict(p=p, za=s["za"], zb=s["zb"], a=a, b=b, gap=s["gap"]))
    groups = []
    for it in items:
        if groups and it["a"] <= groups[-1]["zb"] + margin:
            g = groups[-1]
            g["zb"] = max(g["zb"], it["zb"]); g["b"] = max(g["b"], it["b"])
            g["subs"].append((it["a"], it["b"])); g["positions"].append(it["p"])
        else:
            groups.append(dict(za=it["za"], zb=it["zb"], a=it["a"], b=it["b"],
                                subs=[(it["a"], it["b"])], positions=[it["p"]]))
    for g in groups:
        g["force"] = any(seams[p].get("force", False) for p in g["positions"])
    return groups


def run_pass(X, groups, sr, on_group=None):
    """Call phase_warp.repair_joint on each group in turn; returns (new channel arrays, per-group repair records).
    on_group(done, total): optional; reports progress after each batch of groups is repaired (does not affect the
    result)."""
    Y = [x.copy() for x in X]
    records = []
    prev_end = -10 ** 9
    for gi, g in enumerate(groups):
        if on_group and gi % 25 == 0:
            on_group(gi, len(groups))
        roomL = g["za"] - max(prev_end, groups[gi - 1]["zb"] if gi else -10 ** 9)
        roomR = (groups[gi + 1]["za"] if gi + 1 < len(groups) else 10 ** 12) - g["zb"]
        WL_av = roomL - 32
        WR_av = roomR // 2 - 32
        # Zero everything when there is not enough room (rather than passing a small non-zero value): a warp window
        # shorter
        # than the pitch period makes phase_warp's internal "extension start continuity correction" read out of bounds.
        # The whole-file pipeline always had this safety margin (its `tight` check); the single-file pipeline initially
        # missed
        # this step, and an end-to-end test on a real clip with dense defects caught it (the stereo test clip triggered
        # a
        # broadcast error in render()), so it was added here too.
        tight = 2 * (phase_warp.W_AVAIL_MIN + 32) + 64
        if roomR < tight:
            WR_av = 0
        if roomL < tight:
            WL_av = 0
        if g["za"] - phase_warp.NEED - phase_warp.W_MAX < 0 or g["zb"] + phase_warp.NEED + phase_warp.W_MAX > len(Y[0]):
            res = None
        else:
            res = phase_warp.repair_joint(Y, g["za"], g["zb"], g["subs"], sr, WL_av=max(WL_av, 0),
                                           WR_av=max(WR_av, 0), roomL=max(roomL, 0), roomR=max(roomR, 0),
                                           force_fallback=g["force"])
        if res is None:
            lo0, hi0 = g["a"], g["b"]
            for t in range(len(Y)):
                o = max(lo0 - 16384, 0)
                ctx = Y[t][o:hi0 + 16384].copy(); ctx[lo0 - o:hi0 - o] = np.nan
                from .v3methods import ensemble_select
                yv = ensemble_select.fill(ctx, lo0 - o, hi0 - o, sr)
                Y[t][lo0:hi0] = yv
            info = dict(mode="fallback-edge", lo=lo0, hi=hi0)
            end = hi0
        else:
            end = g["b"]
            for t, (lo, y, info_t) in enumerate(res):
                Y[t][lo:lo + len(y)] = y
                end = max(end, lo + len(y))
            info = res[0][2]
        records.append(dict(group=g, info=info))
        prev_end = end
    return Y, records


def repair_file(channels, sr, job_dir, progress=None, detected=None):
    """channels: list of 1D float64 np.array (equal length). Returns (Y, seams_final, grid_info, history).
    detected: optional (seams, grid), e.g. the result of detecting on another signal (the original, before declip
    repair);
    if given, detection is not rerun on channels."""
    X0 = [x.copy() for x in channels]
    if detected is None:
        seams, grid = D.detect_seams(X0, sr)
    else:
        seams, grid = {p: dict(s) for p, s in detected[0].items()}, detected[1]
    if progress:
        progress(state="repairing", grid=grid, n_seams_initial=len(seams))
    history = []
    Y = X0
    for it in range(1, MAX_PASS + 1):
        groups = merge_groups(seams)
        t0 = time.time()
        Y, records = run_pass(X0, groups, sr, on_group=(lambda i, n, _it=it: progress(
            state="repairing", pass_=_it, done=i, total=n)) if progress else None)
        if progress:
            progress(state="repairing", pass_=it, n_groups=len(groups),
                     n_seams=len(seams), pass_seconds=round(time.time() - t0, 1))
        evs, sts = [], []
        for x in Y:
            e, s = D.detect(x)
            evs.append(e); sts.append(s)
        tri = D.coincident_all(evs) if len(Y) > 1 else evs[0]
        grid_tuple = (grid["L"], set(grid["residues"]), grid["coverage"], grid["confidence"])
        resid = tri if len(Y) > 1 else np.array(
            sorted(p for p, ntr, ongrid, strength in D.build_seam_list(evs, sts, grid_tuple) if ongrid or ntr >= 1))
        widened = forced = added = 0
        for t in resid.astype(int):
            hit = [g for g in groups if g["za"] - RESID_NEAR <= t <= g["zb"] + RESID_NEAR]
            if hit:
                g = min(hit, key=lambda g: min(abs(g["za"] - t), abs(g["zb"] - t)))
                if not g["force"]:
                    # this group used phase warping in the previous round and the residue is still there -> this round
                    # falls back to the safe path (more reliable than widening)
                    for p in g["positions"]:
                        seams[p]["force"] = True
                    forced += 1
                else:
                    # already on the safe path and still residual -> widen the window and repair again
                    key_p = min(g["positions"], key=lambda p: abs(p - t))
                    s = seams[key_p]
                    if s["gap"] < MAX_GAP:
                        ext = min(s["gap"] // 2, (MAX_GAP - s["gap"]) // 2)
                        s["gap"] += 2 * ext; s["za"] -= ext; s["zb"] += ext
                        widened += 1
            else:
                Lg, residues, conf = grid["L"], set(grid["residues"]), grid["confidence"]
                ongrid = (t % Lg) in residues if residues else False
                if ongrid and conf >= D.GRID_CONFIDENCE_MIN:
                    seams[t] = dict(gap=D.GAP0, za=t, zb=t, ntracks=1, ongrid=True, strength=0.0, force=False)
                    added += 1
        history.append(dict(pass_=it, residual=int(len(resid)), forced_fallback=forced, widened=widened, added=added))
        if progress:
            progress(state="repairing", history=history)
        if forced == 0 and widened == 0 and added == 0:
            break
    mode_by_pos = {}
    for rec in records:
        for p in rec["group"]["positions"]:
            mode_by_pos[p] = rec["info"].get("mode", "?")
    return Y, seams, grid, history, mode_by_pos
