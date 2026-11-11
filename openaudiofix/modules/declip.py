"""Clipping repair: frame-joint AR + A-SPADE averaged on clipped samples (ADR-0008)."""
import numpy as np

from ..algorithms import declip as DC
from .base import Fix, RepairModule, RepairResult, ScanResult, channels_of


class DeclipModule(RepairModule):
    id = "declip"
    name = "module.declip"
    device = "cpu"
    default_order = 10

    def scan(self, audio, sr):
        n, detected = 0, False
        for ch in channels_of(audio):
            thr_pos, thr_neg, det = DC.estimate_threshold(ch)
            if det:
                detected = True
                n += len(DC.find_clip_runs(ch, thr_pos, thr_neg))
        return ScanResult(self.id, detected, n, {})

    def repair(self, audio, sr, params, progress):
        chans = channels_of(audio)
        nch = len(chans)
        Y, fixes, detected = [], [], False
        for ci, ch in enumerate(chans):
            def _p(i, n, _ci=ci):
                progress(pct=round((_ci + i / max(n, 1)) / nch * 100, 1), message=f"channel {_ci + 1}/{nch}")
            y, info = DC.declip_hybrid(ch, sr, progress=_p)
            if info.get("detected"):
                detected = True
                for a, b, sg in DC.find_clip_runs(ch, info["thr_pos"], info["thr_neg"]):
                    fixes.append(Fix(a, b, ci, "clip+" if sg > 0 else "clip-"))
            Y.append(y)
        out = np.stack(Y, axis=1)
        peak = float(np.abs(out).max()) if out.size else 0.0
        gain = -20 * np.log10(peak) if peak > 1.0 else 0.0
        notes = dict(detected=detected, peak=round(peak, 6), suggested_gain_db=round(float(gain), 2),
                     n_runs=len(fixes))
        return RepairResult(out, sorted(fixes, key=lambda f: (f.start, f.channel)), notes)
