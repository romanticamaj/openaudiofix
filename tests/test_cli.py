import json

import pytest
import soundfile as sf

from openaudiofix import cli, synth


def _clipped(tmp_path, name="in.wav"):
    p = tmp_path / name
    sf.write(p, synth.inject_clipping(synth.speech_like(seconds=2.0, seed=1), 8.0), 48000, subtype="PCM_24")
    return p


@pytest.mark.slow
def test_repair_writes_three_outputs_next_to_input(tmp_path, capsys):
    p = _clipped(tmp_path, "訪談 第1集.wav")
    assert cli.main(["repair", str(p), "-m", "declip", "-q"]) == 0
    for suffix in ("_afix.wav", "_afix_diff.wav", "_afix_fixes.csv"):
        assert (tmp_path / f"訪談 第1集{suffix}").exists()
    rows = (tmp_path / "訪談 第1集_afix_fixes.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("module,kind,channel") and len(rows) > 2


def test_scan_json(tmp_path, capsys):
    p = _clipped(tmp_path)
    assert cli.main(["scan", str(p), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["declip"]["detected"] is True and "glitch" in out


@pytest.mark.slow
def test_float_export_and_refuses_to_overwrite_input(tmp_path):
    p = _clipped(tmp_path)
    assert cli.main(["repair", str(p), "-m", "declip", "--float", "--no-diff", "-q", "-o", str(tmp_path / "f.wav")]) == 0
    assert sf.info(tmp_path / "f.wav").subtype == "FLOAT" and not (tmp_path / "f_diff.wav").exists()
    assert cli.main(["repair", str(p), "-o", str(p), "-q"]) == 2


def test_errors_map_to_exit_codes(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"x" * 1000)
    assert cli.main(["repair", str(bad), "-q"]) == 3
    p = _clipped(tmp_path)
    assert cli.main(["repair", str(p), "-m", "nope", "-q"]) == 2


def test_glitch_only_repair_exports_at_zero_db(tmp_path, capsys):
    pos = synth.dropout_positions(48000, 3.0, 6)
    x = synth.inject_dropouts(synth.speech_like(seconds=3.1, seed=5), pos, 64)[: 3 * 48000]
    p = tmp_path / "g.wav"
    sf.write(p, x, 48000, subtype="PCM_24")
    assert cli.main(["repair", str(p), "-m", "glitch", "--no-diff"]) == 0
    out = capsys.readouterr().out
    assert "gain +0.00 dB" in out and "byte-exact" in out
    assert (tmp_path / "g_afix.wav").exists()


def test_usage_errors_exit_2(tmp_path):
    assert cli.main([]) == 2
    assert cli.main(["scan"]) == 2


def test_denoise_chain_with_overshoot_reduces_gain_instead_of_clipping(tmp_path, monkeypatch, capsys):
    import numpy as np
    from openaudiofix import modules
    from tests.fakes import LoudDenoiseBackend
    m = modules.get("denoise")
    monkeypatch.setattr(m, "backend_factory", LoudDenoiseBackend)
    monkeypatch.setattr(m, "_backend", None)
    x = synth.speech_like(seconds=1.0, seed=4)
    x = x * (0.8 / np.abs(x).max())  # the fake backend doubles it: peak 1.6
    p = tmp_path / "n.wav"
    sf.write(p, x, 48000, subtype="PCM_24")
    assert cli.main(["repair", str(p), "-m", "glitch,denoise", "--no-diff"]) == 0
    assert "lowered by" in capsys.readouterr().out
    y, _ = sf.read(tmp_path / "n_afix.wav")
    assert np.abs(y).max() < 1.0
    assert np.sum(np.abs(y) > 0.999) <= 2  # only the peak reaches full scale; clipping would pin hundreds
    assert np.corrcoef(y, x[:, 0])[0, 1] > 0.99


def test_refuses_when_any_output_is_the_input(tmp_path):
    p = _clipped(tmp_path, "take_diff.wav")
    before = p.read_bytes()
    # the diff path (take_diff.wav) would be the input
    assert cli.main(["repair", str(p), "-o", str(tmp_path / "take.wav"), "-q", "-m", "glitch"]) == 2
    assert p.read_bytes() == before and not (tmp_path / "take.wav").exists()
    q = tmp_path / "t_fixes.csv"
    _clipped(tmp_path, "t.wav").rename(q)
    before = q.read_bytes()
    # the CSV path (t_fixes.csv) would be the input
    assert cli.main(["repair", str(q), "-o", str(tmp_path / "t.wav"), "-q", "-m", "glitch"]) == 2
    assert q.read_bytes() == before and not (tmp_path / "t.wav").exists()
