import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from openaudiofix import gpu as G

ROOT = Path(__file__).resolve().parents[1]


def _lock():
    return [ln.split("==")[0].lower() for ln in G.LOCK.read_text(encoding="utf-8").splitlines()
            if ln and not ln.startswith("#")]


def test_lock_adds_to_the_core_and_never_repins_it():
    names = _lock()
    assert {"torch", "torchaudio", "torchvision", "librosa"} <= set(names)
    # the core's numeric pins and ClearVoice (installed --no-deps) are not in the lock
    assert not {"numpy", "scipy", "soundfile", "onnxruntime", "clearvoice"} & set(names)
    torch = next(ln for ln in G.LOCK.read_text(encoding="utf-8").splitlines() if ln.startswith("torch=="))
    assert torch == "torch==2.7.1+cu128"


def test_pip_commands_constrain_to_the_core_pins_and_install_clearvoice_alone(tmp_path):
    first, second = G.pip_commands(tmp_path / "c.txt", python="py")
    assert first[:4] == ["py", "-m", "pip", "install"] and "--progress-bar" in first
    assert first[first.index("-c") + 1] == str(tmp_path / "c.txt") and first[first.index("-r") + 1] == str(G.LOCK)
    assert G.TORCH_INDEX in first
    assert second[-2:] == ["--no-deps", "clearvoice==0.1.2"]
    pins = G._constraints()
    assert "numpy==1.26.4" in pins and "scipy==1.15.3" in pins and "soundfile==0.13.1" in pins


def test_pip_progress_follows_downloads():
    p = G.PipProgress(expected=4_000_000_000)
    assert p.feed("  Downloading torch-2.7.1%2Bcu128-cp310-cp310-win_amd64.whl (3273.1 MB)")
    assert p.feed("Progress 1636550000 of 3273100000")
    assert p.done_bytes == 1636550000 and p.pct == pytest.approx(40.9, abs=0.1)
    assert p.feed("Progress 3273100000 of 3273100000")
    assert p.feed("  Downloading librosa-0.10.2.post1-py3-none-any.whl (260 kB)")
    assert p.done_bytes == 3273100000                      # torch counted once, librosa not started
    assert not p.feed("Collecting einops==0.8.2")
    p.feed("Progress 260000 of 260000")
    assert p.done_bytes == 3273100000 + 260000 and p.pct <= 99.0
    assert p.feed("  Using cached scenedetect-0.6.6-py3-none-any.whl (1.2 MB)")
    assert p.done_bytes == 3273100000 + 260000 + 1200000


def test_install_reports_pip_failure(monkeypatch):
    class Proc:
        def __init__(self, cmd, **kw):
            self.stdout = iter(["Collecting torch==2.7.1+cu128\n", "ERROR: No matching distribution found\n"])

        def wait(self):
            return 1
    monkeypatch.setattr(G.subprocess, "Popen", Proc)
    with pytest.raises(RuntimeError, match="No matching distribution"):
        G.install()


def test_install_runs_both_steps_and_reports(monkeypatch):
    cmds, seen = [], []

    class Proc:
        def __init__(self, cmd, **kw):
            cmds.append(cmd)
            self.stdout = iter(["  Downloading torch.whl (3.0 GB)\n", "Progress 1500000000 of 3000000000\n",
                                "Installing collected packages: torch\n"])

        def wait(self):
            return 0
    monkeypatch.setattr(G.subprocess, "Popen", Proc)
    G.install(lambda **kw: seen.append(kw))
    assert len(cmds) == 2 and cmds[1][-1] == "clearvoice==0.1.2"
    assert seen[-1]["pct"] == 100.0 and any(s["phase"] == "installing" for s in seen)


def test_packages_present_needs_the_pinned_versions(monkeypatch):
    versions = {"torch": "2.7.1+cu128", "torchaudio": "2.7.1+cu128", "clearvoice": "0.1.2"}
    monkeypatch.setattr(G, "installed_versions", lambda: dict(versions))
    assert G.packages_present()
    versions["torch"] = "2.6.0"
    assert not G.packages_present()
    del versions["torch"]
    assert not G.packages_present()


def test_no_nvidia_smi_is_reported(monkeypatch):
    monkeypatch.setattr(G.shutil, "which", lambda name: None)
    with pytest.raises(G.GpuUnavailable) as e:
        G.nvidia()
    assert e.value.code == "nvidia_missing"


def test_install_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AFIX_GPU_INSTALL", "0")
    assert G.install_disabled()
    monkeypatch.delenv("AFIX_GPU_INSTALL")
    assert not G.install_disabled()


def test_docker_targets_install_the_same_way():
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "AS gpu" in df and "AS core" in df and "gpu.install()" in df and "AFIX_GPU_INSTALL=0" in df
    assert df.rstrip().endswith("FROM base AS core")  # the default build target is the CPU image
    gpu = (ROOT / "docker-compose.gpu.yml").read_text(encoding="utf-8")
    assert "target: gpu" in gpu and "driver: nvidia" in gpu
    assert "docker.sock" not in (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_worker_exits_when_its_parent_closes_the_pipe(tmp_path):
    env = dict(os.environ, AFIX_DATA=str(tmp_path), PYTHONUTF8="1")
    p = subprocess.Popen([sys.executable, "-m", "server.worker", "--device", "cpu", "--exit-with-parent"],
                         stdin=subprocess.PIPE, cwd=ROOT, env=env)
    try:
        time.sleep(3)
        assert p.poll() is None          # running while the pipe is open
        p.stdin.close()
        assert p.wait(30) == 0
    finally:
        if p.poll() is None:
            p.kill()
