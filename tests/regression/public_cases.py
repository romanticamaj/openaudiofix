import sys

import numpy
import scipy
import soundfile as sf

from openaudiofix import synth


def _declip():
    return synth.inject_clipping(synth.speech_like(seconds=8.0, seed=11), 8.0), 48000


def _glitch():
    sr, gap, n, seconds = 48000, 64, 32, 10.0
    pos = synth.dropout_positions(sr, seconds, n)
    x = synth.speech_like(sr=sr, seconds=seconds + n * gap / sr + 0.01, seed=12, channels=2)
    return synth.quantise24(synth.inject_dropouts(x, pos, gap)[: int(seconds * sr)]), sr


CASES = {
    "declip": dict(build=_declip, modules=["declip"], v5_mode="declip"),
    "glitch": dict(build=_glitch, modules=["glitch"], v5_mode="declick"),
}


def write_case(name, path):
    audio, sr = CASES[name]["build"]()
    sf.write(str(path), audio, sr, subtype="PCM_24")
    return path


def platform_tag():
    return f"{sys.platform}-numpy{numpy.__version__}-scipy{scipy.__version__}"
