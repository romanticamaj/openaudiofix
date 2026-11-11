"""GPU module install state machine (spec section 3, ADR-0012). The GPU stack lives in this Python environment.

not_installed -> checking -> ready_to_download -> downloading -> verifying -> installed
                    |                                 |              |
                 blocked                            failed         failed

The core owns the GPU worker: once the packages are installed it starts `python -m server.worker --device gpu`
as a child process (which exits when the core goes away) and runs the self-test in a separate child process.
Every step is synchronous and records its outcome in the store (it never raises); `start()` runs one in a
background thread. The install steps are injected (`gpu`), so tests never run pip, nvidia-smi or torch.

In the Docker images AFIX_GPU_INSTALL=0: the gpu image has the stack built in (it is tested and enabled at
startup, no install), the core image has none and points to the gpu image.
"""
import logging
import os
import subprocess
import sys
import threading
import time

from openaudiofix import gpu as G

from . import db

log = logging.getLogger(__name__)

TERMINAL = {"not_installed", "blocked", "ready_to_download", "installed", "failed"}
MID = "denoise"
ACTIONS = ("precheck", "download", "verify", "remove", "quick_recheck")
_ACTION_STEP = {"precheck": "precheck", "download": "download", "verify": "verify", "remove": "remove",
                "quick_recheck": "precheck"}
# every step this module records; the UI shows gpu.step.<step> (web/src/i18n), checked by tests on both sides
STEPS = ("precheck", "confirm", "download", "start", "verify", "remove", "done")
SPACE_FACTOR = 2.2  # wheels are downloaded, then unpacked next to them
RESTART_EVERY_S = 30.0  # a GPU worker that died is started again at most this often
MAX_RESTARTS = 3        # ... and after this many deaths in a row the module is marked failed


def selftest_outcome(out, exit_code=0):
    """Map a self-test result (gpu_selftest.run) to (state, step, detail)."""
    if not isinstance(out, dict):
        return "failed", "verify", {"code": "selftest_failed", "error": "self-test output is not a JSON object"}
    if exit_code == 0 and out.get("ok"):
        return "installed", "done", {"gpu": out.get("gpu"), "speed_x": out.get("speed_x"), "load_s": out.get("load_s")}
    return "failed", "verify", {"code": "selftest_failed", "error": str(out.get("error") or "self-test failed"),
                                "failed_at": out.get("step")}


def record_selftest(conn, out, exit_code=0, **extra):
    state, step, detail = selftest_outcome(out, exit_code)
    db.set_module_state(conn, MID, state, step, dict(detail, **extra))
    return state


def _enough_space(local, free, size):
    return bool(local) or free > size * SPACE_FACTOR


def spawn_worker(data_dir):
    """The GPU worker as a child process. Its stdin is a pipe from this process: when the core exits (even when
    it is killed) the pipe closes and the worker stops (server.worker --exit-with-parent)."""
    env = dict(os.environ, AFIX_DATA=str(data_dir), AFIX_GPU_SELFTEST="0", PYTHONUNBUFFERED="1", PYTHONUTF8="1",
               MODEL_DIR=str(G.model_dir()))
    return subprocess.Popen([sys.executable, "-m", "server.worker", "--device", "gpu", "--exit-with-parent"],
                            stdin=subprocess.PIPE, env=env)


class GpuManager:
    def __init__(self, conn_factory, storage, gpu=G, spawn=spawn_worker):
        self._conn_factory = conn_factory
        self._tls = threading.local()  # one SQLite connection per thread (see db.connect)
        self.conn = self._tls.conn = conn_factory()
        self.storage = storage
        self.gpu = gpu
        self.spawn = spawn
        self.proc = None
        self._deaths, self._last_spawn = 0, 0.0
        self._lock = threading.Lock()
        self._proc_lock = threading.Lock()
        self._thread = None

    # ------------------------------------------------------------ state
    def _db(self):
        c = getattr(self._tls, "conn", None)
        if c is None:
            c = self._tls.conn = self._conn_factory()
        return c

    def state(self):
        return db.get_module_state(self._db(), MID)

    def _set(self, state, step=None, **detail):
        if self.gpu.install_disabled():
            detail.setdefault("managed", "image")  # the UI offers no install/remove inside a Docker image
        db.set_module_state(self._db(), MID, state, step, detail)

    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, action):
        """Run one action in the background. False if busy, or if the action can't run in the current state."""
        if action not in ACTIONS:
            raise ValueError(f"unknown action: {action}")
        with self._lock:
            if self.busy():
                return False
            if action == "download" and self.state()["state"] != "ready_to_download":
                return False
            self._thread = threading.Thread(target=self._run, args=(action,), daemon=True)
            self._thread.start()
            return True

    def _run(self, action):
        try:
            getattr(self, action)()
        except Exception:  # the public steps record their own failures; this only keeps the thread quiet
            log.exception("GPU module action %s crashed", action)
        finally:
            c = getattr(self._tls, "conn", None)
            if c is not None:
                c.close()
                self._tls.conn = None

    def _guard(self, action, fn):
        """Run a step; any unexpected error becomes `failed` at the step that was in progress."""
        try:
            return fn()
        except Exception as e:
            log.exception("GPU module %s failed", action)
            try:
                step = self.state().get("step") or _ACTION_STEP[action]
            except Exception:
                step = _ACTION_STEP[action]
            try:
                self._set("failed", step, code="internal_error", error=str(e))
            except Exception:
                log.exception("could not record the GPU module failure")
            return False

    # ------------------------------------------------------------ the worker process
    def worker_running(self):
        with self._proc_lock:
            return self.proc is not None and self.proc.poll() is None

    def _ensure_worker(self):
        with self._proc_lock:
            if self.proc is None or self.proc.poll() is not None:
                self.proc = self.spawn(self.storage.root)
                self._last_spawn = time.time()

    def stop_worker(self, timeout=15):
        with self._proc_lock:
            p, self.proc = self.proc, None
        if p is None or p.poll() is not None:
            return
        try:
            p.terminate()
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        except OSError:
            pass

    def shutdown(self):
        self.stop_worker()

    # ------------------------------------------------------------ steps
    def precheck(self):
        return self._guard("precheck", self._precheck)

    def _precheck(self):
        if self.state()["state"] == "installed":  # re-test of a working install: check the GPU, rerun the self-test
            if self._quick_recheck():
                self._verify()
            return
        self._set("checking", "precheck")
        try:
            name, mem, drv = self.gpu.nvidia()
        except G.GpuUnavailable as e:
            return self._set("blocked", "precheck", code=e.code, error=str(e))
        local = self.gpu.packages_present()
        if not local and self.gpu.install_disabled():
            return self._set("blocked", "precheck", code="install_disabled", gpu=name, vram=mem, driver=drv,
                             error="This Docker image has no GPU stack; start the gpu image instead.")
        size = 0 if local else self.gpu.ESTIMATED_BYTES
        free = self.storage.free_bytes()
        self._set("ready_to_download", "confirm", gpu=name, vram=mem, driver=drv, download_bytes=size,
                  free_bytes=free, enough_space=_enough_space(local, free, size), local_image=local)

    def download(self):
        return self._guard("download", self._download)

    def _download(self):
        s = self.state()
        d = s.get("detail") or {}
        if s["state"] != "ready_to_download":
            return False
        local = d.get("local_image")
        if not local:  # free space may have changed since the precheck
            free = self.storage.free_bytes()
            if not _enough_space(local, free, d.get("download_bytes") or self.gpu.ESTIMATED_BYTES):
                return self._set("ready_to_download", "confirm",
                                 **dict(d, free_bytes=free, enough_space=False, code="not_enough_space"))
        total = (d.get("download_bytes") or 0) + self.gpu.WEIGHTS_BYTES
        last = [0.0]

        def report(pct=0.0, done_bytes=0, phase="packages"):
            if time.time() - last[0] < 0.5 and pct < 100:
                return
            last[0] = time.time()
            self._set("downloading", "download", pct=round(pct * 0.95, 1), done_bytes=done_bytes,
                      total_bytes=total, phase=phase)
        try:
            if not local:
                report(0.0)
                self.gpu.install(report)
            self._set("downloading", "download", pct=95.0, done_bytes=total, total_bytes=total, phase="weights")
            self.gpu.download_weights()
        except Exception as e:
            return self._set("failed", "download", code="install_failed", error=str(e))
        self._verify()

    def verify(self):
        return self._guard("verify", self._verify)

    def _verify(self):
        if not self.gpu.packages_present():
            return self._set("failed", "verify", code="packages_missing",
                             error="The GPU packages are not installed in this environment; install them first.")
        self._set("verifying", "verify")
        out, code = self.gpu.selftest()
        if record_selftest(self._db(), out, code, **({"managed": "image"} if self.gpu.install_disabled() else {})) \
                != "installed":
            self.stop_worker()
            return
        try:
            self._deaths = 0
            self._ensure_worker()
        except Exception as e:
            self._set("failed", "start", code="start_failed", error=str(e))

    def remove(self):
        return self._guard("remove", self._remove)

    def _remove(self):
        self.stop_worker()
        if self.gpu.install_disabled():  # nothing to uninstall inside an image
            return self._set("not_installed", None)
        try:
            self.gpu.uninstall()
        except Exception as e:
            return self._set("failed", "remove", code="uninstall_failed", error=str(e))
        self._set("not_installed", None)

    def quick_recheck(self):
        return self._guard("quick_recheck", self._quick_recheck)

    def _quick_recheck(self):
        """nvidia-smi and the packages; on failure the module is marked failed. True when both are there."""
        try:
            self.gpu.nvidia()
        except G.GpuUnavailable as e:
            self.stop_worker()
            self._set("failed", "precheck", code=e.code, error=str(e))
            return False
        if not self.gpu.packages_present():
            self.stop_worker()
            self._set("failed", "verify", code="packages_missing",
                      error="The GPU packages are no longer installed in this environment.")
            return False
        return True

    def _start_installed(self):
        if self._quick_recheck():
            self._ensure_worker()

    def startup(self):
        """On core start. An installed module gets its worker back; packages that are already there (the gpu
        Docker image, or a manual `afix gpu install`) are tested and enabled without a click, whatever an earlier
        run left behind (blocked, failed, an older version's state); a step cut short by a restart becomes
        failed/interrupted when the packages are not there to test."""
        s = self.state()
        if s["state"] == "installed":
            threading.Thread(target=self._guard, args=("quick_recheck", self._start_installed), daemon=True).start()
            return
        if s["state"] not in TERMINAL:
            self._set("failed", s.get("step") or "precheck", code="interrupted",
                      error=f"interrupted while {s['state']} (the server restarted)")
        if self.gpu.packages_present():
            try:
                self.gpu.nvidia()
            except G.GpuUnavailable:
                return
            self.start("verify")

    def tick(self, wait=False):
        """Periodic: start again a GPU worker that died; after MAX_RESTARTS deaths in a row mark the module failed."""
        if self.busy() or self.state()["state"] != "installed" or self.worker_running():
            return
        if time.time() - self._last_spawn < RESTART_EVERY_S:
            return
        if self.proc is not None or self._last_spawn:  # it ran and died
            self._deaths += 1
        if self._deaths >= MAX_RESTARTS:
            self._set("failed", "start", code="worker_lost",
                      error=f"The GPU worker stopped {self._deaths} times in a row; see the server log.")
            return
        try:
            self._ensure_worker()
        except Exception as e:
            self._set("failed", "start", code="start_failed", error=str(e))
