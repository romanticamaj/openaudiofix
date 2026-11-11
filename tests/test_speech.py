import io

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import butter, sosfilt

from openaudiofix import cli, speech as SP
from openaudiofix.errors import InputError, ModuleUnavailable

SR = 48000
S = SP.Settings()
RNG = np.random.default_rng(7)


def db2a(d):
    return 10 ** (d / 20)


def _noise(seconds, dbfs=-70.0, seed=0):
    return db2a(dbfs) * np.random.default_rng(seed).standard_normal(int(seconds * SR))


def _harmonic(seconds, f0=220.0, dbfs=-20.0, syllables=True, seed=0):
    """A voice-like harmonic tone (f0 plus 4 harmonics). With syllables, 0.2 s syllables of random loudness
    (peaks from dbfs - 15 dB to dbfs), each rising from silence and falling back, like words."""
    t = np.arange(int(seconds * SR)) / SR
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 6))
    x = x / np.sqrt(np.mean(x ** 2)) * db2a(dbfs)
    if syllables:
        syl = int(0.2 * SR)
        gains = db2a(np.random.default_rng(seed).uniform(-15, 0, len(t) // syl + 1))
        env = np.sin(np.pi * (np.arange(len(t)) % syl) / syl) * gains[np.arange(len(t)) // syl]
        x = x * env
    return x


def _place(x, at, y):
    a = int(at * SR)
    x[a:a + len(y)] += y[: len(x) - a]
    return x


class FakeVAD:
    """VAD stand-in: probability `value` inside the given time ranges (seconds), else 0; or level-driven."""
    def __init__(self, ranges=(), value=0.9, level_db=None):
        self.ranges, self.value, self.level_db = ranges, value, level_db

    def probs(self, x16, progress=None):
        n = len(x16) // SP.CHUNK
        if self.level_db is not None:
            rms = np.sqrt((x16[:n * SP.CHUNK].reshape(n, -1) ** 2).mean(axis=1))
            return (rms > db2a(self.level_db)).astype(np.float32)
        p = np.zeros(n, np.float32)
        for a, b in self.ranges:
            p[int(a / SP.HOP_S):int(b / SP.HOP_S)] = self.value
        return p


# ---------------------------------------------------------------- 1. noise floor
@pytest.mark.parametrize("turn,period", [(1.0, 5.0), (2.4, 3.0)])
def test_noise_floor_is_found_whatever_the_share_of_speech(turn, period):
    """Room at -70 dBFS with 20 % or 80 % of the file speech: the floor is the room either way."""
    x = _noise(30.0, -70.0)
    for k, at in enumerate(np.arange(0.3, 30.0 - turn, period)):
        _place(x, at, _harmonic(turn, 180 + 20 * k, -30, seed=k))
    floor = SP.noise_floor(SP.frame_level(x, SR, 100.0))
    assert floor == pytest.approx(-70.0, abs=1.0)


def test_noise_floor_of_digital_silence():
    assert SP.noise_floor(SP.frame_level(np.zeros(SR), SR)) == -110.0


# ---------------------------------------------------------------- 2. hysteresis gate
def test_hysteresis_opens_at_open_and_closes_at_close():
    up = np.linspace(-80, -40, 41)          # 1 dB per frame
    lvl = np.r_[up, up[::-1]]
    on = SP.hysteresis(lvl, -58.0, -66.0)
    first = int(np.flatnonzero(on)[0])
    last = int(np.flatnonzero(on)[-1])
    assert lvl[first] == -58.0 and not on[first - 1]
    assert lvl[last] >= -66.0 and lvl[last + 1] < -66.0


def test_smoothing_keeps_a_short_spike_from_opening_and_a_loud_one_from_being_speech():
    x = _noise(4.0, -70.0)
    # a 5 ms spike 25 dB over the floor: one frame over open, but not after the 30 ms smoothing
    _place(x, 1.0, db2a(-70 + 25 + 3) * RNG.standard_normal(int(0.005 * SR)))
    an = SP.analyze(x, SR, S, FakeVAD())
    assert an.islands == [] and an.spans == []
    # a loud 5 ms click does open the gate, but the island is a click, not speech
    _place(x, 2.0, 0.3 * RNG.standard_normal(int(0.005 * SR)))
    an = SP.analyze(x, SR, S, FakeVAD())
    assert [k for _, _, k in an.islands] == ["click"] and an.spans == []


# ---------------------------------------------------------------- 3. island classes
def test_voiced_island_the_vad_misses_is_kept():
    x = _place(_noise(4.0), 1.5, _harmonic(1.0, 500.0, -25))     # a character voice: high, sung, VAD silent
    an = SP.analyze(x, SR, S, FakeVAD())
    assert [k for _, _, k in an.islands] == ["voiced"] and an.counts["voiced"] == 1
    (a, b), = an.spans
    assert a == pytest.approx(1.5 - S.pad_before, abs=0.03) and b == pytest.approx(2.5 + S.pad_after, abs=0.03)


def test_thud_is_removed():
    t = np.arange(int(0.3 * SR)) / SR
    lp = sosfilt(butter(4, 250, "lowpass", fs=SR, output="sos"), RNG.standard_normal(len(t)))
    thud = lp / np.abs(lp).max() * 0.9 * np.exp(-t / 0.03)     # a knock on the table: low, impulsive, unvoiced
    x = _place(_noise(4.0), 2.0, thud)
    an = SP.analyze(x, SR, S, FakeVAD())
    assert [k for _, _, k in an.islands] == ["thud"] and an.spans == []


def test_quiet_word_the_vad_hears_is_kept_by_lowering_the_gate():
    # a word only 14 dB over the floor: under the +18 dB gate, but the VAD is sure, so the gate is lowered
    x = _place(_noise(4.0), 1.5, _harmonic(0.6, 200.0, -70 + 14, syllables=False))
    an = SP.analyze(x, SR, S, FakeVAD(ranges=[(1.5, 2.1)]))
    assert an.calibrated and an.open_db < S.open_db
    assert [k for _, _, k in an.islands] == ["speech"] and len(an.spans) == 1
    assert SP.analyze(x, SR, SP.Settings(calibrate=False), FakeVAD(ranges=[(1.5, 2.1)])).spans == []


def test_long_unbroken_sound_the_vad_never_hears_is_music():
    x = _place(_noise(12.0), 2.0, _harmonic(8.0, 330.0, -25, syllables=False))
    an = SP.analyze(x, SR, S, FakeVAD())
    assert [k for _, _, k in an.islands] == ["music"] and an.spans == []
    assert [k for _, _, k in SP.analyze(x, SR, SP.Settings(music=False), FakeVAD()).islands] == ["voiced"]


def _calls_over_residue(seconds=8.0, every=2.0, call=0.3):
    """Short voiced calls ("oh, oh, oh") with quiet residue between them, as denoised audio has: one long island."""
    x = _place(_noise(seconds + 4.0, -95.0), 2.0, _noise(seconds, -75.0, seed=1))
    for at in np.arange(2.0, 2.0 + seconds, every):
        _place(x, at, _harmonic(call, 330.0, -25, syllables=False))
    return x


def test_voiced_calls_with_pauses_are_a_voice_even_if_the_vad_never_hears_them():
    an = SP.analyze(_calls_over_residue(), SR, S, FakeVAD())
    assert [k for _, _, k in an.islands] == ["voiced"] and len(an.spans) == 1


def test_a_long_sound_the_vad_heard_even_once_is_not_music():
    x = _place(_noise(12.0), 2.0, _harmonic(8.0, 330.0, -25, syllables=False))
    an = SP.analyze(x, SR, S, FakeVAD(ranges=[(5.0, 5.4)]))  # mean probability stays under music_vad
    assert [k for _, _, k in an.islands] == ["speech"]


def test_hum_under_the_high_pass_does_not_open_the_gate():
    t = np.arange(int(6 * SR)) / SR
    hum = db2a(-55) * np.sin(2 * np.pi * 60 * t)        # 15 dB over the room, but under 100 Hz
    x = _place(_noise(6.0) + hum, 2.0, _harmonic(1.5, 220.0, -25))
    an = SP.analyze(x, SR, S, FakeVAD(ranges=[(2.0, 3.5)]))
    assert an.floor_db == pytest.approx(-70.0, abs=1.5)
    (a, b), = an.spans
    assert a > 1.8 and b < 3.8


def test_sensitivity_presets():
    assert (SP.Settings.for_sensitivity("high").open_db, SP.Settings.for_sensitivity("high").close_db) == (14, 6)
    assert (SP.Settings.for_sensitivity("mid").open_db, SP.Settings.for_sensitivity("mid").close_db) == (18, 10)
    assert (SP.Settings.for_sensitivity("low").open_db, SP.Settings.for_sensitivity("low").close_db) == (22, 14)
    with pytest.raises(InputError):
        SP.Settings.for_sensitivity("max")


# ---------------------------------------------------------------- 4. margins and pauses
def test_keep_mask_pads_and_merges_short_pauses():
    n = 1000
    s = SP.Settings(pad_before=0.05, pad_after=0.10, min_gap=0.3)
    keep = SP.keep_mask(n, [(100, 200), (200 + 10 + 5 + 29, 400)], s)    # 0.29 s between the padded spans
    assert SP.spans_of(keep) == [(95, 410)]
    keep = SP.keep_mask(n, [(100, 200), (200 + 10 + 5 + 30, 400)], s)    # exactly 0.3 s: cut
    assert SP.spans_of(keep) == [(95, 210), (240, 410)]
    # pauses touching the file's ends are not pauses: a short lead-in stays removed
    assert SP.spans_of(SP.keep_mask(n, [(10, 50), (980, 990)], s)) == [(5, 60), (975, 1000)]


def test_half_second_margins_keep_every_pause_under_a_second():
    s = SP.Settings(pad_before=0.5, pad_after=0.5, min_gap=0.0)
    for pause, parts in ((0.99, 1), (1.0, 1), (1.2, 2)):
        b = 100 + int(round(pause * 100))
        assert len(SP.spans_of(SP.keep_mask(1000, [(10, 100), (b, b + 50)], s))) == parts


def test_union_keeps_speech_from_any_track():
    assert SP.union([[(0, 1), (5, 6)], [(0.5, 2), (8, 9)]]) == [(0, 2), (5, 6), (8, 9)]


def test_removed_spans_and_their_labels():
    gaps = SP.removed_spans([(1.0, 2.0), (3.0, 5.0)], 6.0)
    assert gaps == [(0.0, 1.0), (2.0, 3.0), (5.0, 6.0)]
    islands = [(1.0, 2.0, "speech"), (2.2, 2.5, "thud"), (5.2, 5.3, "noise"), (5.4, 5.45, "click")]
    assert [SP.label_of(g, islands) for g in gaps] == ["removed", "thud", "noise"]


# ---------------------------------------------------------------- 5. two tracks that add up to the input
def _tone(seconds, f=220.0):
    t = np.arange(int(seconds * SR)) / SR
    return 0.5 * np.sin(2 * np.pi * f * t)


def test_split_into_speech_and_removed_that_add_up_to_the_input():
    x = np.stack([_tone(10), _tone(10, 330)], axis=1)
    spans = [(1.0, 3.0), (5.0, 6.0), (8.0, 10.0)]
    speech, removed = SP.split(x, SR, spans, S)
    f = int(round(S.fade * SR))
    assert speech.shape == removed.shape == x.shape
    assert np.allclose(speech + removed, x, rtol=0, atol=1e-15)
    # every sample belongs to one track, except inside the fades at the edges of a kept span
    g = np.zeros(len(x), bool)
    for a, b in spans:
        g[int(a * SR):int(b * SR)] = True
    both = (speech != 0).any(axis=1) & (removed != 0).any(axis=1)
    assert not both[~g].any() and both[g].sum() <= 2 * len(spans) * f
    # inside a span the speech is the input sample for sample and the removed track is silent; outside, the reverse
    inside = slice(int(1.0 * SR) + f, int(3.0 * SR) - f)
    outside = slice(int(3.0 * SR), int(5.0 * SR))
    assert np.array_equal(speech[inside], x[inside]) and not removed[inside].any()
    assert np.array_equal(removed[outside], x[outside]) and not speech[outside].any()
    # the last span runs to the end of the file: no fade there; the file start is removed material
    assert np.array_equal(speech[-100:], x[-100:]) and np.array_equal(removed[:100], x[:100])
    # fades, not steps: no sample-to-sample jump bigger than the tone's own
    for y in (speech, removed):
        assert np.abs(np.diff(y, axis=0)).max() <= 1.01 * np.abs(np.diff(x, axis=0)).max()


# ---------------------------------------------------------------- model
def test_model_missing_without_download(tmp_path, monkeypatch):
    monkeypatch.setenv("AFIX_MODELS", str(tmp_path))
    with pytest.raises(ModuleUnavailable):
        SP.ensure_model(download=False)


def test_download_is_checksummed(tmp_path, monkeypatch):
    monkeypatch.setenv("AFIX_MODELS", str(tmp_path))
    good = b"model bytes"
    monkeypatch.setattr(SP, "MODEL_SHA256", __import__("hashlib").sha256(good).hexdigest())
    monkeypatch.setattr(SP.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"tampered"))
    with pytest.raises(ModuleUnavailable):
        SP.ensure_model(say=lambda m: None)
    assert list(tmp_path.iterdir()) == []          # nothing half-written is left behind
    monkeypatch.setattr(SP.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(good))
    p = SP.ensure_model(say=lambda m: None)
    assert p.read_bytes() == good
    monkeypatch.setattr(SP.urllib.request, "urlopen", lambda *a, **k: pytest.fail("cached model re-downloaded"))
    assert SP.ensure_model(say=lambda m: None) == p


def test_vad_runs_use_hysteresis_and_drop_short_blips():
    hop = SP.HOP_S
    p = np.zeros(200)
    p[10:40] = 0.9
    p[40:45] = 0.4          # dips below threshold but above release: still speech
    p[45:60] = 0.9
    p[100:103] = 0.6        # 96 ms blip the model is unsure about: dropped
    p[150:156] = 0.95       # 192 ms but the model is sure: kept
    assert SP.speech_runs(p, S, hop) == [(10 * hop, 60 * hop), (150 * hop, 156 * hop)]


# ---------------------------------------------------------------- 9. CLI
@pytest.fixture
def fake_vad(monkeypatch, tmp_path):
    """Speech wherever the 16 kHz mix is louder than -20 dBFS (the tones; not the short knock); no model needed."""
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    monkeypatch.setattr(SP, "SileroVAD", lambda path: FakeVAD(level_db=-20))
    return model


def _session(tmp_path, thud_at=None):
    a = _noise(12.0, -80.0, 1)
    b = _noise(12.0, -80.0, 2)
    _place(a, 1.0, _tone(2))             # speaker A
    _place(b, 7.0, _tone(2, 330))        # speaker B, later
    if thud_at is not None:
        t = np.arange(int(0.3 * SR)) / SR
        lp = sosfilt(butter(4, 250, "lowpass", fs=SR, output="sos"), RNG.standard_normal(len(t)))
        _place(a, thud_at, lp / np.abs(lp).max() * 0.3 * np.exp(-t / 0.03))
    pa, pb = tmp_path / "a.wav", tmp_path / "b.wav"
    sf.write(pa, a, SR, subtype="PCM_24")
    sf.write(pb, b, SR, subtype="PCM_24")
    return pa, pb


def _labels(path):
    return [(float(a), float(b), k) for a, b, k in (l.split("\t") for l in path.read_text(encoding="utf-8").splitlines())]


def test_cli_splits_tracks_of_one_session_together(tmp_path, fake_vad):
    pa, pb = _session(tmp_path, thud_at=5.0)
    out = tmp_path / "out"
    assert cli.main(["speech", str(pa), str(pb), "--out-dir", str(out), "--model", str(fake_vad), "-q"]) == 0
    xa, _ = sf.read(pa)
    for name in ("a", "b"):
        for kind in ("speech", "removed"):
            info = sf.info(out / f"{name}_{kind}.wav")
            assert (info.frames, info.samplerate, info.subtype) == (len(xa), SR, "PCM_24")
    sa, _ = sf.read(out / "a_speech.wav")
    ra, _ = sf.read(out / "a_removed.wav")
    assert np.abs(sa + ra - xa).max() <= 2 * 2 ** -23        # the two tracks add up to the input (24-bit rounding)
    labels = _labels(out / "a_removed.txt")
    # before A, between A and B (B's speech kept on A too; the knock on A's table named), after B
    assert [k for _, _, k in labels] == ["removed", "thud", "removed"] and labels[0][0] == 0.0
    assert 1.0 - 0.1 <= labels[0][1] <= 1.0 - 0.03 and 3.0 + 0.08 <= labels[1][0] <= 3.0 + 0.15   # margins: 50 / 100 ms
    assert 7.0 - 0.1 <= labels[1][1] <= 7.0 - 0.03 and 9.0 + 0.08 <= labels[2][0] <= 9.0 + 0.15
    assert (out / "b_removed.txt").read_text(encoding="utf-8") == (out / "a_removed.txt").read_text(encoding="utf-8")


def test_cli_flags_set_margins_and_gate(tmp_path, fake_vad, capsys):
    pa, _ = _session(tmp_path)
    out = tmp_path / "out"
    assert cli.main(["speech", str(pa), "--out-dir", str(out), "--model", str(fake_vad), "-q",
                     "--pad-before", "0.3", "--pad-after", "0.5", "--sensitivity", "high", "--fade", "0.01"]) == 0
    labels = _labels(out / "a_removed.txt")
    assert 0.66 <= labels[0][1] <= 0.7 and 3.5 <= labels[1][0] <= 3.56   # the gate's smoothing widens by a frame
    capsys.readouterr()
    assert cli.main(["speech", str(pa), "--out-dir", str(out), "--model", str(fake_vad), "--sensitivity", "high"]) == 0
    assert "gate +14/+6 dB" in capsys.readouterr().out
    assert cli.main(["speech", str(pa), "--out-dir", str(out), "--model", str(fake_vad),
                     "--open-db", "30", "--close-db", "20", "--hp", "0"]) == 0
    assert "gate +30/+20 dB" in capsys.readouterr().out
    assert cli.main(["speech", str(pa), "--model", str(fake_vad), "-q", "--sensitivity", "max"]) == 2


def test_cli_reports_no_speech(tmp_path, fake_vad):
    p = tmp_path / "quiet.wav"
    sf.write(p, np.zeros(SR * 3), SR, subtype="PCM_16")
    assert cli.main(["speech", str(p), "--model", str(fake_vad), "-q"]) == 4
    assert not (tmp_path / "quiet_speech.wav").exists() and not (tmp_path / "quiet_removed.wav").exists()
