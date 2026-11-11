"""The RepairModule contract shared by the CLI, the CPU worker and the GPU worker."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..errors import StageCheckError


@dataclass(frozen=True)
class Fix:
    start: int          # first changed sample
    end: int            # exclusive
    channel: int        # -1 = all channels
    kind: str           # "seam", "clip+", "clip-", "denoise"


@dataclass
class ScanResult:
    module: str
    detected: bool
    count: int
    summary: dict = field(default_factory=dict)


@dataclass
class RepairResult:
    audio: np.ndarray   # float64, shape (n, channels), no gain applied
    fixes: list
    notes: dict = field(default_factory=dict)


class RepairModule:
    id = ""
    name = ""
    device = "cpu"
    default_order = 0
    whole_file = False
    uses_source = False  # True: repair() also takes source=, the chain's untouched input
    params: dict = {"type": "object", "properties": {}}

    def scan(self, audio, sr) -> ScanResult:
        raise NotImplementedError

    def repair(self, audio, sr, params, progress) -> RepairResult:
        raise NotImplementedError

    def describe(self) -> dict:
        return dict(id=self.id, name=self.name, device=self.device, default_order=self.default_order,
                    whole_file=self.whole_file, params=self.params)


def run_repair(module, audio, sr, params, progress, source):
    """Call module.repair, handing over the chain's source when the module asks for it."""
    if module.uses_source:
        return module.repair(audio, sr, params, progress, source=source)
    return module.repair(audio, sr, params, progress)


def channels_of(audio):
    a = np.asarray(audio, dtype=np.float64)
    return [np.ascontiguousarray(a[:, c]).copy() for c in range(a.shape[1])]


def changed_regions(before, after, kind, merge=64):
    """Fix regions covering every sample that differs (any channel); gaps <= merge are joined."""
    m = np.any(np.asarray(before) != np.asarray(after), axis=1)
    idx = np.flatnonzero(m)
    if not len(idx):
        return []
    breaks = np.flatnonzero(np.diff(idx) > merge)
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]] + 1
    return [Fix(int(a), int(b), -1, kind) for a, b in zip(starts, ends)]


def check_output(inp, out):
    if out.shape != inp.shape:
        raise StageCheckError(f"module changed the shape from {inp.shape} to {out.shape}")
    if not np.isfinite(out).all():
        raise StageCheckError("module produced NaN or Inf samples")
