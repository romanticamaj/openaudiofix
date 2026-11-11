"""Test-only modules, registered through AFIX_EXTRA_MODULES."""
import time

from openaudiofix.modules.base import RepairModule, RepairResult, ScanResult


class SleepyModule(RepairModule):
    id = "sleepy"
    name = "module.sleepy"
    device = "cpu"
    default_order = 15
    params = {"type": "object", "properties": {
        "steps": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        "mode": {"type": "string", "enum": ["a", "b"], "default": "a"}}}

    def scan(self, audio, sr):
        return ScanResult(self.id, False, 0, {})

    def repair(self, audio, sr, params, progress):
        for i in range(int(params.get("steps", 50))):
            time.sleep(0.1)
            progress(pct=i * 2.0, message="sleeping")
        return RepairResult(audio.copy(), [], {})


class FlakyModule(RepairModule):
    """Fails while the class attribute `fail` is True; otherwise returns the input unchanged."""
    id = "flaky"
    name = "module.flaky"
    device = "cpu"
    default_order = 25
    fail = True
    runs = 0

    def scan(self, audio, sr):
        return ScanResult(self.id, False, 0, {})

    def repair(self, audio, sr, params, progress):
        type(self).runs += 1
        if type(self).fail:
            raise RuntimeError("flaky module failed on purpose")
        return RepairResult(audio.copy(), [], {})


class LoudDenoiseBackend:
    """Stand-in for MossFormer2 that doubles the level, so the denoised output overshoots full scale."""
    default_window = 4.0

    def __call__(self, x48, decode_window=None):
        import numpy as np
        return 2.0 * np.asarray(x48, dtype=np.float64)


class InstalledGpu:
    """GPU manager stand-in whose denoise module is installed (the API also needs a live gpu heartbeat)."""
    def state(self):
        return {"id": "denoise", "state": "installed", "step": "done", "detail": {}}

    def busy(self):
        return False

    def tick(self):
        pass

    def startup(self):
        pass

    def start(self, action):
        return False

    def shutdown(self):
        pass
