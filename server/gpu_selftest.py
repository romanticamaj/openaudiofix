"""GPU module self-test, run inside the gpu-worker container:  python -m server.gpu_selftest
(the GPU worker also calls run() in-process once at startup and records the result itself).

1. a real CUDA matmul compared with the CPU result
2. MossFormer2 on a 5 s synthetic clip: same length, finite, no burst, not silenced; reports speed.
"""
import json
import sys
import time

import numpy as np


def evaluate_output(x, y):
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if len(y) != len(x):
        return [f"length changed: {len(x)} -> {len(y)}"]
    if not np.isfinite(y).all():
        return ["output contains NaN or Inf"]
    problems = []
    if np.abs(y).max() > 1.0:
        problems.append(f"peak {np.abs(y).max():.2f} exceeds full scale (burst)")
    rx, ry = np.sqrt(np.mean(x ** 2)), np.sqrt(np.mean(y ** 2))
    if rx > 0 and ry < rx * 10 ** (-30 / 20):
        problems.append(f"output is {20 * np.log10(ry / rx + 1e-12):.1f} dB below the input (silence)")
    return problems


def run():
    """Run the self-test in this process; returns {ok, step, gpu, speed_x, load_s, error} (never raises)."""
    out = {"ok": False, "step": "cuda", "gpu": "", "speed_x": 0.0, "load_s": 0.0, "error": ""}
    try:
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("torch.cuda.is_available() is False")
        g = torch.Generator().manual_seed(0)
        a, b = torch.randn(512, 512, generator=g), torch.randn(512, 512, generator=g)
        err = float(((a.cuda() @ b.cuda()).cpu() - a @ b).abs().max())
        if err > 1e-2:
            raise RuntimeError(f"GPU matmul differs from CPU by {err}")
        out["gpu"] = torch.cuda.get_device_name(0)
        out["step"] = "inference"
        from openaudiofix import synth
        from openaudiofix.modules import get
        rng = np.random.default_rng(1)
        x = synth.speech_like(seconds=5.0, seed=21)
        x = x + 10 ** (-35 / 20) * rng.standard_normal(x.shape)
        module = get("denoise")
        t0 = time.perf_counter()
        module.backend()  # cold model load (checkpoint, CUDA context)
        out["load_s"] = round(time.perf_counter() - t0, 2)
        module.repair(x[: 48000], 48000, {}, lambda **k: None)  # 1 s warm-up (CUDA/cuDNN autotune)
        t0 = time.perf_counter()
        res = module.repair(x, 48000, {}, lambda **k: None)
        dt = time.perf_counter() - t0
        problems = evaluate_output(x[:, 0], res.audio[:, 0])
        if problems:
            raise RuntimeError("; ".join(problems))
        out.update(ok=True, step="done", speed_x=round(5.0 / dt, 1))
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def main():
    out = run()
    print(json.dumps(out), flush=True)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
