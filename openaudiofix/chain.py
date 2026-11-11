"""Run modules in their fixed order; every stage output is kept."""
from dataclasses import dataclass

import numpy as np

from . import modules
from .errors import IntegrityError
from .modules.base import check_output, run_repair


@dataclass
class StageOutput:
    module: str
    audio: np.ndarray
    fixes: list
    notes: dict


def reduce_gain_for(module_ids):
    """Export gain policy (spec 4.3): only glitch-only chains keep 0 dB and the v5 byte patch (v5 _run_declick).

    Any chain with another module (declip, denoise, ...) may exceed full scale and reduces gain instead of clipping.
    Speech only fades parts of its input to silence, so it never raises the peak and does not count.
    """
    return any(m not in ("glitch", "speech") for m in module_ids)


def run_chain(audio, sr, module_ids, params=None, progress=None):
    params = params or {}
    progress = progress or (lambda **kw: None)
    mods = modules.ordered(module_ids)
    source = np.asarray(audio, dtype=np.float64)
    cur = source
    out = []
    for i, m in enumerate(mods):
        def _p(pct=None, message=None, _m=m, _i=i):
            progress(module=_m.id, index=_i, total=len(mods), pct=pct, message=message)
        _p(0.0, "start")
        res = run_repair(m, cur, sr, params.get(m.id, {}), _p, source)
        check_output(cur, res.audio)
        out.append(StageOutput(m.id, res.audio, list(res.fixes), dict(res.notes)))
        cur = res.audio
    if out and not any(m.whole_file for m in mods):
        verify_unchanged(source, cur, [f for _, f in all_fixes(out)])
    return out


def all_fixes(stages):
    return [(s.module, f) for s in stages for f in s.fixes]


def verify_unchanged(source, final, fixes):
    source = np.asarray(source)
    final = np.asarray(final)
    mask = np.ones(source.shape, dtype=bool)
    for f in fixes:
        if f.channel < 0:
            mask[f.start:f.end, :] = False
        else:
            mask[f.start:f.end, f.channel] = False
    if not np.array_equal(source[mask], final[mask]):
        n = int((source[mask] != final[mask]).sum())
        raise IntegrityError(f"{n} samples outside the repaired regions changed; this is a bug, please report it.")
