"""The models page: what each repair module downloads, where it is, how big, and what can be done with it.

The speech model (Silero VAD) can be downloaded and deleted here. The background-repair model and its GPU
packages are installed and removed together through the GPU module, so this page only reports them.
"""
import importlib.util
from pathlib import Path

from openaudiofix import gpu as gpu_core, speech as speech_core

from .migrate import tree_size


def _dir_size(p):
    p = Path(p)
    return tree_size(p) if p.is_dir() else (p.stat().st_size if p.is_file() else 0)


def speech_model():
    p = speech_core.model_dir() / speech_core.MODEL_FILE
    if not p.is_file():
        state = "missing"
    else:
        state = "ready" if speech_core._sha256(p) == speech_core.MODEL_SHA256 else "corrupt"
    return {
        "id": "silero", "name": "Silero VAD v5", "used_by": ["speech"], "license": "MIT",
        "state": state, "size_bytes": p.stat().st_size if p.is_file() else 0, "expected_bytes": 2_300_000,
        "path": str(p), "managed_by": None, "device": "cpu",
    }


def mossformer_model():
    d = gpu_core.model_dir() / "clearvoice" / "checkpoints" / "MossFormer2_SE_48K"
    ok = d.is_dir() and any(d.glob("*.pt"))
    return {
        "id": "mossformer2", "name": "MossFormer2 SE 48 kHz", "used_by": ["denoise"], "license": "Apache-2.0",
        "state": "ready" if ok else "missing", "size_bytes": _dir_size(d), "expected_bytes": 200_000_000,
        "path": str(d), "managed_by": "denoise", "device": "gpu",
    }


_pkg_cache = {}


def gpu_packages():
    have = gpu_core.installed_versions()
    key = tuple(sorted(have.items()))
    if key in _pkg_cache:  # walking torch's thousands of files takes a while; versions decide the size
        return _pkg_cache[key]
    size = 0
    for name in ("torch", "torchaudio", "clearvoice"):
        spec = importlib.util.find_spec(name) if name in have else None
        if spec and spec.submodule_search_locations:
            size += sum(_dir_size(x) for x in spec.submodule_search_locations)
    out = {
        "id": "gpu_packages", "name": "PyTorch (CUDA 12.8) + ClearVoice", "used_by": ["denoise"],
        "license": "BSD-3 / Apache-2.0", "state": "ready" if gpu_core.packages_present() else "missing",
        "size_bytes": size, "expected_bytes": gpu_core.ESTIMATED_BYTES - 200_000_000,
        "versions": have, "path": None, "managed_by": "denoise", "device": "gpu",
    }
    _pkg_cache.clear()
    _pkg_cache[key] = out
    return out


def all_models():
    return [speech_model(), mossformer_model(), gpu_packages()]
