"""Keep only the speech (ADR-0011, v2 in ADR-0013): a level gate finds the sound, the VAD and the island rules
decide which of it is speech; the rest is faded to silence and the chain's diff is what was removed."""
import numpy as np

from .. import speech as SP
from ..errors import InputError
from .base import Fix, RepairModule, RepairResult, ScanResult

PARAMS = {
    "type": "object",
    "properties": {
        "sensitivity": {"type": "string", "enum": ["low", "mid", "high"], "default": "mid"},
        "pad_before_ms": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 50},
        "pad_after_ms": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 100},
        "min_gap_ms": {"type": "integer", "minimum": 0, "maximum": 2000, "default": 300},
    },
    "additionalProperties": False,
}


def settings_from_params(params):
    """Settings from the module's params (any subset; missing keys take the schema default)."""
    params = dict(params or {})
    props = PARAMS["properties"]
    unknown = sorted(set(params) - set(props))
    if unknown:
        raise InputError(f"unknown speech setting {unknown[0]!r}", code="bad_request")
    v = {k: params.get(k, p["default"]) for k, p in props.items()}
    for k, p in props.items():
        x = v[k]
        if p["type"] == "string":
            if x not in p["enum"]:
                raise InputError(f"speech setting {k} must be one of {', '.join(p['enum'])}", code="bad_request")
        elif isinstance(x, bool) or not isinstance(x, (int, float)) or x != int(x) \
                or not p["minimum"] <= x <= p["maximum"]:
            raise InputError(f"speech setting {k} must be a whole number from {p['minimum']} to {p['maximum']}",
                             code="bad_request")
    s = SP.Settings.for_sensitivity(v["sensitivity"], pad_before=v["pad_before_ms"] / 1000,
                                    pad_after=v["pad_after_ms"] / 1000, min_gap=v["min_gap_ms"] / 1000)
    return s, v


class SpeechModule(RepairModule):
    id = "speech"
    name = "module.speech"
    device = "cpu"
    default_order = 90  # after every repair: detect speech on the repaired audio
    params = PARAMS

    def scan(self, audio, sr):
        # running the model on every upload would cost about a minute an hour of audio; it runs on repair
        return ScanResult(self.id, False, 0, {})

    def repair(self, audio, sr, params, progress):
        s, used = settings_from_params(params)
        x = np.asarray(audio, dtype=np.float64)
        n = len(x)

        def say(msg):
            progress(message=msg)

        def prog(pct, msg):
            progress(pct=round(min(pct, 99.0) * 0.95, 1), message=msg)

        spans, analyses = SP.detect([SP.Track("", x, sr, None)], s, say=say, progress=prog)
        speech, _ = SP.split(x, sr, spans, s)
        # the spans split() kept, in samples; a removed gap plus the fades inside its neighbours is one fix
        f = max(int(round(s.fade * sr)), 1)
        kept = [(max(int(round(a * sr)), 0), min(int(round(b * sr)), n)) for a, b in spans]
        kept = [(a, b) for a, b in kept if b - a > 2 * f]
        fixes, labels, prev = [], [], 0
        for a, b in kept + [(n, n)]:
            if a > prev:
                fixes.append(Fix(prev - f if prev else 0, a + f if a < n else n, -1, "removed"))
                labels.append(SP.label_of((prev / sr, a / sr), analyses[0].islands))
            prev = b
        removed_s = sum(fx.end - fx.start for fx in fixes) / sr
        notes = analyses[0].notes()
        notes.update(n_speech_spans=len(kept), n_removed=len(fixes), removed_s=round(removed_s, 2), params=used,
                     labels=labels)
        return RepairResult(speech, fixes, notes)
