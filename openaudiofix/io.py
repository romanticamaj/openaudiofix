"""Decoding (ffmpeg / soundfile), export (v5 gain arithmetic, optional byte-level WAV patch), diffs, atomic .npy writes."""
import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass

import numpy as np
import soundfile as sf

from .algorithms import wavio
from .errors import InputError

MAX_DURATION_S = 4 * 3600
LOSSY_CODECS = {"mp3", "mp2", "aac", "opus", "vorbis", "wmav1", "wmav2", "ac3", "eac3", "amr_nb", "amr_wb", "dts"}
PATCHABLE_SUBTYPES = {"PCM_16", "PCM_24", "PCM_32"}


@dataclass
class AudioStream:
    index: int      # position among the file's audio streams (ffmpeg -map 0:a:<index>)
    codec: str
    sr: int
    channels: int


@dataclass
class Probe:
    format: str
    duration_s: float
    streams: list
    has_video: bool


@dataclass
class Source:
    audio: np.ndarray       # float64 (n, channels)
    sr: int
    subtype: object         # soundfile subtype for WAV input, else None
    wav_path: object        # original path when the input is a RIFF WAV (byte patching), else None
    lossy: bool
    codec: str


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()[-3:]
        raise InputError("ffmpeg could not read this file: " + " / ".join(tail),
                         hint="The file may be damaged or in an unsupported format.", code="decode_failed")
    info = json.loads(r.stdout or "{}")
    streams, has_video = [], False
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            streams.append(AudioStream(len(streams), s.get("codec_name", "?"), int(s.get("sample_rate", 0)),
                                       int(s.get("channels", 0))))
        elif s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic"):
            has_video = True
    if not streams:
        raise InputError("This file has no audio track.", code="no_audio")
    fmt = info.get("format", {})
    duration = float(fmt.get("duration") or 0.0)
    if duration <= 0:  # container without a duration: use the longest stream so the guard cannot be skipped
        durs = []
        for s in info.get("streams", []):
            try:
                durs.append(float(s.get("duration") or 0.0))
            except (TypeError, ValueError):
                pass
        duration = max(durs, default=0.0)
    if duration > MAX_DURATION_S:
        raise InputError(f"The file is {duration / 60:.0f} minutes long; the limit is {MAX_DURATION_S // 3600} hours.",
                         hint="Split the recording and repair the parts separately.", code="too_long")
    return Probe(fmt.get("format_name", "?"), duration, streams, has_video)


def decode(path, stream=0, probe_result=None):
    pr = probe_result or probe(path)
    if not 0 <= stream < len(pr.streams):
        raise InputError(f"Audio track {stream} does not exist.", code="decode_failed")
    st = pr.streams[stream]
    lossy = st.codec in LOSSY_CODECS
    try:
        info = sf.info(str(path))
    except Exception:
        info = None
    if info is not None and info.format in ("WAV", "WAVEX") and len(pr.streams) == 1:
        audio, sr = sf.read(str(path), dtype="float64", always_2d=True)
        return Source(audio, sr, info.subtype, str(path), False, st.codec)
    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, "a.f32")
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map", f"0:a:{stream}", "-vn",
                            "-ac", str(st.channels), "-c:a", "pcm_f32le", "-f", "f32le", raw],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise InputError("ffmpeg failed to decode the audio: " + (r.stderr or "").strip()[-300:], code="decode_failed")
        audio = np.fromfile(raw, dtype="<f4").reshape(-1, st.channels).astype(np.float64)
    return Source(audio, st.sr, None, None, lossy, st.codec)


def peak_gain_db(*arrays):
    peak = max((float(np.abs(a).max()) for a in arrays if a.size), default=0.0)
    return float(-20 * np.log10(peak)) if peak > 1.0 else 0.0


def export(final, sr, dst, source, float_out=False, reduce_gain=True):
    """Write the repaired audio. Gain is applied once, here, never earlier.

    reduce_gain=True (declip-style, v5 `_run_declip` arithmetic): gain_db = -20*log10(peak) when peak > 1,
    else 0; the whole signal is scaled by 10**(gain_db/20) and written with the source subtype.
    The byte-level WAV patch is used only when that gain is 0 dB.

    reduce_gain=False (glitch-only chains, v5 `_run_declick` behaviour): gain is always 0 dB. Eligible
    sources (RIFF WAV, integer PCM 16/24/32, fmt code 1) go through wavio.write_repaired_wav, which clips
    overshoot to the integer range; other sources are written with the same clip (ceiling 1 - 2**-23)
    and byte_exact=False.

    float_out=True writes 32-bit float at the repaired level (gain 0 dB, byte_exact=False).
    Returns dict(gain_db, subtype, byte_exact).
    """
    final = np.asarray(final, dtype=np.float64)
    if float_out:
        sf.write(str(dst), final[:, 0] if final.shape[1] == 1 else final, sr, subtype="FLOAT")
        return dict(gain_db=0.0, subtype="FLOAT", byte_exact=False)
    gain_db = peak_gain_db(final) if reduce_gain else 0.0
    subtype = source.subtype or "PCM_24"
    if gain_db == 0.0 and source.wav_path and subtype in PATCHABLE_SUBTYPES:
        fmt = wavio.read_fmt(source.wav_path)
        if fmt["fmt_code"] == 1:
            chans_new = [final[:, c] for c in range(final.shape[1])]
            chans_old = [source.audio[:, c] for c in range(source.audio.shape[1])]
            st = wavio.write_repaired_wav(source.wav_path, str(dst), chans_old, chans_new)
            return dict(gain_db=0.0, subtype=subtype, byte_exact=bool(st["byte_exact"]))
    if reduce_gain:
        out = final * 10 ** (gain_db / 20)
    else:
        out = np.clip(final, -1, 1 - 2 ** -23)
    sf.write(str(dst), out[:, 0] if out.shape[1] == 1 else out, sr, subtype=subtype)
    return dict(gain_db=round(gain_db, 2), subtype=subtype, byte_exact=False)


def write_diff(dst, a, b, sr):
    d = np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)
    pk = float(np.abs(d).max()) if d.size else 0.0
    if pk <= 0:
        return None
    g = 0.89 / pk
    out = d * g
    sf.write(str(dst), out[:, 0] if out.shape[1] == 1 else out, sr, subtype="PCM_24")
    return round(float(20 * np.log10(g)), 2)


def tmp_path_for(path):
    """A unique sibling temp name, so concurrent writers (e.g. a lost worker and the job's new owner)
    never write or delete each other's temp file."""
    return f"{path}.{uuid.uuid4().hex[:12]}.tmp"


def save_npy(path, arr):
    path = str(path)
    tmp = tmp_path_for(path)
    try:
        with open(tmp, "wb") as f:
            np.save(f, np.asarray(arr))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
