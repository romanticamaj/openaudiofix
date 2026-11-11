"""Start the API server and the CPU worker together:  python -m server.launcher

The web server and the worker are children of this process. They are terminated whenever the launcher
exits (normal exit, SIGINT/SIGTERM, CTRL_BREAK on Windows). A hard kill of the launcher itself (for example
`Popen.terminate()` on Windows) cannot be intercepted; kill the whole process tree in that case.
"""
import atexit
import os
import signal
import subprocess
import sys
import time

from server import locations

RESTART_CODE = 75  # server.app exits with this when Settings asks for a restart

_children = []


def _spawn(cmd):
    p = subprocess.Popen(cmd)
    _children.append(p)
    return p


def _terminate_children(grace=10):
    live = [p for p in _children if p.poll() is None]
    for p in live:
        try:
            p.terminate()
        except OSError:
            pass
    deadline = time.monotonic() + grace
    for p in live:
        try:
            p.wait(max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def main():
    host = os.environ.get("AFIX_HOST", "127.0.0.1")
    port = os.environ.get("AFIX_PORT", "8765")
    os.environ["AFIX_LAUNCHER"] = "1"
    env0 = {k: os.environ.get(k) for k in ("AFIX_DATA", "AFIX_MODELS")}
    web_cmd = [sys.executable, "-m", "uvicorn", "server.app:create_app", "--factory", "--host", host, "--port", port]
    worker_cmd = [sys.executable, "-m", "server.worker", "--device", "cpu"]
    atexit.register(_terminate_children)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):  # SIGBREAK exists on Windows only
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    try:
        while True:
            # the folders chosen in Settings are read at every (re)start; the environment still wins
            for k, v in env0.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            locations.apply_location()
            data = os.environ["AFIX_DATA"]
            os.makedirs(data, exist_ok=True)
            web = _spawn(web_cmd)
            worker = _spawn(worker_cmd)
            print(f"OpenAudioFix on http://{host}:{port}/  (data: {data})", flush=True)
            while not stopping:
                time.sleep(1)
                if web.poll() is not None:
                    break
                if worker.poll() is not None:
                    print(f"worker exited with {worker.returncode}; restarting", file=sys.stderr, flush=True)
                    _children.remove(worker)
                    worker = _spawn(worker_cmd)
            if stopping or web.returncode != RESTART_CODE:
                break
            print("restarting (asked from Settings)", flush=True)
            _terminate_children()
            _children.clear()
    finally:
        _terminate_children()
    return web.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
