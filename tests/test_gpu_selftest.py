import json

import numpy as np

from server import gpu_selftest as G


def test_evaluate_output_accepts_good_and_names_each_failure():
    x = np.full(1000, 0.1)
    assert G.evaluate_output(x, x * 0.8) == []
    assert "length" in G.evaluate_output(x, x[:-1])[0]
    bad = x.copy()
    bad[3] = np.nan
    assert "NaN" in G.evaluate_output(x, bad)[0]
    assert "peak" in G.evaluate_output(x, x * 20)[0]
    assert "silence" in G.evaluate_output(x, x * 1e-3)[0]


def test_main_reports_cuda_step_when_torch_missing(monkeypatch, capsys):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "torch":
            raise ImportError("no torch")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert G.main() == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is False and out["step"] == "cuda" and "torch" in out["error"]


def test_evaluate_output_zero_rms_input_does_not_divide_by_zero():
    z = np.zeros(100)
    assert G.evaluate_output(z, z) == []


def test_main_success_path_warms_up_before_timing(monkeypatch, capsys):
    import sys
    import types

    class T:
        def __init__(self, a): self.a = a
        def cuda(self): return self
        def cpu(self): return self
        def __matmul__(self, o): return T(self.a @ o.a)
        def __sub__(self, o): return T(self.a - o.a)
        def abs(self): return T(np.abs(self.a))
        def max(self): return T(self.a.max())
        def __float__(self): return float(self.a)

    class Gen:
        def manual_seed(self, s):
            self.rng = np.random.default_rng(s)
            return self

    fake = types.ModuleType("torch")
    fake.cuda = types.SimpleNamespace(is_available=lambda: True, get_device_name=lambda i: "Fake GPU")
    fake.Generator = Gen
    fake.randn = lambda *shape, generator: T(generator.rng.standard_normal(shape))
    monkeypatch.setitem(sys.modules, "torch", fake)

    events = []

    class FakeBackend:
        default_window = 4.0

        def __init__(self): events.append("load")

        def __call__(self, x48, window=None):
            events.append(len(x48))
            return np.asarray(x48) * 0.8

    from openaudiofix.modules import get
    mod = get("denoise")
    monkeypatch.setattr(mod, "backend_factory", FakeBackend)
    monkeypatch.setattr(mod, "_backend", None)

    assert G.main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is True and out["step"] == "done" and out["gpu"] == "Fake GPU"
    assert out["speed_x"] > 0 and "load_s" in out
    assert events == ["load", 48000, 5 * 48000]


def test_run_returns_the_result_in_process(monkeypatch, capsys):
    """final review I2: the GPU worker calls run() in-process; main() only prints it."""
    res = {"ok": True, "step": "done", "gpu": "G", "speed_x": 9.0, "load_s": 1.0, "error": ""}
    monkeypatch.setattr(G, "run", lambda: dict(res))
    assert G.main() == 0 and json.loads(capsys.readouterr().out)["gpu"] == "G"
    monkeypatch.setattr(G, "run", lambda: dict(res, ok=False, step="cuda"))
    assert G.main() == 1
