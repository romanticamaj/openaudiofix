"""Speech extraction for podcasts: split a recording into the speech and everything that was taken out.

v2 (ADR-0013): a level gate decides where there is sound, Silero VAD only helps decide what that sound is.

    mono mix -> 100 Hz high-pass (only to measure; the output is never filtered)
      -> 10 ms frames: level (dBFS), spectral centroid; 16 kHz autocorrelation -> f0 and periodicity
      -> Silero VAD v5 (32 ms frames, MIT, ONNX, CPU; downloaded on first use), mapped onto the 10 ms grid
      -> noise floor = lowest prominent peak of the lower half of the level histogram; gate relative to it
      -> hysteresis gate on the 30 ms-smoothed level -> islands of sound
      -> each island classified: speech (VAD says so, or voiced: character voices, laughs, singing),
         thud (impulsive, low, unvoiced), click, noise, music (long, VAD silent), short
      -> speech islands get a margin before and after; pauses shorter than min_gap are kept

Each input gives two files of its own length: the speech (everything else silent) and the removed parts
(the speech silent). Their fades are complementary, so the two played together are the original: in a DAW the
second track shows exactly what was taken out, and any of it (a laugh, say) can be pulled back.
Several tracks of one session (one mic per speaker) are split together: speech on any track keeps that span on
all of them. Each track gets its own noise floor.
"""
from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass, field
from math import gcd
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, resample_poly, sosfilt

from .errors import InputError, ModuleUnavailable
from .paths import default_models_dir

MODEL_URL = "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx"
MODEL_SHA256 = "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"
MODEL_FILE = "silero_vad_v5.1.2.onnx"
VAD_SR = 16000
CHUNK = 512          # samples per VAD frame at 16 kHz (32 ms)
CONTEXT = 64         # samples of the previous frame the v5 model expects in front of each chunk
HOP_S = CHUNK / VAD_SR
FRAME_S = 0.01       # analysis frame of the level gate

# open / close thresholds over the noise floor for the three sensitivities (ADR-0013)
SENSITIVITY = {"high": (14.0, 6.0), "mid": (18.0, 10.0), "low": (22.0, 14.0)}
# island classes; everything but speech goes to the removed track (fix kind "removed")
KINDS = ("speech", "voiced", "thud", "click", "noise", "music", "short")


@dataclass
class Settings:
    # level gate: where is there sound
    open_db: float = 18.0       # an island opens this far above the noise floor ...
    close_db: float = 10.0      # ... and closes when the level falls below this (hysteresis)
    hp_hz: float = 100.0        # high-pass before measuring the level, so hum barely lifts the floor
    smooth: float = 0.03        # level smoothing; a 5 ms spike does not open the gate
    calibrate: bool = True      # lower open_db when the speech is quieter than expected (ADR-0013 §2.5)
    # island classification: is that sound speech
    threshold: float = 0.5      # VAD probability that makes an island speech
    sure: float = 0.85          # islands shorter than min_on stay only if the VAD is this sure
    min_on: float = 0.10        # shorter islands are fragments
    island_min: float = 0.20    # an island the VAD rejects is still speech if this long ...
    voiced_min: float = 0.30    # ... and at least this fraction of it is voiced (character voices, laughs)
    thud_crest_db: float = 20.0  # thud: peak minus median level above this ...
    thud_centroid: float = 300.0  # ... centroid below this (Hz) ...
    thud_voiced: float = 0.10   # ... and almost unvoiced
    click_len: float = 0.08     # click: shorter than this ...
    click_centroid: float = 2500.0  # ... and brighter than this (Hz)
    active_db: float = 20.0     # the part of an island within this of its loud end is what is judged, not the
                                # quiet residue between sounds, which after denoising can fill most of an island
    music: bool = True          # long, unbroken islands the VAD never hears are music (jingles, beds)
    music_len: float = 5.0
    music_vad: float = 0.10     # mean VAD probability below this
    music_active: float = 0.80  # music does not stop: at least this much of the island is active. Voices pause
    # VAD runs (reported as the model's view; they no longer set the boundaries)
    release: float = 0.35
    min_speech: float = 0.25
    # margins and output
    min_gap: float = 0.3        # pauses shorter than this are kept
    pad_before: float = 0.05    # margin kept before each speech island
    pad_after: float = 0.10     # margin kept after each speech island
    fade: float = 0.020         # fade at every boundary between speech and removed

    @classmethod
    def for_sensitivity(cls, sensitivity="mid", **kw):
        if sensitivity not in SENSITIVITY:
            raise InputError(f"unknown sensitivity {sensitivity!r}; use low, mid or high", code="bad_request")
        o, c = SENSITIVITY[sensitivity]
        return cls(open_db=o, close_db=c, **kw)


# ---------------------------------------------------------------- model
def model_dir():
    return Path(os.environ.get("AFIX_MODELS") or default_models_dir())


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ensure_model(path=None, download=True, say=print):
    """Path of the VAD model; downloads and verifies it on first use unless download=False."""
    if path:
        p = Path(path)
        if not p.is_file():
            raise ModuleUnavailable(f"The speech model {p} does not exist.", hint="Check the --model path.")
        return p
    p = model_dir() / MODEL_FILE
    if p.is_file() and _sha256(p) == MODEL_SHA256:
        return p
    if not download:
        raise ModuleUnavailable("The speech model is not downloaded yet.",
                                hint=f"Run once with network access, or put {MODEL_FILE} into {p.parent}.")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    say(f"downloading the speech model (Silero VAD v5, MIT, 2.3 MB) to {p} ...")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as r, open(tmp, "wb") as fh:
            fh.write(r.read())
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ModuleUnavailable(f"Could not download the speech model: {e}",
                                hint=f"Check the network, or download {MODEL_URL} to {p} by hand.")
    if _sha256(tmp) != MODEL_SHA256:
        tmp.unlink(missing_ok=True)
        raise ModuleUnavailable("The downloaded speech model does not match its checksum.", hint="Try again later.")
    os.replace(tmp, p)
    return p


class SileroVAD:
    def __init__(self, path):
        try:
            import onnxruntime as ort
        except ImportError:
            raise ModuleUnavailable("Speech extraction needs onnxruntime.", hint="pip install onnxruntime")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])

    def probs(self, x16, progress=None):
        """Speech probability for every 32 ms frame of a 16 kHz mono signal."""
        n = len(x16) // CHUNK
        out = np.zeros(n, np.float32)
        state = np.zeros((2, 1, 128), np.float32)
        ctx = np.zeros((1, CONTEXT), np.float32)
        sr = np.array(VAD_SR, dtype=np.int64)
        x = np.asarray(x16, dtype=np.float32)
        step = max(n // 100, 1)
        for i in range(n):
            inp = np.concatenate([ctx, x[None, i * CHUNK:(i + 1) * CHUNK]], axis=1)
            p, state = self.sess.run(None, {"input": inp, "state": state, "sr": sr})
            out[i] = p[0, 0]
            ctx = inp[:, -CONTEXT:]
            if progress and i % step == 0:
                progress(i / n * 100)
        return out


def mono(audio):
    x = np.asarray(audio, dtype=np.float64)
    return x.mean(axis=1) if x.ndim == 2 else x


def to_vad_rate(audio, sr):
    """Mono mix at 16 kHz for the detector (the output keeps the source's own rate and channels)."""
    x = mono(audio)
    g = gcd(int(sr), VAD_SR)
    return resample_poly(x, VAD_SR // g, int(sr) // g) if sr != VAD_SR else x


def speech_runs(probs, s: Settings, hop=HOP_S):
    """Hysteresis on the VAD probabilities -> [(start_s, end_s)]: where the model alone hears speech."""
    runs, on, start = [], False, 0
    for i, p in enumerate(probs):
        if not on and p >= s.threshold:
            on, start = True, i
        elif on and p < s.release:
            runs.append((start, i))
            on = False
    if on:
        runs.append((start, len(probs)))
    return [(a * hop, b * hop) for a, b in runs
            if (b - a) * hop >= s.min_speech or float(np.max(probs[a:b])) >= s.sure]


# ---------------------------------------------------------------- frame features
@dataclass
class Frames:
    """Per-10 ms features of one track. Frame i covers samples [i*hop, (i+1)*hop)."""
    sr: int
    hop: int                   # samples per frame
    n_samples: int
    level: np.ndarray          # dBFS of the high-passed mono mix
    centroid: np.ndarray = None  # Hz, of the unfiltered mix (20 ms Hann window)
    f0: np.ndarray = None      # Hz, autocorrelation peak at 16 kHz (40 ms window)
    per: np.ndarray = None     # periodicity: normalised autocorrelation at that peak
    vad: np.ndarray = None     # Silero probability on the 10 ms grid

    @property
    def n(self):
        return len(self.level)

    def t(self, i):
        """Seconds at the start of frame i."""
        return i * self.hop / self.sr


def frame_hop(sr):
    return max(int(round(sr * FRAME_S)), 1)


def frame_level(x, sr, hp_hz=100.0):
    """dBFS per 10 ms frame of a mono signal, after a 4th-order Butterworth high-pass (0 = none)."""
    hop = frame_hop(sr)
    y = np.asarray(x, dtype=np.float64)
    if hp_hz and hp_hz < sr / 2:
        y = sosfilt(butter(4, hp_hz, "highpass", fs=sr, output="sos"), y)
    n = len(y) // hop
    fr = y[: n * hop].reshape(n, hop)
    return 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-12)


def frame_centroid(x, sr, n):
    """Spectral centroid per 10 ms frame (20 ms Hann window starting at the frame)."""
    hop = frame_hop(sr)
    win = 2 * hop
    x = np.asarray(x, dtype=np.float64)
    out = np.zeros(n, np.float32)
    nfr = min(max((len(x) - win) // hop + 1, 0), n)
    if nfr <= 0:
        return out
    w = np.hanning(win)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    B = max(1, 4_000_000 // win)
    for s in range(0, nfr, B):
        e = min(s + B, nfr)
        idx = np.arange(win)[None, :] + hop * np.arange(s, e)[:, None]
        S = np.abs(np.fft.rfft(x[idx] * w, axis=1)) ** 2 + 1e-20
        out[s:e] = (S * freqs).sum(axis=1) / S.sum(axis=1)
    return out


def frame_periodicity(x16, n):
    """(f0, periodicity) per 10 ms frame from a 16 kHz mono signal: normalised autocorrelation peak, 60-1000 Hz."""
    W, H = 640, 160
    lag_min, lag_max = 16, 267
    f0 = np.zeros(n, np.float32)
    per = np.zeros(n, np.float32)
    x16 = np.asarray(x16, dtype=np.float64)
    nfr = min(max((len(x16) - W) // H + 1, 0), n)
    B = 4000
    for s in range(0, nfr, B):
        e = min(s + B, nfr)
        idx = np.arange(W)[None, :] + H * np.arange(s, e)[:, None]
        seg = x16[idx]
        seg = seg - seg.mean(axis=1, keepdims=True)
        F = np.fft.rfft(seg, 2 * W, axis=1)
        ac = np.fft.irfft(F * np.conj(F), axis=1)[:, :W]
        ac = ac / (ac[:, :1] + 1e-20)
        k = lag_min + np.argmax(ac[:, lag_min:lag_max], axis=1)
        per[s:e] = ac[np.arange(e - s), k]
        f0[s:e] = VAD_SR / k
    return f0, per


def vad_on_grid(vad32, n, hop, sr):
    """VAD probabilities (32 ms frames) looked up for every 10 ms frame."""
    if len(vad32) == 0:
        return np.zeros(n, np.float32)
    t = np.arange(n) * hop / sr
    return np.asarray(vad32, np.float32)[np.minimum((t / HOP_S).astype(int), len(vad32) - 1)]


# ---------------------------------------------------------------- decisions
def noise_floor(level, prominence=0.25):
    """The room's level: the lowest prominent peak of the level histogram (1 dB bins) in its lower half.

    In a file with clear pauses that is simply the mode of the lower half. On a track with a lot of quieter
    sound over the room (another speaker's bleed on a mic of a multi-mic session, which can fill most of the
    lower half) the room still shows as a smaller peak below it; the lowest peak at least `prominence` of the
    tallest one is taken. Digital silence (below -110 dBFS) is ignored; a file of nothing else gives -110."""
    lv = level[level > -110]
    if not len(lv):
        return -110.0
    h, edges = np.histogram(lv, bins=np.arange(-110, 1, 1.0))
    lo = edges[:-1] < np.percentile(lv, 50)
    if not lo.any():
        lo[: int(np.argmax(h)) + 1] = True
    hs = np.convolve(h, np.ones(3) / 3, mode="same")
    top = hs[lo].max()
    last = len(hs) - 1
    for i in np.flatnonzero(lo):
        if hs[i] >= prominence * top and hs[i] >= hs[max(i - 1, 0)] and hs[i] >= hs[min(i + 1, last)]:
            j = max(i - 1, 0) + int(np.argmax(h[max(i - 1, 0):i + 2]))     # the raw mode next to the smoothed peak
            return float(edges[j] + 0.5)
    return float(edges[:-1][lo][np.argmax(h[lo])] + 0.5)


def hysteresis(level, open_at, close_at):
    """True from where the level reaches open_at until it falls below close_at."""
    on = np.zeros(len(level), bool)
    st = False
    for i, v in enumerate(level):
        if not st and v >= open_at:
            st = True
        elif st and v < close_at:
            st = False
        on[i] = st
    return on


def spans_of(mask):
    """[(a, b)] frame index runs where mask is True."""
    m = np.r_[0, np.asarray(mask).astype(np.int8), 0]
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def classify(fr: Frames, a, b, s: Settings):
    """Class of the island of frames [a, b): one of KINDS."""
    hop_s = fr.hop / fr.sr
    dur = (b - a) * hop_s
    vad = fr.vad[a:b]
    vmax = float(vad.max())
    level = fr.level[a:b]
    active = level > np.percentile(level, 95) - s.active_db
    voiced = float(((fr.per[a:b] > 0.75) & (fr.f0[a:b] > 120) & (fr.f0[a:b] < 900))[active].mean())
    crest = float(level.max() - np.median(level))
    cmed = float(np.median(fr.centroid[a:b]))
    if (s.music and dur > s.music_len and float(vad.mean()) < s.music_vad and vmax < s.threshold
            and float(active.mean()) >= s.music_active):
        return "music"
    if vmax < s.threshold:
        if voiced < s.thud_voiced and crest > s.thud_crest_db and cmed < s.thud_centroid:
            return "thud"
        if dur < s.click_len and cmed > s.click_centroid:
            return "click"
        if dur < s.island_min or voiced < s.voiced_min:
            return "noise" if dur >= s.min_on else "short"
        return "voiced"          # the VAD missed it, but it is a voice: character voices, laughs, singing
    if dur < s.min_on and vmax < s.sure:
        return "short"
    return "speech"


def keep_mask(n, speech_islands, s: Settings, hop_s=FRAME_S):
    """Frames to keep: speech islands plus their margins, with pauses shorter than min_gap filled in
    (pauses that touch the start or end of the file are not pauses and stay removed)."""
    keep = np.zeros(n, bool)
    pb, pa = int(round(s.pad_before / hop_s)), int(round(s.pad_after / hop_s))
    for a, b in speech_islands:
        keep[max(0, a - pb):min(n, b + pa)] = True
    for a, b in spans_of(~keep):
        if (b - a) * hop_s < s.min_gap and a > 0 and b < n:
            keep[a:b] = True
    return keep


@dataclass
class TrackAnalysis:
    spans: list                 # [(start_s, end_s)] kept on this track
    islands: list               # [(start_s, end_s, kind)]
    floor_db: float
    speech_floor_db: float | None
    open_db: float              # actually used, over the floor (after calibration)
    close_db: float
    calibrated: bool
    counts: dict = field(default_factory=dict)
    speech_s: float = 0.0       # length of the speech islands, before margins

    def notes(self):
        return dict(floor_db=round(self.floor_db, 1),
                    speech_floor_db=None if self.speech_floor_db is None else round(self.speech_floor_db, 1),
                    open_db=round(self.open_db, 1), close_db=round(self.close_db, 1), calibrated=self.calibrated,
                    islands=dict(self.counts), speech_s=round(self.speech_s, 2))


def features(audio, sr, s: Settings, vad=None, progress=None):
    """All frame features of one track. vad: a SileroVAD-like object (probs(x16, progress))."""
    say = progress or (lambda pct, msg: None)
    x = mono(audio)
    hop = frame_hop(sr)
    say(0.0, "estimating noise floor")
    fr = Frames(int(sr), hop, len(x), frame_level(x, sr, s.hp_hz))
    say(5.0, "detecting speech")
    x16 = to_vad_rate(x, sr)
    vad32 = vad.probs(x16, lambda p: say(5.0 + 0.85 * p, "detecting speech")) if vad is not None else np.zeros(0)
    fr.vad = vad_on_grid(vad32, fr.n, hop, sr)
    say(90.0, "classifying")
    fr.centroid = frame_centroid(x, sr, fr.n)
    fr.f0, fr.per = frame_periodicity(x16, fr.n)
    return fr


def decide(fr: Frames, s: Settings):
    """Frame features -> TrackAnalysis (gate, islands, kept spans)."""
    hop_s = fr.hop / fr.sr
    floor = noise_floor(fr.level)
    sp = fr.level[fr.vad >= s.threshold]
    speech_floor = float(np.percentile(sp, 5)) if len(sp) else None
    open_db, close_db, calibrated = s.open_db, s.close_db, False
    if s.calibrate and speech_floor is not None and floor + open_db > speech_floor + 3.0:
        open_db = min(open_db, max(12.0, speech_floor - floor))     # lowered, never raised
        close_db = min(close_db, open_db - 2.0)
        calibrated = open_db < s.open_db
    k = max(int(round(s.smooth / hop_s)), 1)
    L = uniform_filter1d(fr.level, k) if k > 1 else fr.level
    on = hysteresis(L, floor + open_db, floor + close_db)
    counts = {kd: 0 for kd in KINDS}
    islands, speech = [], []
    for a, b in spans_of(on):
        kd = classify(fr, a, b, s)
        counts[kd] += 1
        islands.append((a, b, kd))
        if kd in ("speech", "voiced"):
            speech.append((a, b))
    keep = keep_mask(fr.n, speech, s, hop_s)
    dur = fr.n_samples / fr.sr

    def sec(i):
        return dur if i >= fr.n else fr.t(i)
    return TrackAnalysis(spans=[(sec(a), sec(b)) for a, b in spans_of(keep)],
                         islands=[(sec(a), sec(b), kd) for a, b, kd in islands],
                         floor_db=floor, speech_floor_db=speech_floor, open_db=open_db, close_db=close_db,
                         calibrated=calibrated, counts=counts,
                         speech_s=sum(b - a for a, b in speech) * hop_s)


def analyze(audio, sr, s: Settings, vad=None, progress=None):
    return decide(features(audio, sr, s, vad, progress), s)


def union(span_lists):
    """Spans of several tracks merged into one list (speech on any track keeps the span everywhere)."""
    allspans = sorted(sp for spans in span_lists for sp in spans)
    out = []
    for a, b in allspans:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def removed_spans(spans, duration):
    """The complement of the kept spans: [(start_s, end_s)]."""
    out, prev = [], 0.0
    for a, b in list(spans) + [(duration, duration)]:
        if a - prev > 1e-3:
            out.append((prev, a))
        prev = b
    return out


def label_of(gap, islands):
    """Label for a removed gap: the class of the loudest thing in it by length, or 'removed' when only quiet."""
    a, b = gap
    by = {}
    for ia, ib, kd in islands:
        if kd in ("speech", "voiced"):
            continue
        o = min(b, ib) - max(a, ia)
        if o > 0:
            by[kd] = by.get(kd, 0.0) + o
    return max(by, key=by.get) if by else "removed"


# ---------------------------------------------------------------- rendering
def split(audio, sr, spans, s: Settings):
    """-> (speech, removed), both shaped like the input. Inside a span the speech is the input sample for sample
    and the removed track is silent; outside it is the other way round; at each boundary a raised-cosine fade
    (inside the span) hands over from one to the other, and the two always add up to the input."""
    x = np.asarray(audio, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    n = len(x)
    f = max(int(round(s.fade * sr)), 1)
    up = np.sin((np.arange(f) + 0.5) / f * np.pi / 2) ** 2
    g = np.zeros(n)
    for a, b in spans:
        a, b = max(int(round(a * sr)), 0), min(int(round(b * sr)), n)
        if b - a <= 2 * f:
            continue
        g[a:b] = 1.0
        if a > 0:
            g[a:a + f] = up
        if b < n:
            g[b - f:b] = up[::-1]
    speech = x * g[:, None]
    removed = x - speech
    return speech, removed


# ---------------------------------------------------------------- whole pipeline
@dataclass
class Track:
    path: str
    audio: np.ndarray
    sr: int
    subtype: object


def detect(tracks, s: Settings, model=None, download=True, say=print, progress=None, vad=None):
    """Analyse every track -> (spans kept on all tracks, [TrackAnalysis per track]).
    progress(pct, message) gets the overall percentage."""
    vad = vad or SileroVAD(ensure_model(model, download, say))
    analyses = []
    for k, t in enumerate(tracks):
        def _p(pct, msg, _k=k):
            if progress:
                progress((_k + pct / 100) / len(tracks) * 100, msg)
        analyses.append(analyze(t.audio, t.sr, s, vad, _p))
    spans = union([an.spans for an in analyses])
    if len(tracks) > 1:      # pauses between two speakers' turns follow the same rule as within a track
        dur = len(tracks[0].audio) / tracks[0].sr
        merged = []
        for a, b in spans:
            if merged and a - merged[-1][1] < s.min_gap:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        spans = [sp for sp in merged if sp[1] > sp[0] and sp[0] < dur]
    return spans, analyses


def check_session(tracks):
    sr, n = tracks[0].sr, len(tracks[0].audio)
    for t in tracks[1:]:
        if t.sr != sr or abs(len(t.audio) - n) > sr // 100:
            raise InputError(f"{t.path}: tracks of one session must share sample rate and length.",
                             hint="Give each unrelated file its own run.", code="bad_request")

