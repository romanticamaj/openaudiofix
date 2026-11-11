"""Copying the work data (or the models) to a new folder from the Settings page.

The copy runs in a thread and reports bytes copied. The old folder is left untouched: after a restart the server
uses the new one, and the person deletes the old one when they are satisfied. The database is copied with SQLite's
backup API so a copy taken while the server is running is consistent.
"""
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path


def tree_size(root):
    total = 0
    for dp, _, fs in os.walk(root):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


class Copier:
    """One copy at a time; state() is what the UI polls."""

    def __init__(self):
        self._lock = threading.Lock()
        self._s = {"state": "idle"}

    def state(self):
        with self._lock:
            return dict(self._s)

    def _set(self, **kw):
        with self._lock:
            self._s.update(kw)

    def busy(self):
        return self.state()["state"] == "copying"

    def start(self, what, src, dst, on_done, skip=("tmp",)):
        """Copy folder `src` into empty-or-new folder `dst`; on_done(dst) runs after a successful copy."""
        with self._lock:
            if self._s["state"] == "copying":
                return False
            self._s = {"state": "copying", "what": what, "to": str(dst), "done_bytes": 0, "total_bytes": None,
                       "started": time.time()}
        threading.Thread(target=self._run, args=(Path(src), Path(dst), on_done, set(skip)), daemon=True).start()
        return True

    def _run(self, src, dst, on_done, skip):
        try:
            self._set(total_bytes=tree_size(src))
            done = 0
            dst.mkdir(parents=True, exist_ok=True)
            for dp, dns, fs in os.walk(src):
                rel = Path(dp).relative_to(src)
                dns[:] = [d for d in dns if not (rel == Path(".") and d in skip)]
                (dst / rel).mkdir(parents=True, exist_ok=True)
                for f in fs:
                    s, d = Path(dp) / f, dst / rel / f
                    if f.endswith((".db-wal", ".db-shm", ".db-journal")):
                        continue
                    if f.endswith(".db"):
                        with sqlite3.connect(s) as a, sqlite3.connect(d) as b:
                            a.backup(b)
                    else:
                        shutil.copy2(s, d)
                    try:
                        done += s.stat().st_size
                    except OSError:
                        pass
                    self._set(done_bytes=done)
            on_done(dst)
            self._set(state="done", done_bytes=done, finished=time.time())
        except Exception as e:  # reported to the UI; the old folder is still in use
            self._set(state="failed", error=f"{type(e).__name__}: {e}", finished=time.time())
