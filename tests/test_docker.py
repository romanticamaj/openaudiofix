import json
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.docker, pytest.mark.slow]

ROOT = Path(__file__).resolve().parents[1]
REAL_VOLUME = "openaudiofix-data"


def _docker_ok():
    """True only if the docker CLI exists and the daemon answers within a few seconds (never hangs)."""
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=8).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not _docker_ok(), reason="Docker is not running or does not answer")
def test_core_image_serves_ui_and_api(tmp_path):
    suffix = uuid.uuid4().hex[:8]
    project = f"afix-test-{suffix}"
    volume = f"afix-test-data-{suffix}"
    image = f"afix-test-core:{suffix}"
    port = _free_port()
    assert volume != REAL_VOLUME
    override = tmp_path / "override.yml"
    # unique volume, image tag and host port: the user's real openaudiofix-data volume is never touched
    override.write_text(
        "services:\n"
        "  core:\n"
        f"    image: {image}\n"
        f"    ports: !override [\"127.0.0.1:{port}:8765\"]\n"
        "volumes:\n"
        "  data:\n"
        f"    name: {volume}\n",
        encoding="utf-8",
    )
    compose = ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.yml"), "-f", str(override)]
    try:
        subprocess.run(compose + ["build", "core"], check=True, cwd=ROOT, timeout=1800)
        subprocess.run(compose + ["up", "-d", "core"], check=True, cwd=ROOT, timeout=120)
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                h = json.loads(urllib.request.urlopen(f"{base}/api/health", timeout=2).read())
                break
            except Exception:
                time.sleep(1)
        else:
            pytest.fail("core did not come up")
        assert h["ok"] is True
        page = urllib.request.urlopen(f"{base}/", timeout=5).read().decode()
        assert "<title>OpenAudioFix</title>" in page
        mods = json.loads(urllib.request.urlopen(f"{base}/api/modules", timeout=5).read())
        assert {m["id"] for m in mods["modules"]} >= {"glitch", "declip", "denoise"}
    finally:
        # -v removes only the uniquely named test volume declared in the override file
        subprocess.run(compose + ["down", "-v"], check=False, cwd=ROOT, timeout=120)
        subprocess.run(["docker", "rmi", image], check=False, capture_output=True, timeout=60)
