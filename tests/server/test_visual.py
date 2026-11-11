import struct
import zlib

import numpy as np

from server import visual as V


def test_peaks_minmax_matches_bruteforce():
    rng = np.random.default_rng(0)
    a = rng.standard_normal((10000, 2))
    get = lambda i0, i1: a[i0:i1]
    p = V.peaks_minmax(get, 1000, 9000, 16)
    mono = a.mean(axis=1)
    edges = np.linspace(1000, 9000, 17).astype(int)
    assert p[0] == round(float(mono[edges[0]:edges[1]].max()), 4)
    assert p[1] == round(float(mono[edges[0]:edges[1]].min()), 4)
    assert len(p) == 32


def test_peaks_minmax_caps_buckets_to_samples():
    a = np.ones((10, 1))
    assert len(V.peaks_minmax(lambda i0, i1: a[i0:i1], 0, 5, 100)) == 10


def test_spectrogram_png_is_valid_and_sine_lands_in_one_band():
    sr = 48000
    t = np.arange(sr * 2) / sr
    a = (0.5 * np.sin(2 * np.pi * 1000 * t))[:, None]
    png = V.spectrogram_png(lambda i0, i1: a[i0:i1], len(a), sr, 0, len(a), 64, 128)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (64, 128)
    idat = png[png.index(b"IDAT") + 4:]
    raw = zlib.decompressobj().decompress(idat)
    rows = np.frombuffer(raw, np.uint8).reshape(128, 1 + 64 * 3)[:, 1:].reshape(128, 64, 3)
    brightness = rows.astype(int).sum(axis=2).mean(axis=1)
    assert abs(int(brightness.argmax()) - 63) <= 3
    assert brightness.max() > 3 * np.median(brightness)


def test_resolve_builds_diff_getters(tmp_path):
    from server import storage as S
    st = S.Storage(tmp_path)
    pid = st.create()
    src = np.zeros((100, 1)); s0 = src.copy(); s0[10] = 0.5
    st.save_source(pid, src)
    st.save_stage(pid, 0, "glitch", s0, [], {})
    project = {"id": pid, "modules": ["glitch"], "result": {"preview_gain_db": 0.0, "diffs": [{"id": "d_chain", "gain_db": 6.0}]}}
    get, n, gain = V.resolve(st, project, "d_chain")
    assert n == 100 and get(0, 100)[10, 0] == -0.5 and gain == 10 ** (6 / 20)
    get, n, gain = V.resolve(st, project, "s0")
    assert get(0, 100)[10, 0] == 0.5 and gain == 1.0


def test_peaks_minmax_clamps_negative_start():
    a = np.arange(10, dtype=float)[:, None]
    p = V.peaks_minmax(lambda i0, i1: a[i0:i1], -5, 10, 2)
    assert len(p) == 4 and p[1] == 0.0


def test_resolve_rejects_noncanonical_ids(tmp_path):
    import pytest
    from server import storage as S
    st = S.Storage(tmp_path)
    pid = st.create()
    st.save_source(pid, np.zeros((10, 1)))
    st.save_stage(pid, 0, "glitch", np.zeros((10, 1)), [], {})
    project = {"id": pid, "modules": ["glitch"], "result": {}}
    for bad in ("s²", "s00", "s01", "s1", "d1", "s-1", "d", "s", "x0", "s0 ", "٣"):
        with pytest.raises(KeyError):
            V.resolve(st, project, bad)
    assert V.resolve(st, project, "s0")[1] == 10 and V.resolve(st, project, "d0")[1] == 10
