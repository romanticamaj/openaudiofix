"""Queue worker: python -m server.worker --device cpu|gpu [--data DIR]

Job kinds: scan (decode + every module's scan), repair (one module = one stage), finalize (checks, previews,
peaks, CSV, report). One stage per job, so a crash loses at most the step that was running.

A job's outcome is committed in one transaction: the guarded `db.finish` plus the project update / next
enqueue. If the job is no longer this worker's (requeued after a stale heartbeat, taken by another worker),
`finish` changes nothing and the project is left alone.
"""
import argparse
import importlib
import json
import logging
import math
import os
import socket
import sys
import threading
import time
import traceback
from dataclasses import asdict

import numpy as np

from openaudiofix import chain, io as aio, modules, report
from openaudiofix.errors import AfixError, Cancelled
from openaudiofix.modules.base import check_output, run_repair

from . import locations
from . import db, previews, visual
from .storage import Storage, write_text_atomic

log = logging.getLogger("openaudiofix.worker")

PEAK_BUCKETS = 8000
PEAK_CHUNK = 1 << 20
PROGRESS_EVERY = 0.5
KEEPALIVE_EVERY = 10.0


def _load_extra_modules():
    for spec in filter(None, os.environ.get("AFIX_EXTRA_MODULES", "").split(",")):
        mod, _, cls = spec.strip().partition(":")
        modules.register(getattr(importlib.import_module(mod), cls)())


def _peak_of(get, n):
    pk = 0.0
    for a in range(0, n, PEAK_CHUNK):
        pk = max(pk, float(np.abs(np.asarray(get(a, min(n, a + PEAK_CHUNK)))).max(initial=0.0)))
    return pk


def _slicer(a):
    return lambda i0, i1: a[i0:i1]


def _differ(a, b):
    return lambda i0, i1: np.asarray(a[i0:i1]) - np.asarray(b[i0:i1])


def _module(mid):
    try:
        return modules.get(mid)
    except KeyError:
        raise AfixError(f"Module {mid!r} is not available on this worker.", code="unknown_module") from None


class Worker:
    def __init__(self, data_dir, device, worker_id=None, poll=1.0, stale_timeout=60.0):
        self.st = Storage(data_dir)
        self.conn = db.connect(self.st.db_path)
        self.device = device
        self.id = worker_id or f"{device}-{socket.gethostname()}-{os.getpid()}"
        self.poll, self.stale_timeout = poll, stale_timeout
        _load_extra_modules()

    # ------------------------------------------------------------ GPU self-test
    def report_gpu_selftest(self, selftest=None):
        """Run the GPU self-test in this process and record the module state (installed or failed) in the store.

        Used by a GPU worker started by hand (`python -m server.worker --device gpu`, e.g. on another machine that
        shares the data folder); the core's own worker is tested by the core instead. The worker keeps beating
        while the test runs, flagged `selftest: running`. A crash of the self-test is recorded as failed, not raised.
        """
        from . import gpu_manager, gpu_selftest
        selftest = selftest or gpu_selftest.run
        testing = {"selftest": "running"}
        db.worker_beat(self.conn, self.id, self.device, testing)
        stop = threading.Event()
        beat = threading.Thread(target=self._beat_until, args=(stop, testing), daemon=True)
        beat.start()
        try:
            try:
                out = selftest()
            except Exception as e:
                out = {"ok": False, "step": "verify", "error": f"{type(e).__name__}: {e}"}
        finally:
            stop.set()
            beat.join(KEEPALIVE_EVERY + 5)
        state = gpu_manager.record_selftest(self.conn, out, 0 if out.get("ok") else 1, reported_by="worker")
        db.worker_beat(self.conn, self.id, self.device)
        log.info("GPU self-test: %s %s", state, out.get("error") or "")
        return state

    def _beat_until(self, stop, info=None):
        conn = db.connect(self.st.db_path)
        try:
            while not stop.wait(KEEPALIVE_EVERY):
                db.worker_beat(conn, self.id, self.device, info)
        finally:
            conn.close()

    # ------------------------------------------------------------ loop
    def run_forever(self):
        while True:
            try:
                busy = self.run_once()
            except Exception:  # e.g. the database stayed locked; the stale reaper recovers the job
                log.exception("worker %s: unexpected error, continuing", self.id)
                busy = False
            if not busy:
                time.sleep(self.poll)

    def run_once(self):
        db.worker_beat(self.conn, self.id, self.device)
        self.conn.execute("BEGIN IMMEDIATE")  # reaped jobs and their project states change together
        try:
            for r in db.requeue_stale(self.conn, timeout=self.stale_timeout):
                if r["state"] == "failed":
                    job = db.get_job(self.conn, r["id"])
                    db.update_project(self.conn, r["project_id"], state="failed", error=job["error"])
                elif r["state"] == "cancelled":
                    db.update_project(self.conn, r["project_id"], state="cancelled")
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        job = db.claim(self.conn, self.device, self.id)
        if job is None:
            return False
        self.run_job(job)
        return True

    def run_job(self, job):
        stop = threading.Event()
        threading.Thread(target=self._keepalive, args=(job["id"], stop), daemon=True).start()
        pid = job["project_id"]
        try:
            handler = {"scan": self._scan, "repair": self._repair, "finalize": self._finalize}[job["kind"]]
            result, after = handler(job)
            self._commit(job, "done", after, result=result)
        except Cancelled:
            self._commit(job, "cancelled", lambda: db.update_project(self.conn, pid, state="cancelled"))
        except AfixError as e:
            self._fail(job, {"code": e.code, "message": e.message, "hint": e.hint})
        except MemoryError:
            self._fail(job, {"code": "out_of_memory", "message": "The server ran out of memory during this step.",
                             "hint": "Close other programs or process a shorter file."})
        except Exception as e:
            self._fail(job, {"code": "internal_error", "message": f"{type(e).__name__}: {e}", "hint": "",
                             "detail": traceback.format_exc()})
        finally:
            stop.set()

    def _commit(self, job, state, after=None, **kw):
        """Finish the job and apply its project side effects atomically; False if the job was lost.

        A cancel requested after the step's last progress check still wins: a "done" outcome of a job whose
        cancel flag is set is recorded as cancelled and its side effects (next stage, project state) are skipped.
        """
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if state == "done":
                r = self.conn.execute("SELECT cancel FROM jobs WHERE id=?", (job["id"],)).fetchone()
                if r is not None and r["cancel"]:
                    pid = job["project_id"]
                    state, kw = "cancelled", {}
                    after = lambda: db.update_project(self.conn, pid, state="cancelled")  # noqa: E731
            ok = db.finish(self.conn, job["id"], state, worker_id=self.id, **kw)
            if ok and after is not None:
                after()
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return ok

    def _fail(self, job, err):
        err = dict(err, module=job.get("module"), kind=job["kind"])
        self._commit(job, "failed", lambda: db.update_project(self.conn, job["project_id"], state="failed",
                                                              error=err), error=err)

    def _keepalive(self, job_id, stop):
        conn = db.connect(self.st.db_path)
        try:
            while not stop.wait(KEEPALIVE_EVERY):
                if not db.touch(conn, job_id, worker_id=self.id):
                    return  # lost the job; the next progress report stops the handler
                db.worker_beat(conn, self.id, self.device)
        finally:
            conn.close()

    def _progress(self, job_id):
        last = [0.0]

        def progress(pct=None, message=None, **_):
            now = time.time()
            if now - last[0] < PROGRESS_EVERY and pct not in (0.0, 100.0):
                return
            last[0] = now
            if db.report_progress(self.conn, job_id, pct, message, worker_id=self.id):
                raise Cancelled("cancelled by the user")
        return progress

    # ------------------------------------------------------------ handlers
    # Each handler returns (job result, after) where after() applies the project side effects inside _commit.
    def _scan(self, job):
        pid = job["project_id"]
        p = db.get_project(self.conn, pid)
        prog = self._progress(job["id"])
        prog(pct=0.0, message="decode")
        src_file = self.st.pdir(pid) / p["original_file"]
        pr = aio.probe(src_file)
        s = aio.decode(src_file, int(p.get("stream") or 0), pr)
        self.st.save_source(pid, s.audio)
        scans = {}
        mods = modules.all_modules()
        for i, m in enumerate(mods):
            prog(pct=round(i / len(mods) * 100, 1), message=m.id)
            scans[m.id] = asdict(m.scan(s.audio, s.sr))
        prog(pct=100.0, message="done")
        fields = dict(sr=s.sr, channels=int(s.audio.shape[1]), subtype=s.subtype, wav_original=s.wav_path is not None,
                      lossy=s.lossy, codec=s.codec, duration_s=round(len(s.audio) / s.sr, 3),
                      has_video=pr.has_video, scan=scans, state="ready", error=None)
        return {"modules": list(scans)}, lambda: db.update_project(self.conn, pid, **fields)

    def _repair(self, job):
        pid, seq, mid = job["project_id"], int(job["seq"]), job["module"]
        p = db.get_project(self.conn, pid)
        mods = p["modules"]
        m = _module(mid)
        prog = self._progress(job["id"])
        prog(pct=0.0, message="start")
        inp = self.st.load(self.st.source_path(pid) if seq == 0 else self.st.stage_path(pid, seq - 1, mods[seq - 1]))
        inp = np.array(inp)
        src = np.array(self.st.load(self.st.source_path(pid))) if m.uses_source and seq else inp
        res = run_repair(m, inp, p["sr"], (p.get("params") or {}).get(m.id, {}), prog, src)
        check_output(inp, res.audio)
        prog(pct=100.0, message="save")
        self.st.save_stage(pid, seq, mid, res.audio, res.fixes, res.notes)

        def after():
            if seq + 1 < len(mods):
                nxt = _module(mods[seq + 1])
                db.enqueue(self.conn, pid, "repair", nxt.device, nxt.id, seq + 1)
            else:
                db.enqueue(self.conn, pid, "finalize", "cpu", None, len(mods))
        return {"fixes": len(res.fixes)}, after

    def _write_view(self, pid, sid, get, n, sr, ch, gain):
        """One listenable/viewable source or diff: WebM preview plus overview peaks, both at `gain`."""
        previews.encode_preview(get, n, sr, ch, self.st.preview_path(pid, sid), gain=gain)
        peaks = visual.peaks_minmax(get, 0, n, PEAK_BUCKETS, gain)
        write_text_atomic(self.st.peaks_path(pid, sid), json.dumps(peaks))

    def _finalize(self, job):
        pid = job["project_id"]
        p = db.get_project(self.conn, pid)
        mods, sr, ch = p["modules"], p["sr"], p["channels"]
        prog = self._progress(job["id"])
        prog(pct=0.0, message="start")
        src = self.st.load(self.st.source_path(pid))
        stages = [self.st.load(self.st.stage_path(pid, k, m)) for k, m in enumerate(mods)]
        fx = [self.st.load_fixes(pid, k, m) for k, m in enumerate(mods)]
        if not any(_module(m).whole_file for m in mods):
            prog(pct=1.0, message="verify")
            chain.verify_unchanged(np.asarray(src), np.asarray(stages[-1]), [f for fixes, _ in fx for f in fixes])
        n = len(src)
        peaks = [_peak_of(_slicer(a), n) for a in [src, *stages]]
        peak = max(peaks)
        # floor to 0.01 dB so the stored (rounded) gain never pushes a preview over full scale
        preview_gain_db = math.floor(-20 * math.log10(peak) * 100) / 100 if peak > 1.0 else 0.0
        # export gain: every chain except glitch-only reduces gain (spec 4.3, chain.reduce_gain_for)
        export_gain_db = aio.peak_gain_db(np.array([peaks[-1]])) if chain.reduce_gain_for(mods) else 0.0
        g = 10 ** (preview_gain_db / 20)
        sources = [("original", _slicer(src))] + [(f"s{k}", _slicer(stages[k])) for k in range(len(mods))]
        diffs = [("d_chain", None, _differ(src, stages[-1]))] + [
            (f"d{k}", m, _differ(src if k == 0 else stages[k - 1], stages[k])) for k, m in enumerate(mods)]
        total = len(sources) + len(diffs)
        done = 0
        for sid, get in sources:
            prog(pct=round(done / total * 100, 1), message=f"preview {sid}")
            self._write_view(pid, sid, get, n, sr, ch, g)
            done += 1
        diff_meta = []
        for sid, m, get in diffs:
            prog(pct=round(done / total * 100, 1), message=f"preview {sid}")
            pk = _peak_of(get, n)
            gain_db = round(float(20 * np.log10(0.89 / pk)), 2) if pk > 0 else None
            if gain_db is not None:
                self._write_view(pid, sid, get, n, sr, ch, 10 ** (gain_db / 20))
            diff_meta.append({"id": sid, "module": m, "gain_db": gain_db})
            done += 1
        outs = [chain.StageOutput(m, None, fixes, notes) for m, (fixes, notes) in zip(mods, fx)]
        csv_path = self.st.pdir(pid) / "fixes.csv"
        tmp = aio.tmp_path_for(csv_path)
        try:
            report.write_fixes_csv(tmp, outs, sr)
            os.replace(tmp, csv_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        export_gain_db = round(export_gain_db, 2)
        # seconds each repair step ran (the finalize step's own time is in its job row)
        timings = {j["module"]: round(j["finished"] - j["started"], 2) for j in db.jobs_for(self.conn, pid)
                   if j["kind"] == "repair" and j["state"] == "done" and j["started"] and j["finished"]}
        result = dict(rev=round(time.time(), 3), preview_gain_db=preview_gain_db, export_gain_db=export_gain_db,
                      sources=[{"id": "original"}] + [{"id": f"s{k}", "module": m} for k, m in enumerate(mods)],
                      diffs=diff_meta, counts={m: len(f) for m, (f, _) in zip(mods, fx)},
                      notes={m: notes for m, (_, notes) in zip(mods, fx)}, timings=timings,
                      byte_exact_possible=bool(p.get("wav_original")) and p.get("subtype") in aio.PATCHABLE_SUBTYPES
                      and export_gain_db == 0.0)
        write_text_atomic(self.st.pdir(pid) / "report.json",
                          json.dumps(dict(result, project=pid, modules=mods, params=p.get("params") or {}),
                                     ensure_ascii=False, indent=1))
        prog(pct=100.0, message="done")
        return {"ok": True}, lambda: db.update_project(self.conn, pid, state="done", result=result, error=None)


def _exit_when_stdin_closes():
    """The parent's end of the pipe closes when it exits or is killed; a job cut short is requeued as stale."""
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except (OSError, ValueError):
        pass
    log.info("parent process is gone; stopping")
    os._exit(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", choices=["cpu", "gpu"], required=True)
    ap.add_argument("--data", default=os.environ.get("AFIX_DATA") or locations.default_data())
    ap.add_argument("--exit-with-parent", action="store_true",
                    help="stop when stdin closes: the core starts the GPU worker with a pipe as its stdin")
    a = ap.parse_args()
    if a.exit_with_parent:
        threading.Thread(target=_exit_when_stdin_closes, daemon=True).start()
    w = Worker(a.data, a.device)
    # a GPU worker started by the core is tested by the core: AFIX_GPU_SELFTEST=0
    if a.device == "gpu" and os.environ.get("AFIX_GPU_SELFTEST", "1") != "0":
        w.report_gpu_selftest()
    w.run_forever()


if __name__ == "__main__":
    main()
