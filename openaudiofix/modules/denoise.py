"""Background repair: MossFormer2_SE_48K speech enhancement (ClearVoice), GPU only."""
import os
import sys
import tempfile

import numpy as np

from ..algorithms.align import align_to, resample
from ..errors import GpuOutOfMemory, ModuleUnavailable
from .base import Fix, RepairModule, RepairResult, ScanResult, channels_of
from ..paths import default_models_dir

MODEL = "MossFormer2_SE_48K"
MODEL_SR = 48000
OOM_RETRIES = 2


def noise_floor_dbfs(audio, sr, frame_s=0.05):
    mono = np.asarray(audio, dtype=np.float64).mean(axis=1)
    n = max(1, int(sr * frame_s))
    k = len(mono) // n
    if k == 0:
        return -120.0
    rms = np.sqrt(np.mean(mono[: k * n].reshape(k, n) ** 2, axis=1))
    return float(20 * np.log10(np.percentile(rms, 10) + 1e-12))


def _is_oom(e):
    return "out of memory" in str(e).lower()


def _empty_cuda_cache():
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass


class MossFormerBackend:
    """The real backend. torch/ClearVoice are imported lazily so the core package never depends on them."""

    def __init__(self):
        try:
            from clearvoice import ClearVoice
        except Exception as e:
            raise ModuleUnavailable("Background repair needs the GPU module.",
                                    hint="Install it from the Modules panel.") from e
        md = os.environ.get("MODEL_DIR") or os.environ.get("AFIX_MODELS") or str(default_models_dir())
        self.work = os.path.join(md, "clearvoice")
        os.makedirs(self.work, exist_ok=True)
        cwd = os.getcwd()
        os.chdir(self.work)  # ClearVoice downloads checkpoints/ into the working directory
        try:
            self.cv = ClearVoice(task="speech_enhancement", model_names=[MODEL])
        finally:
            os.chdir(cwd)
        self.args = self.cv.models[0].args
        self.default_window = float(self.args.decode_window)

    def __call__(self, x48, decode_window=None):
        import soundfile as sf
        self.args.decode_window = float(decode_window or self.default_window)
        cwd = os.getcwd()
        os.chdir(self.work)
        try:
            with tempfile.TemporaryDirectory() as td:
                p = os.path.join(td, "in.wav")
                sf.write(p, np.asarray(x48, dtype=np.float32), MODEL_SR, subtype="FLOAT")
                y = self.cv(input_path=p, online_write=False)
        finally:
            os.chdir(cwd)
        return np.asarray(y, dtype=np.float64).reshape(-1)[: len(x48)]


class DenoiseModule(RepairModule):
    id = "denoise"
    name = "module.denoise"
    device = "gpu"
    default_order = 30
    whole_file = True
    backend_factory = MossFormerBackend
    _backend = None

    def backend(self):
        if self._backend is None:
            self._backend = self.backend_factory()
        return self._backend

    def scan(self, audio, sr):
        floor = noise_floor_dbfs(audio, sr)
        return ScanResult(self.id, floor > -60.0, 0, {"noise_floor_dbfs": round(floor, 1)})

    def _enhance(self, backend, xin):
        """Run the backend; on GPU OOM halve the decode window and retry (at most OOM_RETRIES times)."""
        window = None
        for attempt in range(OOM_RETRIES + 1):
            try:
                return backend(xin, window), window
            except Exception as e:
                if not _is_oom(e):
                    raise
                if attempt == OOM_RETRIES:
                    raise GpuOutOfMemory("The GPU ran out of memory.",
                                         hint="Close other programs that use the GPU, then re-run the GPU self-test.") from e
                window = (window or backend.default_window) / 2
                _empty_cuda_cache()

    def repair(self, audio, sr, params, progress):
        backend = self.backend()
        chans = channels_of(audio)
        out, lags, window = [], [], None
        for ci, ch in enumerate(chans):
            progress(pct=round(ci / len(chans) * 100, 1), message=f"channel {ci + 1}/{len(chans)}")
            x32 = ch.astype(np.float32)
            y, window = self._enhance(backend, resample(x32, sr, MODEL_SR))
            y = resample(np.asarray(y, dtype=np.float32), MODEL_SR, sr)
            y, lag = align_to(x32, y)
            out.append(np.asarray(y, dtype=np.float64))
            lags.append(int(lag))
        progress(pct=100.0, message="done")
        res = np.stack(out, axis=1)
        notes = dict(noise_floor_before=round(noise_floor_dbfs(audio, sr), 1),
                     noise_floor_after=round(noise_floor_dbfs(res, sr), 1), lag=lags, decode_window=window)
        return RepairResult(res, [Fix(0, len(res), -1, "denoise")], notes)


if __name__ == "__main__":
    if "--download" in sys.argv:
        MossFormerBackend()
        print("MossFormer2 weights ready")
