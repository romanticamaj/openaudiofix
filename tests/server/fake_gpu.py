"""A stand-in for openaudiofix.gpu: no pip, no nvidia-smi, no torch. It records what was asked of it."""
from openaudiofix.gpu import GpuUnavailable


class FakeGpu:
    ESTIMATED_BYTES = int(3.6e9)
    WEIGHTS_BYTES = int(0.2e9)
    GpuUnavailable = GpuUnavailable

    def __init__(self, gpu=True, present=False, disabled=False, selftest_ok=True):
        self.gpu, self.present, self.disabled, self.selftest_ok = gpu, present, disabled, selftest_ok
        self.calls = []
        self.install_error = None
        self.reports = []

    def nvidia(self):
        self.calls.append("nvidia")
        if not self.gpu:
            raise GpuUnavailable("nvidia_missing", "nvidia-smi was not found")
        return "RTX Test", "16303 MiB", "591.86"

    def packages_present(self):
        return self.present

    def install_disabled(self):
        return self.disabled

    def install(self, report):
        self.calls.append("install")
        if self.install_error:
            raise RuntimeError(self.install_error)
        for pct in (10.0, 50.0, 100.0):
            report(pct=pct, done_bytes=int(pct / 100 * self.ESTIMATED_BYTES), phase="packages")
        self.present = True

    def download_weights(self):
        self.calls.append("weights")

    def selftest(self):
        self.calls.append("selftest")
        if self.selftest_ok:
            return {"ok": True, "step": "done", "gpu": "RTX Test", "speed_x": 40.0, "load_s": 2.0}, 0
        return {"ok": False, "step": "inference", "error": "boom"}, 1

    def uninstall(self):
        self.calls.append("uninstall")
        self.present = False


class FakeProc:
    """A Popen look-alike for the GPU worker."""

    def __init__(self):
        self.alive = True
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False

    def kill(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


class Spawner:
    def __init__(self):
        self.procs = []

    def __call__(self, data_dir):
        p = FakeProc()
        self.procs.append(p)
        return p
