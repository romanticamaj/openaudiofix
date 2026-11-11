"""Browser previews: WebM/Opus, streamed into ffmpeg so hour-long files never sit in memory twice."""
import os
import subprocess
import threading

import uuid

import numpy as np

CHUNK = 1 << 18
M3DB = 10 ** (-3 / 20)
# ITU-R BS.775 style 5.1 downmix, channel order L R C LFE Ls Rs (WAV / ffmpeg "5.1"): C and LFE go to both
# sides at -3 dB, each surround to its own side. Rows are normalised so the downmix never exceeds full scale.
DOWNMIX_51 = np.array([[1.0, 0.0], [0.0, 1.0], [M3DB, M3DB], [M3DB, M3DB], [1.0, 0.0], [0.0, 1.0]])
DOWNMIX_51 = DOWNMIX_51 / DOWNMIX_51.sum(axis=0)


def _to_stereo(seg):
    if seg.shape[1] <= 2:
        return seg
    if seg.shape[1] == 6:
        return seg @ DOWNMIX_51
    return np.stack([seg[:, 0::2].mean(axis=1), seg[:, 1::2].mean(axis=1)], axis=1)


def encode_preview(get, n, sr, channels, dst, gain=1.0, bitrate="128k"):
    """Encode get(i0, i1) -> (m, channels) float samples to `dst` (WebM/Opus). More than two channels are
    downmixed to stereo (5.1: ITU downmix; other layouts: even channels left, odd channels right). Written to a
    unique `<dst>.<id>.tmp.webm`, then replaced."""
    out_ch = min(channels, 2)
    tmp = f"{dst}.{uuid.uuid4().hex[:12]}.tmp.webm"
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", str(out_ch),
                          "-i", "pipe:0", "-c:a", "libopus", "-b:a", bitrate, "-f", "webm", tmp],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    err = []
    reader = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
    reader.start()
    try:
        try:
            for a in range(0, n, CHUNK):
                seg = _to_stereo(np.asarray(get(a, min(n, a + CHUNK)), dtype=np.float64)) * gain
                p.stdin.write(seg.astype("<f4").tobytes())
            p.stdin.close()
        except BrokenPipeError:
            pass  # ffmpeg exited early; its return code and stderr say why
        rc = p.wait()
        reader.join()
        if rc != 0:
            msg = b"".join(err).decode("utf-8", "replace").strip()
            raise RuntimeError(f"ffmpeg failed to encode the preview: {msg[-300:]}")
        os.replace(tmp, dst)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        try:
            os.remove(tmp)
        except OSError:
            pass
