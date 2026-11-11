"""Module registry. Built-in modules register themselves at the bottom of this file."""
from .base import RepairModule  # noqa: F401

_REGISTRY = {}


def register(module):
    _REGISTRY[module.id] = module
    return module


def unregister(module_id):
    """Remove a module (test-only helper); unknown ids are ignored."""
    _REGISTRY.pop(module_id, None)


def get(module_id):
    return _REGISTRY[module_id]


def all_modules():
    return sorted(_REGISTRY.values(), key=lambda m: m.default_order)


def ordered(ids):
    return sorted((get(i) for i in ids), key=lambda m: m.default_order)


# built-in modules (each task below adds its line here)
from .glitch import GlitchModule  # noqa: E402

register(GlitchModule())

from .declip import DeclipModule  # noqa: E402

register(DeclipModule())

from .denoise import DenoiseModule  # noqa: E402

register(DenoiseModule())

from .speech import SpeechModule  # noqa: E402

register(SpeechModule())
