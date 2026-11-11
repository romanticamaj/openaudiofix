import numpy as np

from openaudiofix import synth


def test_speech_like_is_deterministic_and_shaped():
    a = synth.speech_like(seconds=2.0, seed=3, channels=2)
    b = synth.speech_like(seconds=2.0, seed=3, channels=2)
    assert a.shape == (96000, 2) and a.dtype == np.float64
    assert np.array_equal(a, b)
    assert 10 ** (-12.5 / 20) < np.abs(a).max() <= 10 ** (-11.5 / 20)


def test_inject_clipping_creates_flat_tops_at_full_scale():
    x = synth.speech_like(seconds=2.0, seed=1)
    y = synth.inject_clipping(x, drive_db=8.0)
    top = 1.0 - 2.0 ** -23
    assert y.shape == x.shape
    assert (y >= top).sum() > 100 and (y <= -1.0).sum() > 100
    assert np.abs(y).max() <= 1.0


def test_inject_dropouts_removes_gap_samples_at_each_position():
    sr, gap = 48000, 64
    pos = synth.dropout_positions(sr, 2.0, n=3)
    assert all(p % 64 == 0 for p in pos)
    x = synth.speech_like(sr=sr, seconds=2.0 + 3 * gap / sr + 0.01, seed=2)
    y = synth.inject_dropouts(x, pos, gap)
    assert len(y) == len(x) - 3 * gap
    # before the first dropout nothing changes; right after it the source has jumped by `gap`
    assert np.array_equal(y[: pos[0]], x[: pos[0]])
    assert np.array_equal(y[pos[0]: pos[0] + 10], x[pos[0] + gap: pos[0] + gap + 10])
