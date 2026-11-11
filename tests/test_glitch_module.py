import numpy as np

from openaudiofix import modules, synth
from openaudiofix.algorithms import declick as DK


def _glitchy(seconds=6.0, n=10, gap=64, seed=5, channels=2, sr=48000):
    pos = synth.dropout_positions(sr, seconds, n)
    x = synth.speech_like(sr=sr, seconds=seconds + n * gap / sr + 0.01, seed=seed, channels=channels)
    return synth.inject_dropouts(x, pos, gap)[: int(seconds * sr)], pos


def _audible(y, pos, floor_db=-45.0):
    """Injected positions with real signal around them. The synthetic source has pauses where a dropout is
    inaudible, so no detector can (or should) find those."""
    out = []
    for p in pos:
        w = y[p - 480:p + 480]
        if 20 * np.log10(np.sqrt((w ** 2).mean()) + 1e-12) > floor_db:
            out.append(p)
    return out


def test_glitch_scan_finds_injected_seams():
    y, pos = _glitchy()
    r = modules.get("glitch").scan(y, 48000)
    assert r.detected and r.count >= 0.4 * len(_audible(y, pos))  # seed 5: 4 found / 8 audible


def test_glitch_repair_equals_algorithm_and_only_touches_fix_regions():
    y, pos = _glitchy()
    calls = []
    res = modules.get("glitch").repair(y, 48000, {}, lambda pct=None, message=None: calls.append(message))
    ref = DK.repair_file([y[:, c].copy() for c in range(2)], 48000, None)[0]
    assert np.array_equal(res.audio, np.stack(ref, axis=1))
    # the mask comes from the RETURNED fixes, not from the diff: outside every fix the audio is untouched
    assert res.fixes
    mask = np.ones(len(y), bool)
    for f in res.fixes:
        mask[f.start:f.end] = False
    assert np.array_equal(res.audio[mask], y[mask])
    assert res.audio.shape == y.shape and not np.array_equal(res.audio, y)
    # seed 5 measured: 4 of 8 audible injected positions are inside a fix (v5 misses the rest; the other
    # 2 positions are in synthetic pauses). Margin: require >= 40% (3.2 -> 4).
    audible = _audible(y, pos)
    covered = [p for p in audible if any(f.start <= p < f.end for f in res.fixes)]
    assert len(covered) >= 0.4 * len(audible)
    # precision: every returned fix contains an injected dropout (no false-positive repairs)
    assert all(any(f.start <= p < f.end for p in pos) for f in res.fixes) and calls


def test_glitch_clean_input_is_untouched():
    x = synth.speech_like(seconds=3.0, seed=9)
    res = modules.get("glitch").repair(x, 48000, {}, lambda **k: None)
    assert res.fixes == [] and np.array_equal(res.audio, x)
