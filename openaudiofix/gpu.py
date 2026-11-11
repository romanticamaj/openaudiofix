"""Background repair's GPU stack, installed into this Python environment (ADR-0012).

    afix gpu status | install | test | remove

The packages are pinned in gpu-requirements.txt (torch/torchaudio/torchvision from the PyTorch CUDA 12.8 index,
plus ClearVoice's dependencies) and ClearVoice itself is installed without its dependencies (it pins an older
soundfile). They are only ever added: the core's pinned numpy/scipy/soundfile are passed to pip as constraints,
so installing the GPU stack never changes the numbers the CPU modules produce (v5 parity).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from .paths import default_models_dir

LOCK = Path(__file__).with_name("gpu-requirements.txt")
CLEARVOICE = "clearvoice==0.1.2"
PACKAGES = ["torch==2.7.1", "torchaudio==2.7.1", CLEARVOICE]  # what "installed" means (see packages_present)
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
CORE_PINNED = ("numpy", "scipy", "soundfile", "onnxruntime")
ESTIMATED_BYTES = 3_600_000_000   # measured: torch cu128 wheel ~3.2 GB + the rest; MossFormer2 weights ~0.2 GB
WEIGHTS_BYTES = 230_000_000
UNINSTALL = ("torch", "torchaudio", "torchvision", "clearvoice")
SIZE_UNITS = {"kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "bytes": 1, "B": 1}


class GpuUnavailable(Exception):
    """No usable NVIDIA GPU or driver; `code` is the UI's blocked-hint key."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def model_dir():
    """Where the MossFormer2 weights live: MODEL_DIR (the GPU image bakes them in), else AFIX_MODELS, else the
    default models folder, the one the speech model uses."""
    d = os.environ.get("MODEL_DIR") or os.environ.get("AFIX_MODELS")
    return Path(d) if d else default_models_dir()


def nvidia():
    """(name, memory, driver) of the first NVIDIA GPU, from nvidia-smi (it comes with the driver)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        raise GpuUnavailable("nvidia_missing", "nvidia-smi was not found: no NVIDIA driver is installed")
    try:
        r = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GpuUnavailable("nvidia_missing", f"nvidia-smi did not answer: {e}") from None
    lines = [s for s in r.stdout.splitlines() if s.strip()]
    if r.returncode != 0 or not lines:
        raise GpuUnavailable("nvidia_missing", (r.stderr or r.stdout).strip() or "nvidia-smi found no GPU")
    name, mem, drv = [s.strip() for s in lines[0].split(",")[:3]]
    return name, mem, drv


def installed_versions():
    """{package: version} of the GPU packages that are installed; missing ones are left out."""
    out = {}
    for spec in PACKAGES:
        name = spec.split("==")[0]
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return out


def packages_present():
    """True when every GPU package is installed at the pinned version (a +cu128 local label is fine)."""
    have = installed_versions()
    return all(have.get(s.split("==")[0], "").split("+")[0] == s.split("==")[1] for s in PACKAGES)


def install_disabled():
    """Docker images set AFIX_GPU_INSTALL=0: the GPU stack is part of the image (the gpu target) or deliberately
    absent (the core target); pip inside a container would be lost when it is recreated."""
    return os.environ.get("AFIX_GPU_INSTALL", "1") == "0"


def _constraints():
    """The core's own pins (numpy==1.26.4, ...) as a pip constraints file's lines."""
    pins = []
    for name in CORE_PINNED:
        try:
            pins.append(f"{name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            pass
    return pins


def pip_commands(constraints_path, python=None):
    """The locked packages under the core's pins, then ClearVoice on its own (--no-deps)."""
    pip = [python or sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
           "--progress-bar", "raw"]
    return [pip + ["--extra-index-url", TORCH_INDEX, "-c", str(constraints_path), "-r", str(LOCK)],
            pip + ["--no-deps", CLEARVOICE]]


_DOWNLOADING = re.compile(r"(Downloading|Using cached) \S+ \(([\d.]+) (kB|KB|MB|GB|bytes|B)\)")
_PROGRESS = re.compile(r"Progress (\d+) of (\d+)")


class PipProgress:
    """Bytes downloaded so far, from pip's output: "Downloading x.whl (2.6 GB)" opens a file and
    `--progress-bar raw` reports "Progress <done> of <total>" while it downloads."""

    def __init__(self, expected=ESTIMATED_BYTES):
        self.expected = expected
        self.finished = 0      # bytes of files completely downloaded
        self.current = 0       # bytes of the file being downloaded
        self.open_total = 0

    def feed(self, line):
        m = _DOWNLOADING.search(line)
        if m:
            self.finished += self.open_total
            self.current = 0
            size = int(float(m.group(2)) * SIZE_UNITS[m.group(3)])
            if m.group(1) == "Using cached":  # already on disk: counts as downloaded at once
                self.finished, self.open_total = self.finished + size, 0
            else:
                self.open_total = size
            return True
        m = _PROGRESS.search(line)
        if m:
            self.current, self.open_total = int(m.group(1)), int(m.group(2))
            return True
        return False

    @property
    def done_bytes(self):
        return self.finished + self.current

    @property
    def pct(self):
        return min(99.0, round(self.done_bytes / max(self.expected, self.done_bytes, 1) * 100, 1))


def install(report=lambda **kw: None, python=None):
    """pip install the GPU packages into this environment. report(pct=, done_bytes=, phase=) as it goes.
    Raises RuntimeError with pip's last lines when pip fails."""
    with tempfile.TemporaryDirectory() as td:
        cpath = Path(td) / "core-constraints.txt"
        cpath.write_text("\n".join(_constraints()) + "\n", encoding="utf-8")
        prog = PipProgress()
        for cmd in pip_commands(cpath, python):
            tail = []
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", bufsize=1)
            for line in p.stdout:
                line = line.rstrip()
                if prog.feed(line):
                    report(pct=prog.pct, done_bytes=prog.done_bytes, phase="packages")
                elif line.startswith("Installing collected packages"):
                    report(pct=99.0, done_bytes=prog.done_bytes, phase="installing")
                if line and not line.startswith("Progress "):
                    tail = (tail + [line])[-20:]
            if p.wait() != 0:
                raise RuntimeError("pip install failed:\n" + "\n".join(tail))
    report(pct=100.0, done_bytes=prog.done_bytes, phase="packages")


def _python_env():
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1", MODEL_DIR=str(model_dir()))
    return env


def download_weights(python=None):
    """Fetch the MossFormer2 checkpoint (in a child process: torch is imported there, not here)."""
    r = subprocess.run([python or sys.executable, "-m", "openaudiofix.modules.denoise", "--download"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=_python_env())
    if r.returncode != 0:
        raise RuntimeError("downloading the MossFormer2 weights failed:\n" + (r.stderr or r.stdout)[-2000:])


def selftest(python=None, timeout=900):
    """Run server.gpu_selftest in a child process; -> (its JSON result, exit code)."""
    try:
        r = subprocess.run([python or sys.executable, "-m", "server.gpu_selftest"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=_python_env(), timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "step": "inference", "error": f"the self-test took longer than {timeout} s"}, 1
    lines = [s for s in r.stdout.splitlines() if s.strip().startswith("{")]
    try:
        return json.loads(lines[-1]), r.returncode
    except (IndexError, ValueError):
        return {"ok": False, "step": "cuda", "error": (r.stderr or r.stdout or "no output")[-2000:]}, r.returncode or 1


def uninstall(python=None):
    """Remove the GPU packages (their small shared dependencies stay) and the MossFormer2 weights."""
    r = subprocess.run([python or sys.executable, "-m", "pip", "uninstall", "-y", *UNINSTALL],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("pip uninstall failed:\n" + (r.stderr or r.stdout)[-2000:])
    shutil.rmtree(model_dir() / "clearvoice", ignore_errors=True)
