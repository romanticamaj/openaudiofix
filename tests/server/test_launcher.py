import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest


def _port_free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _kill_tree(p):
    if sys.platform == "win32":  # terminate() skips the launcher's handlers and would orphan its children
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        p.terminate()
    p.wait(15)


@pytest.mark.slow
def test_launcher_serves_health_and_frees_port(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, AFIX_DATA=str(tmp_path), AFIX_PORT=str(port), PYTHONUTF8="1")
    p = subprocess.Popen([sys.executable, "-m", "server.launcher"], env=env)
    try:
        for _ in range(60):
            try:
                body = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1).read()
                break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.fail("server did not come up")
        assert b'"ok":true' in body.replace(b" ", b"")
    finally:
        _kill_tree(p)
    for _ in range(20):
        if _port_free(port):
            break
        time.sleep(0.5)
    assert _port_free(port)


@pytest.mark.slow
def test_launcher_stops_its_children_on_graceful_signal(tmp_path):
    import signal
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, AFIX_DATA=str(tmp_path), AFIX_PORT=str(port), PYTHONUTF8="1")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    p = subprocess.Popen([sys.executable, "-m", "server.launcher"], env=env, creationflags=flags)
    try:
        for _ in range(60):
            if not _port_free(port):
                break
            time.sleep(0.5)
        else:
            pytest.fail("server did not come up")
        p.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGTERM)
        p.wait(30)
        for _ in range(20):
            if _port_free(port):
                break
            time.sleep(0.5)
        assert _port_free(port)
    finally:
        if p.poll() is None:
            _kill_tree(p)


@pytest.mark.slow
def test_restart_from_settings_comes_back_on_the_folder_saved_for_the_next_start(tmp_path):
    import json
    from server import locations
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    first, second = tmp_path / "first", tmp_path / "second"
    env = {k: v for k, v in os.environ.items() if k not in ("AFIX_DATA", "AFIX_MODELS", "AFIX_LOCATION_SOURCES")}
    env.update(AFIX_CONFIG_DIR=str(tmp_path / "cfg"), AFIX_PORT=str(port), PYTHONUTF8="1")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "location.json").write_text(json.dumps({"data": str(first)}), encoding="utf-8")

    def data_dir():
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/settings", timeout=2) as r:
            return json.load(r)["data_dir"]

    def wait_for(folder):
        for _ in range(120):
            try:
                if data_dir() == str(folder.resolve()):
                    return
            except Exception:
                pass
            time.sleep(0.5)
        pytest.fail(f"the server did not come up on {folder}")

    p = subprocess.Popen([sys.executable, "-m", "server.launcher"], env=env)
    try:
        wait_for(first)
        (tmp_path / "cfg" / "location.json").write_text(json.dumps({"data": str(second)}), encoding="utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/restart", method="POST", data=b"")
        assert urllib.request.urlopen(req, timeout=5).status == 200
        wait_for(second)
    finally:
        _kill_tree(p)
