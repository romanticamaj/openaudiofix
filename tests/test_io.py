import subprocess

import numpy as np
import pytest
import soundfile as sf

from openaudiofix import io as aio, synth
from openaudiofix.errors import InputError


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def test_probe_and_decode_wav_keeps_samples_and_subtype(tmp_path):
    x = synth.speech_like(seconds=1.0, channels=2)
    p = tmp_path / "a.wav"
    sf.write(p, x, 48000, subtype="PCM_24")
    pr = aio.probe(p)
    assert pr.streams[0].channels == 2 and not pr.has_video
    s = aio.decode(p)
    assert s.subtype == "PCM_24" and s.wav_path == str(p) and not s.lossy
    assert np.array_equal(s.audio, sf.read(p, dtype="float64", always_2d=True)[0])


@pytest.mark.parametrize("ext,lossy", [("mp3", True), ("flac", False), ("m4a", True)])
def test_decode_compressed_formats(tmp_path, ext, lossy):
    p = tmp_path / f"a.{ext}"
    ff("-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=44100", "-ac", "2", str(p))
    s = aio.decode(p)
    assert s.sr == 44100 and s.audio.shape[1] == 2 and s.lossy is lossy and s.wav_path is None
    assert abs(len(s.audio) / 44100 - 2.0) < 0.1


def test_decode_video_takes_audio_track(tmp_path):
    p = tmp_path / "v.mp4"
    ff("-f", "lavfi", "-i", "testsrc=duration=2:size=64x64:rate=10", "-f", "lavfi", "-i", "sine=duration=2",
       "-shortest", "-c:v", "libx264", "-c:a", "aac", str(p))
    pr = aio.probe(p)
    assert pr.has_video and len(pr.streams) == 1
    assert aio.decode(p).audio.shape[1] == 1


def test_probe_lists_multiple_audio_tracks(tmp_path):
    p = tmp_path / "m.mkv"
    ff("-f", "lavfi", "-i", "sine=f=300:duration=1", "-f", "lavfi", "-i", "sine=f=600:duration=1",
       "-map", "0", "-map", "1", "-c:a", "flac", str(p))
    pr = aio.probe(p)
    assert [s.index for s in pr.streams] == [0, 1]
    a0, a1 = aio.decode(p, stream=0).audio, aio.decode(p, stream=1).audio
    assert not np.array_equal(a0, a1)


def test_probe_rejects_garbage_and_video_without_audio(tmp_path):
    g = tmp_path / "g.wav"
    g.write_bytes(b"not audio at all" * 100)
    with pytest.raises(InputError) as e:
        aio.probe(g)
    assert e.value.code == "decode_failed"
    v = tmp_path / "v.mp4"
    ff("-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10", "-c:v", "libx264", str(v))
    with pytest.raises(InputError) as e:
        aio.probe(v)
    assert e.value.code == "no_audio"


def test_probe_rejects_longer_than_four_hours(tmp_path, monkeypatch):
    p = tmp_path / "a.wav"
    sf.write(p, np.zeros((4800, 1)), 48000)
    monkeypatch.setattr(aio, "MAX_DURATION_S", 0.05)
    with pytest.raises(InputError) as e:
        aio.probe(p)
    assert e.value.code == "too_long"


def test_export_byte_patches_integer_wav_when_no_gain(tmp_path):
    x = synth.speech_like(seconds=1.0, channels=2)
    src = tmp_path / "s.wav"
    sf.write(src, x, 48000, subtype="PCM_24")
    s = aio.decode(src)
    y = s.audio.copy(); y[1000:1010] *= 0.5
    info = aio.export(y, 48000, tmp_path / "o.wav", s)
    assert info["byte_exact"] and info["gain_db"] == 0.0
    a, b = (tmp_path / "o.wav").read_bytes(), src.read_bytes()
    assert len(a) == len(b) and a[:2000] == b[:2000]
    off = b.index(b"data") + 8
    lo, hi = off + 1000 * 6, off + 1010 * 6  # 2 ch x 3 bytes per frame
    assert a[lo:hi] != b[lo:hi]
    assert a[:lo] == b[:lo] and a[hi:] == b[hi:]


def test_export_reduces_gain_when_over_full_scale(tmp_path):
    x = synth.speech_like(seconds=1.0)
    src = tmp_path / "s.wav"
    sf.write(src, x, 48000, subtype="PCM_16")
    s = aio.decode(src)
    y = s.audio * 6.0
    info = aio.export(y, 48000, tmp_path / "o.wav", s)
    out, _ = sf.read(tmp_path / "o.wav", always_2d=True)
    assert info["gain_db"] < 0 and not info["byte_exact"] and info["subtype"] == "PCM_16"
    assert np.abs(out).max() <= 1.0


def test_export_float_keeps_level(tmp_path):
    s = aio.Source(np.zeros((10, 1)), 48000, "PCM_24", None, False, "pcm_s24le")
    y = np.full((10, 1), 1.5)
    info = aio.export(y, 48000, tmp_path / "o.wav", s, float_out=True)
    out, _ = sf.read(tmp_path / "o.wav", always_2d=True)
    assert info == dict(gain_db=0.0, subtype="FLOAT", byte_exact=False) and out.max() == pytest.approx(1.5)


@pytest.mark.parametrize("subtype", ["PCM_U8", "FLOAT"])
def test_export_non_patchable_wav_falls_back(tmp_path, subtype):
    x = synth.speech_like(seconds=0.5)
    src = tmp_path / "s.wav"
    sf.write(src, x, 48000, subtype=subtype)
    s = aio.decode(src)
    info = aio.export(s.audio * 0.5, 48000, tmp_path / "o.wav", s)
    assert info["byte_exact"] is False and sf.info(tmp_path / "o.wav").subtype == subtype


def test_write_diff_matches_v5_semantics(tmp_path):
    a = np.zeros((100, 1)); b = a.copy(); b[10] = 0.01
    g = aio.write_diff(tmp_path / "d.wav", a, b, 48000)
    d, _ = sf.read(tmp_path / "d.wav")
    assert g == pytest.approx(20 * np.log10(0.89 / 0.01), abs=0.01) and abs(d).max() == pytest.approx(0.89, abs=1e-6)
    assert aio.write_diff(tmp_path / "z.wav", a, a.copy(), 48000) is None and not (tmp_path / "z.wav").exists()


def test_too_long_is_rejected_at_probe_without_writing_files(tmp_path, monkeypatch):
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    p = src_dir / "a.wav"
    sf.write(p, np.zeros((4800, 1)), 48000)
    monkeypatch.setattr(aio, "MAX_DURATION_S", 0.05)
    monkeypatch.setattr(aio.subprocess, "run", _spy(aio.subprocess.run))
    before = sorted(tmp_path.rglob("*"))
    with pytest.raises(InputError) as e:
        aio.probe(p)
    assert e.value.code == "too_long"
    assert sorted(tmp_path.rglob("*")) == before
    assert _CALLS and all(c[0] == "ffprobe" for c in _CALLS)  # never reached ffmpeg decode


_CALLS = []


def _spy(real):
    _CALLS.clear()

    def run(cmd, *a, **k):
        _CALLS.append(cmd)
        return real(cmd, *a, **k)
    return run


@pytest.mark.parametrize("subtype", ["PCM_U8", "FLOAT"])
def test_export_non_patchable_wav_over_full_scale_does_not_crash(tmp_path, subtype):
    x = synth.speech_like(seconds=0.5, channels=2)
    src = tmp_path / "s.wav"
    sf.write(src, x, 48000, subtype=subtype)
    s = aio.decode(src)
    info = aio.export(s.audio * 6.0, 48000, tmp_path / "o.wav", s)
    out, _ = sf.read(tmp_path / "o.wav", always_2d=True)
    assert info["byte_exact"] is False and info["gain_db"] < 0 and np.abs(out).max() <= 1.0 + 1e-6
    assert out.shape == s.audio.shape


def _overshoot_case(tmp_path):
    x = synth.speech_like(seconds=0.5)
    src = tmp_path / "s.wav"
    sf.write(src, x, 48000, subtype="PCM_16")
    s = aio.decode(src)
    y = s.audio.copy()
    y[500, 0] = 1.2  # a sample above full scale after repair
    return s, y


def test_export_reduce_gain_false_patches_and_clips_overshoot(tmp_path):
    s, y = _overshoot_case(tmp_path)
    info = aio.export(y, 48000, tmp_path / "o.wav", s, reduce_gain=False)
    out, _ = sf.read(tmp_path / "o.wav", dtype="int32", always_2d=True)
    assert info == dict(gain_db=0.0, subtype="PCM_16", byte_exact=True)
    assert out[500, 0] >> 16 == 32767


def test_export_reduce_gain_true_lowers_gain_on_overshoot(tmp_path):
    s, y = _overshoot_case(tmp_path)
    info = aio.export(y, 48000, tmp_path / "o.wav", s, reduce_gain=True)
    assert info["gain_db"] < 0 and info["byte_exact"] is False


def test_export_reduce_gain_false_fallback_clips(tmp_path):
    src = tmp_path / "s.wav"
    sf.write(src, np.zeros((100, 1)), 48000, subtype="FLOAT")
    s = aio.decode(src)
    y = np.zeros((100, 1)); y[5, 0] = 1.5
    info = aio.export(y, 48000, tmp_path / "o.wav", s, reduce_gain=False)
    out, _ = sf.read(tmp_path / "o.wav", always_2d=True)
    assert info["gain_db"] == 0.0 and info["byte_exact"] is False and out.max() == pytest.approx(1 - 2 ** -23)


def test_probe_falls_back_to_stream_duration(tmp_path, monkeypatch):
    p = tmp_path / "a.wav"
    p.write_bytes(b"x")
    fake = {"format": {"format_name": "x"}, "streams": [
        {"codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "48000", "channels": 1, "duration": "99999"}]}

    class R:
        returncode, stdout, stderr = 0, __import__("json").dumps(fake), ""
    monkeypatch.setattr(aio.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(InputError) as e:
        aio.probe(p)
    assert e.value.code == "too_long"


def test_save_npy_round_trip_leaves_no_tmp(tmp_path):
    a = np.random.default_rng(0).normal(size=(50, 2))
    aio.save_npy(tmp_path / "a.npy", a)
    assert np.array_equal(np.load(tmp_path / "a.npy"), a)
    assert [q.name for q in tmp_path.iterdir()] == ["a.npy"]


def test_save_npy_removes_tmp_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(aio.np, "save", lambda f, a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        aio.save_npy(tmp_path / "a.npy", np.zeros(3))
    assert list(tmp_path.iterdir()) == []


def test_null_test_against_the_original_float_cancels_and_same_depth_does_not(tmp_path):
    """Peaks rebuilt over full scale: float keeps every other sample, a lowered file changes all of them."""
    sr = 48000
    rng = np.random.default_rng(0)
    src = np.round(rng.uniform(-.7, .7, (sr, 2)) * 2 ** 23) / 2 ** 23
    w = tmp_path / "s.wav"
    sf.write(w, src, sr, subtype="PCM_24")
    a, _ = sf.read(w, dtype="float64", always_2d=True)
    fin = a.copy()
    fin[1000:1100] *= 2.2
    S = aio.Source(a, sr, "PCM_24", str(w), False, "pcm")
    outside = np.ones(len(a), bool)
    outside[1000:1100] = False
    aio.export(fin, sr, tmp_path / "f.wav", S, float_out=True, reduce_gain=True)
    y, _ = sf.read(tmp_path / "f.wav", dtype="float64", always_2d=True)
    assert np.array_equal(y[outside], a[outside]) and np.abs(y).max() > 1
    info = aio.export(fin, sr, tmp_path / "p.wav", S, float_out=False, reduce_gain=True)
    y, _ = sf.read(tmp_path / "p.wav", dtype="float64", always_2d=True)
    assert info["gain_db"] < 0 and np.abs(y[outside] - a[outside]).max() > 0.01
