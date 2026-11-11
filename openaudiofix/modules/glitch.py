"""Buffer-dropout glitch repair (v4 phase-aligned micro-warp with per-hole fallback)."""
from collections import Counter

import numpy as np

from ..algorithms import declick as DK
from ..algorithms import declick_detect as D
from ..algorithms.wavio import jsonable
from .base import RepairModule, RepairResult, ScanResult, changed_regions, channels_of


class GlitchModule(RepairModule):
    id = "glitch"
    name = "module.glitch"
    device = "cpu"
    # After declip: phase micro-warp across a clipped plateau overshoots full scale and breaks the plateau,
    # so declip run afterwards misses it. Seams are still found on the untouched source, see repair().
    default_order = 20
    uses_source = True

    def scan(self, audio, sr):
        seams, grid = D.detect_seams(channels_of(audio), sr)
        return ScanResult(self.id, bool(seams), len(seams), {"grid": jsonable(grid.get("L"))})

    def repair(self, audio, sr, params, progress, source=None):
        """source: the chain's input before declip. Seam detection runs there, because declip's reconstruction
        across a dropout can smooth the step the detector looks for; the repair itself runs on `audio`."""
        def prog(**kw):
            if "total" in kw:  # inside a pass: passes run until nothing is left, at most DK.MAX_PASS of them
                frac = (kw["pass_"] - 1 + kw["done"] / max(kw["total"], 1)) / DK.MAX_PASS
                progress(pct=round(5 + 94 * frac, 1), message=f"pass {kw['pass_']}: {kw['done']}/{kw['total']}")
            else:
                progress(pct=None, message=f"pass {kw['pass_']}" if "pass_" in kw else kw.get("state"))

        progress(pct=0.0, message="detecting seams")
        detected = D.detect_seams(channels_of(audio if source is None else source), sr)
        progress(pct=5.0, message="repairing")
        Y, seams, grid, history, mode_by_pos = DK.repair_file(channels_of(audio), sr, None, progress=prog,
                                                              detected=detected)
        out = np.stack(Y, axis=1)
        notes = dict(n_seams=len(seams), n_seams_detected=len(detected[0]), grid=jsonable(grid.get("L")),
                     passes=len(history), modes=dict(Counter(mode_by_pos.values())))
        return RepairResult(out, changed_regions(audio, out, "seam"), notes)
