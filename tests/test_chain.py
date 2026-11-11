import numpy as np
import pytest

from openaudiofix import chain, modules, report, synth
from openaudiofix.errors import IntegrityError
from openaudiofix.modules.base import Fix


def _both_defects(sr=48000, seconds=4.0, channels=2):
    gap, n = 64, 6
    pos = synth.dropout_positions(sr, seconds, n)
    x = synth.speech_like(sr=sr, seconds=seconds + n * gap / sr + 0.01, seed=7, channels=channels)
    y = synth.inject_dropouts(x, pos, gap)[: int(seconds * sr)]
    return synth.inject_clipping(y, 6.0)


@pytest.mark.slow
def test_chain_runs_in_default_order_and_feeds_each_stage():
    x = _both_defects()
    stages = chain.run_chain(x, 48000, ["glitch", "declip"])
    assert [s.module for s in stages] == ["declip", "glitch"]
    d = modules.get("declip").repair(x, 48000, {}, lambda **k: None)
    assert np.array_equal(stages[0].audio, d.audio)
    chain.verify_unchanged(x, stages[-1].audio, [f for _, f in chain.all_fixes(stages)])


@pytest.mark.slow
def test_chain_declips_every_plateau_even_when_glitch_overshoots(monkeypatch):
    """Bug on a real recording: glitch's micro-warp across a plateau overshot full scale (1.14), so declip run after it estimated
    its threshold from the overshoot and repaired nothing. Declip must see the untouched plateaus."""
    from openaudiofix.modules.base import RepairResult
    x = _both_defects(channels=1)
    alone = modules.get("declip").repair(x, 48000, {}, lambda **k: None)
    assert alone.notes["n_runs"] > 0
    g = modules.get("glitch")

    def overshooting(audio, sr, params, progress, source=None):
        out = np.array(audio, dtype=np.float64)
        i = int(np.argmax(np.abs(out[:, 0])))
        out[i:i + 8] *= 1.14
        return RepairResult(out, [Fix(i, i + 8, -1, "seam")], {})
    monkeypatch.setattr(g, "repair", overshooting)
    stages = chain.run_chain(x, 48000, ["glitch", "declip"])
    dc = next(s for s in stages if s.module == "declip")
    assert dc.notes["detected"] and dc.notes["n_runs"] == alone.notes["n_runs"]


@pytest.mark.slow
def test_chain_glitch_detects_seams_on_the_source():
    """Glitch repairs the declipped audio but finds its seams on the untouched source."""
    from openaudiofix.algorithms import declick_detect as D
    x = _both_defects()
    seams, _ = D.detect_seams([x[:, c] for c in range(x.shape[1])], 48000)
    stages = chain.run_chain(x, 48000, ["glitch", "declip"])
    g = next(s for s in stages if s.module == "glitch")
    assert g.notes["n_seams_detected"] == len(seams) > 0


def test_chain_without_clipping_matches_glitch_alone():
    sr, gap = 48000, 64
    pos = synth.dropout_positions(sr, 3.0, 5)
    x = synth.speech_like(sr=sr, seconds=3.0 + 5 * gap / sr + 0.01, seed=3, channels=2) * 0.3
    x = synth.inject_dropouts(x, pos, gap)[: 3 * sr]
    stages = chain.run_chain(x, sr, ["glitch", "declip"])
    g = modules.get("glitch").repair(x, sr, {}, lambda **k: None)
    assert np.array_equal(stages[-1].audio, g.audio)


@pytest.mark.slow
@pytest.mark.parametrize("sr,channels", [(44100, 2), (48000, 6)])
def test_chain_handles_44k1_and_six_channels(sr, channels):
    x = _both_defects(sr=sr, seconds=3.0, channels=channels)
    stages = chain.run_chain(x, sr, ["glitch", "declip"])
    assert stages[-1].audio.shape == x.shape
    assert len(stages[-1].audio) == len(x)


def test_verify_unchanged_catches_stray_edits():
    x = np.zeros((1000, 2))
    y = x.copy()
    y[500, 1] = 0.1
    with pytest.raises(IntegrityError):
        chain.verify_unchanged(x, y, [Fix(10, 20, -1, "seam")])
    chain.verify_unchanged(x, y, [Fix(499, 501, 1, "clip+")])


def test_fixes_rows_and_timecode():
    st = chain.StageOutput("declip", np.zeros((10, 1)), [Fix(48000 * 61 + 24, 48000 * 61 + 30, 0, "clip+")], {})
    rows = report.fixes_rows([st], 48000)
    assert rows[0]["timecode"] == "00:01:01.000" and rows[0]["length"] == 6 and rows[0]["module"] == "declip"


def test_timecode_floors_to_millisecond():
    assert report.timecode(0.0019999) == "00:00:00.001"
    assert report.timecode(3661.5) == "01:01:01.500"


def test_write_fixes_csv(tmp_path):
    st = chain.StageOutput("glitch", np.zeros((10, 1)), [Fix(5, 9, -1, "seam")], {})
    p = tmp_path / "f.csv"
    report.write_fixes_csv(p, [st], 48000)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("module,kind,channel")
    assert lines[1].startswith("glitch,seam,-1,5,9,4,")


def test_reduce_gain_for():
    assert chain.reduce_gain_for(["glitch", "declip"]) is True
    assert chain.reduce_gain_for(["declip"]) is True
    assert chain.reduce_gain_for(["glitch"]) is False
    assert chain.reduce_gain_for(["glitch", "speech"]) is False
    assert chain.reduce_gain_for(["declip", "speech"]) is True
    assert chain.reduce_gain_for([]) is False
    # final review I1: only glitch-only chains keep the v5 byte patch; denoise can overshoot too
    assert chain.reduce_gain_for(["denoise"]) is True
    assert chain.reduce_gain_for(["glitch", "denoise"]) is True
    assert chain.reduce_gain_for(["glitch", "declip", "denoise"]) is True
