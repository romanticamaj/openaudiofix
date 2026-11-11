import numpy as np
import pytest

from openaudiofix import chain, modules, synth
from openaudiofix.errors import GpuOutOfMemory, ModuleUnavailable
from openaudiofix.modules import denoise as DN


class FakeBackend:
    default_window = 4.0

    def __init__(self, fail_times=0, delay=7):
        self.fail_times, self.delay, self.windows = fail_times, delay, []

    def __call__(self, x48, decode_window=None):
        self.windows.append(decode_window)
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
        y = 0.5 * np.asarray(x48, dtype=np.float64)
        return np.r_[np.zeros(self.delay), y[: len(y) - self.delay]]


@pytest.fixture
def mod(monkeypatch):
    m = modules.get("denoise")
    fake = FakeBackend()
    monkeypatch.setattr(m, "backend_factory", lambda: fake)
    monkeypatch.setattr(m, "_backend", None)
    return m, fake


def test_denoise_keeps_shape_and_compensates_latency(mod):
    m, fake = mod
    x = synth.speech_like(sr=44100, seconds=2.0, channels=2)
    res = m.repair(x, 44100, {}, lambda **k: None)
    assert res.audio.shape == x.shape and res.fixes == [DN.Fix(0, len(x), -1, "denoise")]
    assert np.corrcoef(res.audio[:, 0], x[:, 0])[0, 1] > 0.99


def test_denoise_halves_window_on_oom_then_succeeds(mod):
    m, fake = mod
    fake.fail_times = 2
    m.repair(synth.speech_like(seconds=1.0), 48000, {}, lambda **k: None)
    assert fake.windows == [None, 2.0, 1.0]


def test_denoise_gives_up_after_two_retries(mod):
    m, fake = mod
    fake.fail_times = 3
    with pytest.raises(GpuOutOfMemory):
        m.repair(synth.speech_like(seconds=1.0), 48000, {}, lambda **k: None)


def test_denoise_scan_reports_noise_floor_on_cpu():
    x = synth.speech_like(seconds=2.0)
    x = x + 10 ** (-40 / 20) * np.random.default_rng(0).standard_normal(x.shape)
    r = modules.get("denoise").scan(x, 48000)
    assert r.detected and -45 < r.summary["noise_floor_dbfs"] < -35


def test_real_backend_unavailable_without_clearvoice():
    try:
        import clearvoice  # noqa: F401
        pytest.skip("clearvoice is installed here")
    except ImportError:
        pass
    with pytest.raises(ModuleUnavailable):
        DN.MossFormerBackend()


def test_chain_glitch_declip_denoise_skips_verify_unchanged(mod):
    """Spec 5.3 / ruling P12: a whole-file module in the chain disables verify_unchanged."""
    m, fake = mod
    sr, seconds, gap = 48000, 1.0, 64
    pos = synth.dropout_positions(sr, seconds, 2)
    x = synth.speech_like(sr=sr, seconds=seconds + 2 * gap / sr + 0.01, seed=7, channels=2)
    x = synth.inject_clipping(synth.inject_dropouts(x, pos, gap)[: int(seconds * sr)], 6.0)
    seen = []
    stages = chain.run_chain(x, sr, ["denoise", "declip", "glitch"],
                             progress=lambda **kw: seen.append(kw["module"]))
    assert [s.module for s in stages] == ["declip", "glitch", "denoise"]
    assert list(dict.fromkeys(seen)) == ["declip", "glitch", "denoise"]
    for s in stages:
        assert s.audio.shape == x.shape
    assert fake.windows == [None, None]  # one call per channel, no OOM back-off
    # denoise rewrote the whole file, which verify_unchanged would have rejected as an integrity failure
    assert not np.array_equal(stages[1].audio, stages[2].audio)
    assert stages[2].fixes == [DN.Fix(0, len(x), -1, "denoise")]
    with pytest.raises(chain.IntegrityError):
        chain.verify_unchanged(x, stages[-1].audio, [f for _, f in chain.all_fixes(stages[:2])])
