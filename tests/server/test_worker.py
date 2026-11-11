import json
import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest
import soundfile as sf

from openaudiofix import modules, synth
from server import db, previews, storage as S
from server.worker import Worker
from tests import fakes


def _project(st, conn, tmp_path, audio, sr=48000, name="a.wav", subtype="PCM_24"):
    pid = st.create()
    p = st.original_path(pid, name)
    sf.write(p, audio, sr, subtype=subtype)
    db.create_project(conn, pid, name, state="scanning", original_file=p.name, stream=0)
    db.enqueue(conn, pid, "scan", "cpu")
    return pid


def _drain(w, limit=20):
    for _ in range(limit):
        if not w.run_once():
            return


def _ffprobe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True, check=True)
    info = json.loads(r.stdout)
    return info["streams"][0], float(info["format"]["duration"])


@pytest.fixture(autouse=True)
def _unregister_fakes():
    yield
    for mid in ("sleepy", "flaky"):
        modules.unregister(mid)
    fakes.FlakyModule.fail, fakes.FlakyModule.runs = True, 0


@pytest.fixture
def env(tmp_path):
    st = S.Storage(tmp_path)
    return st, db.connect(st.db_path), Worker(tmp_path, "cpu", "w-test", poll=0)


def test_scan_then_full_chain_produces_previews_and_result(env, tmp_path):
    st, conn, w = env
    x = synth.inject_clipping(synth.speech_like(seconds=0.5, seed=2, channels=2), 3.0)  # short: declip is slow
    pid = _project(st, conn, tmp_path, x)
    _drain(w)
    p = db.get_project(conn, pid)
    assert p["state"] == "ready" and p["scan"]["declip"]["detected"] and p["sr"] == 48000 and p["wav_original"]
    db.update_project(conn, pid, modules=["glitch", "declip"], state="processing")
    db.enqueue(conn, pid, "repair", "cpu", "glitch", 0)
    _drain(w)
    p = db.get_project(conn, pid)
    assert p["state"] == "done", p["error"]
    r = p["result"]
    assert [s["id"] for s in r["sources"]] == ["original", "s0", "s1"]
    assert r["preview_gain_db"] <= r["export_gain_db"] <= 0 and r["counts"]["declip"] > 0
    for sid in ("original", "s0", "s1", "d_chain"):
        assert st.preview_path(pid, sid).stat().st_size > 1000
        assert len(json.loads(st.peaks_path(pid, sid).read_text())) > 100
    assert (st.pdir(pid) / "fixes.csv").exists()
    assert json.loads((st.pdir(pid) / "report.json").read_text(encoding="utf-8"))["modules"] == ["glitch", "declip"]
    assert [j["state"] for j in db.jobs_for(conn, pid)] == ["done"] * 4


def test_44k1_input_previews_and_lengths(env, tmp_path):
    st, conn, w = env
    x = synth.inject_clipping(synth.speech_like(sr=44100, seconds=0.5, seed=2), 3.0)
    pid = _project(st, conn, tmp_path, x, sr=44100)
    _drain(w)
    db.update_project(conn, pid, modules=["glitch", "declip"], state="processing")
    db.enqueue(conn, pid, "repair", "cpu", "glitch", 0)
    _drain(w)
    p = db.get_project(conn, pid)
    assert p["state"] == "done", p["error"]
    assert p["sr"] == 44100
    assert p["result"]["counts"]["declip"] > 0
    for k, m in enumerate(["glitch", "declip"]):
        assert st.load(st.stage_path(pid, k, m)).shape == x.shape
    for sid in ("original", "s0", "s1", "d_chain"):
        stream, duration = _ffprobe(st.preview_path(pid, sid))
        assert stream["codec_name"] == "opus" and int(stream["sample_rate"]) == 48000  # Opus resamples
        assert abs(duration - 0.5) < 0.1
        # peaks are computed on the 44.1 kHz samples: 8000 max/min pairs over len(x) samples
        assert len(json.loads(st.peaks_path(pid, sid).read_text())) == 2 * 8000


def test_glitch_only_chain_keeps_export_gain_at_zero(env, tmp_path):
    st, conn, w = env
    x = synth.speech_like(seconds=1.0, seed=3)
    x = x * (1.5 / np.abs(x).max())  # float WAV keeps the overshoot: peak 1.5
    pid = _project(st, conn, tmp_path, x, subtype="FLOAT")
    _drain(w)
    db.update_project(conn, pid, modules=["glitch"], state="processing")
    db.enqueue(conn, pid, "repair", "cpu", "glitch", 0)
    _drain(w)
    p = db.get_project(conn, pid)
    assert p["state"] == "done", p["error"]
    r = p["result"]
    assert r["preview_gain_db"] == pytest.approx(-3.52, abs=0.01)  # previews still share a gain that fits
    assert r["export_gain_db"] == 0.0  # no declip step: export never reduces gain (Task 6/7 ruling)
    assert r["byte_exact_possible"] is False  # float WAV cannot be byte-patched


def test_bad_upload_fails_scan_with_code(env, tmp_path):
    st, conn, w = env
    pid = st.create()
    st.original_path(pid, "x.wav").write_bytes(b"garbage" * 100)
    db.create_project(conn, pid, "x.wav", state="scanning", original_file="original.wav", stream=0)
    db.enqueue(conn, pid, "scan", "cpu")
    _drain(w)
    p = db.get_project(conn, pid)
    assert p["state"] == "failed" and p["error"]["code"] == "decode_failed"


def test_cancel_stops_running_step(env, tmp_path, monkeypatch):
    st, conn, w = env
    monkeypatch.setenv("AFIX_EXTRA_MODULES", "tests.fakes:SleepyModule")
    w = Worker(tmp_path, "cpu", "w-test", poll=0)
    pid = _project(st, conn, tmp_path, synth.speech_like(seconds=0.5))
    _drain(w)
    db.update_project(conn, pid, modules=["sleepy"], state="processing")
    db.enqueue(conn, pid, "repair", "cpu", "sleepy", 0)
    t = threading.Thread(target=w.run_once)
    t.start()
    time.sleep(0.6)
    db.request_cancel(conn, pid)
    t.join(10)
    assert not t.is_alive()
    assert db.get_project(conn, pid)["state"] == "cancelled"
    assert db.jobs_for(conn, pid)[-1]["state"] == "cancelled"
    assert not st.stage_path(pid, 0, "sleepy").exists()


def test_stale_job_of_dead_worker_is_requeued_and_finished(env, tmp_path):
    """Spec 5.3: a worker killed mid-job (simulated: claimed, then its heartbeat goes stale)."""
    st, conn, w = env
    pid = _project(st, conn, tmp_path, synth.speech_like(seconds=0.5))
    _drain(w)
    db.update_project(conn, pid, modules=["glitch"], state="processing")
    jid = db.enqueue(conn, pid, "repair", "cpu", "glitch", 0)
    assert db.claim(conn, "cpu", "w-dead")["id"] == jid
    conn.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (time.time() - 120, jid))
    _drain(w)
    assert db.get_project(conn, pid)["state"] == "done"
    job = db.get_job(conn, jid)
    assert job["state"] == "done" and job["worker"] == "w-test" and job["attempts"] == 2


def test_stale_cancelled_and_failed_jobs_update_the_project(env, tmp_path):
    st, conn, w = env
    pa = _project(st, conn, tmp_path, synth.speech_like(seconds=0.3))
    pb = _project(st, conn, tmp_path, synth.speech_like(seconds=0.3))
    _drain(w)
    for pid in (pa, pb):
        db.update_project(conn, pid, modules=["glitch"], state="processing")
    ja = db.enqueue(conn, pa, "repair", "cpu", "glitch", 0)
    jb = db.enqueue(conn, pb, "repair", "cpu", "glitch", 0)
    db.claim(conn, "cpu", "w-dead")
    db.claim(conn, "cpu", "w-dead")
    db.request_cancel(conn, pa)
    conn.execute("UPDATE jobs SET heartbeat=?, attempts=2 WHERE id IN (?, ?)", (time.time() - 120, ja, jb))
    assert w.run_once() is False
    assert db.get_project(conn, pa)["state"] == "cancelled"
    p = db.get_project(conn, pb)
    assert p["state"] == "failed" and p["error"]["code"] == "worker_lost"


def test_lost_job_does_not_write_project_state(env, tmp_path):
    st, conn, w = env
    pid = _project(st, conn, tmp_path, synth.speech_like(seconds=0.3))
    _drain(w)
    db.update_project(conn, pid, modules=["glitch"], state="processing")
    db.enqueue(conn, pid, "repair", "cpu", "glitch", 0)
    job = db.claim(conn, "cpu", w.id)
    conn.execute("UPDATE jobs SET worker='w-other' WHERE id=?", (job["id"],))  # requeued and taken meanwhile
    w.run_job(job)
    assert db.get_project(conn, pid)["state"] == "processing"
    assert db.get_job(conn, job["id"])["state"] == "running"
    assert [j["kind"] for j in db.jobs_for(conn, pid)] == ["scan", "repair"]  # no finalize enqueued


def test_retry_resumes_from_the_failed_step(env, tmp_path, monkeypatch):
    st, conn, w = env
    monkeypatch.setenv("AFIX_EXTRA_MODULES", "tests.fakes:FlakyModule")
    w = Worker(tmp_path, "cpu", "w-test", poll=0)
    pid = _project(st, conn, tmp_path, synth.speech_like(seconds=0.5))
    _drain(w)
    db.update_project(conn, pid, modules=["glitch", "flaky"], state="processing")
    db.enqueue(conn, pid, "repair", "cpu", "glitch", 0)
    _drain(w)
    p = db.get_project(conn, pid)
    assert p["state"] == "failed" and p["error"]["code"] == "internal_error" and p["error"]["module"] == "flaky"
    s0 = st.stage_path(pid, 0, "glitch")
    mtime = s0.stat().st_mtime_ns
    fakes.FlakyModule.fail = False
    assert db.retry_failed(conn, pid)["module"] == "flaky"
    db.update_project(conn, pid, state="processing", error=None)
    _drain(w)
    assert db.get_project(conn, pid)["state"] == "done"
    assert s0.stat().st_mtime_ns == mtime  # the glitch step was not run again
    assert fakes.FlakyModule.runs == 2
    assert [(j["kind"], j["module"]) for j in db.jobs_for(conn, pid)] == [
        ("scan", None), ("repair", "glitch"), ("repair", "flaky"), ("finalize", None)]


def test_preview_downmixes_more_than_two_channels(tmp_path):
    x = synth.speech_like(seconds=0.5, channels=6)
    dst = tmp_path / "p.webm"
    previews.encode_preview(lambda i0, i1: x[i0:i1], len(x), 48000, 6, dst)
    stream, duration = _ffprobe(dst)
    assert stream["channels"] == 2 and abs(duration - 0.5) < 0.1
    assert [q.name for q in tmp_path.iterdir()] == ["p.webm"]  # no temp file left behind


def test_51_downmix_follows_itu_layout():
    seg = np.zeros((4, 6))
    seg[0, 2] = 1.0  # C -> both sides equally
    seg[1, 3] = 1.0  # LFE -> both sides equally
    seg[2, 4] = 1.0  # Ls -> left only
    seg[3, 5] = 1.0  # Rs -> right only
    out = previews._to_stereo(seg)
    assert out[0, 0] == out[0, 1] > 0 and out[1, 0] == out[1, 1] > 0
    assert out[2, 0] > 0 and out[2, 1] == 0 and out[3, 1] > 0 and out[3, 0] == 0
    assert np.abs(previews._to_stereo(np.ones((1, 6)))).max() <= 1.0 + 1e-12  # never over full scale


def test_cancel_after_last_progress_check_still_cancels(env, tmp_path, monkeypatch):
    """A cancel that lands while the stage is being saved must stop the chain (no next stage enqueued)."""
    st, conn, w = env
    pid = _project(st, conn, tmp_path, synth.speech_like(seconds=0.3))
    _drain(w)
    db.update_project(conn, pid, modules=["glitch", "declip"], state="processing")
    db.enqueue(conn, pid, "repair", "cpu", "glitch", 0)
    save = w.st.save_stage

    def save_then_cancel(*a, **kw):
        db.request_cancel(conn, pid)
        return save(*a, **kw)
    monkeypatch.setattr(w.st, "save_stage", save_then_cancel)
    _drain(w)
    assert db.get_project(conn, pid)["state"] == "cancelled"
    assert [(j["kind"], j["module"], j["state"]) for j in db.jobs_for(conn, pid)] == [
        ("scan", None, "done"), ("repair", "glitch", "cancelled")]


@pytest.mark.slow
def test_killed_worker_job_is_requeued_and_finished_by_another(tmp_path):
    st = S.Storage(tmp_path)
    conn = db.connect(st.db_path)
    pid = _project(st, conn, tmp_path, synth.speech_like(seconds=0.5))
    Worker(tmp_path, "cpu", "w0", poll=0).run_once()
    db.update_project(conn, pid, modules=["sleepy"], state="processing")
    db.enqueue(conn, pid, "repair", "cpu", "sleepy", 0)
    env = dict(os.environ, AFIX_EXTRA_MODULES="tests.fakes:SleepyModule", PYTHONUTF8="1")
    proc = subprocess.Popen([sys.executable, "-m", "server.worker", "--device", "cpu", "--data", str(tmp_path)], env=env)
    for _ in range(100):
        if db.jobs_for(conn, pid)[-1]["state"] == "running":
            break
        time.sleep(0.1)
    proc.kill(); proc.wait()
    os.environ["AFIX_EXTRA_MODULES"] = "tests.fakes:SleepyModule"
    try:
        w = Worker(tmp_path, "cpu", "w2", poll=0, stale_timeout=0.5)
        time.sleep(1.0)
        for _ in range(5):
            w.run_once()
    finally:
        del os.environ["AFIX_EXTRA_MODULES"]
    assert db.get_project(conn, pid)["state"] == "done"


class _HookConn:
    """Connection proxy that runs `hook` right after the first statement matching `marker`."""
    def __init__(self, conn, marker, hook):
        self.conn, self.marker, self.hook, self.fired = conn, marker, hook, False

    def execute(self, sql, *args):
        r = self.conn.execute(sql, *args)
        if not self.fired and self.marker in sql:
            self.fired = True
            self.hook()
        return r


def test_cancel_is_not_lost_when_a_step_finishes_mid_cancel(env, tmp_path):
    """final review m2: a worker committing "step 0 done, enqueue step 1" between request_cancel's two UPDATEs."""
    st, conn, w = env
    pid = "p" * 12
    db.create_project(conn, pid, "a.wav", state="processing", modules=["glitch", "declip"])
    db.enqueue(conn, pid, "repair", "cpu", "glitch", 0)
    job = db.claim(w.conn, "cpu", w.id)
    t = threading.Thread(target=w._commit, args=(job, "done", lambda: db.enqueue(w.conn, pid, "repair", "cpu",
                                                                                   "declip", 1)))

    def finish_step_now():
        t.start()
        t.join(1.0)  # without a transaction the worker commits here; with one it waits for the cancel
    db.request_cancel(_HookConn(conn, "state='queued'", finish_step_now), pid)
    t.join(10)
    jobs = db.jobs_for(conn, pid)
    assert [j["state"] for j in jobs] == ["cancelled"], jobs  # step 0 recorded as cancelled, step 1 never queued
    assert db.get_project(conn, pid)["state"] == "cancelled"


def test_delete_project_is_one_transaction(env):
    st, conn, w = env
    db.create_project(conn, "q" * 12, "a.wav", state="ready")
    db.enqueue(conn, "q" * 12, "scan", "cpu")

    def boom():
        raise RuntimeError("crash between the two deletes")
    with pytest.raises(RuntimeError):
        db.delete_project(_HookConn(conn, "DELETE FROM jobs", boom), "q" * 12)
    assert conn.in_transaction is False
    assert db.get_project(conn, "q" * 12) is not None and len(db.jobs_for(conn, "q" * 12)) == 1


def test_gpu_worker_main_runs_the_self_test_once_at_startup(tmp_path, monkeypatch):
    """final review I2: a compose-started GPU worker (no Docker socket in core) enables the module itself."""
    from server import gpu_selftest, worker as W
    calls = []

    def fake_run():
        calls.append(1)
        return {"ok": True, "step": "done", "gpu": "G", "speed_x": 2.0, "load_s": 1.0, "error": ""}
    monkeypatch.setattr(gpu_selftest, "run", fake_run)
    monkeypatch.setattr(W.Worker, "run_forever", lambda self: None)
    monkeypatch.delenv("AFIX_GPU_SELFTEST", raising=False)
    monkeypatch.setattr(sys, "argv", ["worker", "--device", "gpu", "--data", str(tmp_path)])
    W.main()
    conn = db.connect(tmp_path / "afix.db")
    assert calls == [1] and db.get_module_state(conn, "denoise")["state"] == "installed"
    monkeypatch.setattr(sys, "argv", ["worker", "--device", "cpu", "--data", str(tmp_path)])
    W.main()
    monkeypatch.setenv("AFIX_GPU_SELFTEST", "0")
    monkeypatch.setattr(sys, "argv", ["worker", "--device", "gpu", "--data", str(tmp_path)])
    W.main()
    assert calls == [1]  # not for cpu workers, nor when the core tests the container it started


def test_gpu_self_test_crash_is_recorded_not_raised(tmp_path):
    w = Worker(tmp_path, "gpu", "g", poll=0)

    def boom():
        raise RuntimeError("driver exploded")
    w.report_gpu_selftest(selftest=boom)
    s = db.get_module_state(w.conn, "denoise")
    assert s["state"] == "failed" and s["detail"]["code"] == "selftest_failed" and "exploded" in s["detail"]["error"]
