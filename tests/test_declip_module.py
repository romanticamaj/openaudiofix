import numpy as np
import pytest

from openaudiofix import modules, synth
from openaudiofix.algorithms import declip as DC


@pytest.mark.slow
def test_declip_changes_only_clipped_samples_and_lists_every_run():
    x = synth.inject_clipping(synth.speech_like(seconds=3.0, seed=1, channels=2), 8.0)
    res = modules.get("declip").repair(x, 48000, {}, lambda **k: None)
    mask = np.ones(x.shape, bool)
    for f in res.fixes:
        assert f.kind in ("clip+", "clip-") and f.channel in (0, 1)
        mask[f.start:f.end, f.channel] = False
    assert np.array_equal(res.audio[mask], x[mask])
    y0, _ = DC.declip_hybrid(x[:, 0].copy(), 48000)
    assert np.array_equal(res.audio[:, 0], y0)
    assert res.notes["peak"] > 1.0 and res.notes["suggested_gain_db"] < 0


def test_declip_scan_counts_runs_and_reports_clean_input():
    x = synth.speech_like(seconds=2.0, seed=2)
    assert modules.get("declip").scan(x, 48000).detected is False
    y = synth.inject_clipping(x, 8.0)
    r = modules.get("declip").scan(y, 48000)
    assert r.detected and r.count > 10


@pytest.mark.slow
def test_declip_progress_reaches_100_over_channels():
    x = synth.inject_clipping(synth.speech_like(seconds=2.0, seed=3, channels=2), 6.0)
    seen = []
    modules.get("declip").repair(x, 48000, {}, lambda pct=None, message=None: seen.append(pct))
    vals = [p for p in seen if p is not None]
    assert vals == sorted(vals) and vals[-1] <= 100.0 and vals[-1] > 50.0
