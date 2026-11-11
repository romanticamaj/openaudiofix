"""WAV read/write: bit-level in-place patching (preserves chunks such as JUNK/iXML, overwriting only the samples that
actually changed), and JSON-safe type conversion."""
import os, struct, mmap, shutil
import numpy as np
import soundfile as sf


def jsonable(o):
    """numpy scalar types (bool_, intNN, floatNN) are not types the json module recognizes (bool_ cannot be serialized
    at all; intNN/floatNN get by on some platforms and not on others); convert recursively to native Python types before
    serializing, to stop the problem in one place."""
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    return o


def chunks_of(path):
    out = []
    with open(path, "rb") as f:
        f.seek(12)
        while True:
            h = f.read(8)
            if len(h) < 8:
                break
            k, s = struct.unpack("<4sI", h)
            out.append((k, f.tell(), s))
            f.seek(s + (s % 2), 1)
    return out


def read_fmt(path):
    for k, off, size in chunks_of(path):
        if k == b"fmt ":
            with open(path, "rb") as f:
                f.seek(off)
                raw = f.read(size)
            fmt_code, channels, sr, _, _, bits = struct.unpack("<HHIIHH", raw[:16])
            return dict(fmt_code=fmt_code, channels=channels, sr=sr, bits=bits)
    raise ValueError("no fmt chunk")


def write_repaired_wav(src_path, dst_path, X_orig, X_new):
    """Prefer byte-level in-place patching (preserves JUNK / iXML / other chunks, overwriting only the samples that
    actually changed); supports only integer PCM 16/24/32-bit (the whole detection/repair flow of this project has only
    been validated on these too). Other formats (float WAV etc.) fall back to a standard full rewrite, and the result
    reports byte_exact=False."""
    fmt = read_fmt(src_path)
    ch = fmt["channels"]
    if fmt["fmt_code"] != 1 or fmt["bits"] not in (16, 24, 32):
        y = np.stack(X_new, axis=1) if ch > 1 else X_new[0]
        sf.write(dst_path, np.clip(y, -1, 1 - 2 ** -23), fmt["sr"], subtype=sf.info(src_path).subtype)
        return dict(byte_exact=False, samples_altered=None, altered_pct=None)

    bits = fmt["bits"]
    bps = bits // 8
    shift = 32 - bits
    full_scale = 2 ** (bits - 1) - 1

    orig_i32 = sf.read(src_path, dtype="int32")[0]
    if orig_i32.ndim == 1:
        orig_i32 = orig_i32[:, None]
    orig_i = orig_i32 >> shift  # original integer sample values (one column per channel)

    new_f = np.stack(X_new, axis=1) if ch > 1 else X_new[0][:, None]
    new_f = np.clip(new_f, -1, 1 - 2 ** -(bits - 1))
    new_i = np.rint(new_f * (2 ** (bits - 1))).astype(np.int64)
    new_i = np.clip(new_i, -(2 ** (bits - 1)), full_scale)

    off, size = next((o, s) for k, o, s in chunks_of(src_path) if k == b"data")
    shutil.copy2(src_path, dst_path)

    diff_frame = np.flatnonzero(np.any(orig_i != new_i, axis=1))
    with open(dst_path, "r+b") as f:
        mm = mmap.mmap(f.fileno(), 0)
        for r in (np.split(diff_frame, np.flatnonzero(np.diff(diff_frame) > 1) + 1) if len(diff_frame) else []):
            a, b = int(r[0]), int(r[-1]) + 1
            raw = new_i[a:b]  # (n, ch) int64
            byte_cols = [((raw >> (8 * k)) & 255).astype(np.uint8) for k in range(bps)]
            packed = np.stack(byte_cols, axis=-1)  # (n, ch, bps)
            mm[off + ch * bps * a: off + ch * bps * b] = packed.tobytes()
        mm.flush(); mm.close()

    n = orig_i.shape[0]
    return dict(byte_exact=True, samples_altered=int(len(diff_frame)),
                altered_pct=round(len(diff_frame) / max(n, 1) * 100, 4))
