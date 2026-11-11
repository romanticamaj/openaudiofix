import ast
import pathlib
import threading
import time

import pytest

from openaudiofix import errors as core_errors
from server import db, errors


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "afix.db")


def test_project_roundtrip_with_json_fields(conn):
    db.create_project(conn, "p1", "a.wav", state="scanning", modules=["glitch"], lossy=False)
    db.update_project(conn, "p1", scan={"glitch": {"count": 3}}, state="ready")
    p = db.get_project(conn, "p1")
    assert p["modules"] == ["glitch"] and p["scan"]["glitch"]["count"] == 3 and p["state"] == "ready"
    assert [x["id"] for x in db.list_projects(conn)] == ["p1"]
    db.delete_project(conn, "p1")
    assert db.get_project(conn, "p1") is None


def test_claim_is_fifo_per_device_and_exclusive(conn):
    a = db.enqueue(conn, "p", "repair", "cpu", "glitch", 0)
    g = db.enqueue(conn, "p", "repair", "gpu", "denoise", 1)
    b = db.enqueue(conn, "q", "scan", "cpu")
    j1 = db.claim(conn, "cpu", "w1")
    j2 = db.claim(conn, "cpu", "w2")
    assert (j1["id"], j2["id"]) == (a, b) and j1["attempts"] == 1
    assert j1["state"] == "running" and j1["worker"] == "w1"
    assert db.claim(conn, "cpu", "w3") is None
    assert db.claim(conn, "gpu", "g1")["id"] == g


def test_concurrent_claims_never_return_the_same_job(tmp_path):
    path = tmp_path / "afix.db"
    setup = db.connect(path)
    n = 60
    ids = {db.enqueue(setup, "p", "repair", "cpu", "glitch", i) for i in range(n)}
    claimed = {"a": [], "b": []}
    start = threading.Barrier(2)

    def work(name):
        c = db.connect(path)  # separate connection per worker
        start.wait()
        while True:
            j = db.claim(c, "cpu", name)
            if j is None:
                return
            claimed[name].append(j["id"])

    threads = [threading.Thread(target=work, args=(k,)) for k in claimed]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    both = claimed["a"] + claimed["b"]
    assert len(both) == len(set(both)) == n and set(both) == ids
    assert all(db.get_job(setup, i)["attempts"] == 1 for i in ids)


def test_progress_reports_cancel_request(conn):
    j = db.enqueue(conn, "p", "repair", "cpu", "declip")
    db.claim(conn, "cpu", "w")
    assert db.report_progress(conn, j, 10.0, "x") is False
    db.request_cancel(conn, "p")
    assert db.report_progress(conn, j, 20.0, "x") is True
    assert db.get_job(conn, j)["pct"] == 20.0


def test_a_message_without_a_percentage_keeps_the_percentage(conn):
    j = db.enqueue(conn, "p", "repair", "cpu", "speech")
    db.claim(conn, "cpu", "w")
    db.report_progress(conn, j, 40.0, "detecting speech")
    db.report_progress(conn, j, None, "downloading the model")
    job = db.get_job(conn, j)
    assert (job["pct"], job["message"]) == (40.0, "downloading the model")


def test_cancel_marks_queued_jobs_cancelled_immediately(conn):
    j = db.enqueue(conn, "p", "repair", "cpu", "declip")
    assert db.request_cancel(conn, "p") == 1
    assert db.get_job(conn, j)["state"] == "cancelled"


def test_requeue_stale_retries_once_then_fails(conn):
    j = db.enqueue(conn, "p", "repair", "cpu", "declip")
    db.claim(conn, "cpu", "w")
    conn.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (time.time() - 120, j))
    assert db.requeue_stale(conn) == [{"id": j, "project_id": "p", "state": "queued"}]
    db.claim(conn, "cpu", "w")
    conn.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (time.time() - 120, j))
    assert db.requeue_stale(conn) == [{"id": j, "project_id": "p", "state": "failed"}]
    assert db.get_job(conn, j)["error"]["code"] == "worker_lost"


def test_fresh_heartbeat_is_not_requeued(conn):
    j = db.enqueue(conn, "p", "repair", "cpu", "declip")
    db.claim(conn, "cpu", "w")
    db.touch(conn, j)
    assert db.requeue_stale(conn) == []
    assert db.get_job(conn, j)["state"] == "running"


def test_retry_failed_resets_the_failed_job(conn):
    j = db.enqueue(conn, "p", "repair", "cpu", "declip")
    db.claim(conn, "cpu", "w")
    db.finish(conn, j, "failed", error={"code": "internal_error", "message": "boom", "hint": ""})
    r = db.retry_failed(conn, "p")
    assert r["id"] == j and db.get_job(conn, j)["state"] == "queued" and db.get_job(conn, j)["attempts"] == 0


def test_worker_heartbeat_and_module_state(conn):
    db.worker_beat(conn, "g1", "gpu", {"gpu": "RTX"})
    assert db.live_workers(conn, "gpu")[0]["info"]["gpu"] == "RTX"
    assert db.get_module_state(conn, "denoise")["state"] == "not_installed"
    db.set_module_state(conn, "denoise", "downloading", "download", {"pct": 12.5})
    s = db.get_module_state(conn, "denoise")
    assert s["state"] == "downloading" and s["detail"]["pct"] == 12.5


def test_api_error_body_and_core_error_mapping():
    e = errors.ApiError(404, "project_not_found", "No such project", "Reload the list")
    assert e.body() == {"code": "project_not_found", "message": "No such project", "hint": "Reload the list"}
    # every code a core AfixError subclass carries must be a server code
    subclasses = [v for v in vars(core_errors).values()
                  if isinstance(v, type) and issubclass(v, core_errors.AfixError)]
    assert len(subclasses) >= 7
    for cls in subclasses:
        assert cls.code in errors.CODES, cls
        api = errors.from_afix(cls("m", "h"))
        assert api.body() == {"code": cls.code, "message": "m", "hint": "h"} and api.status == 422
    # codes raised literally in the core package (e.g. io.py) are covered too
    for f in pathlib.Path(core_errors.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg == "code" and isinstance(node.value, ast.Constant):
                assert node.value.value in errors.CODES, (f.name, node.value.value)
    # unknown codes degrade to internal_error
    assert errors.from_afix(core_errors.AfixError("x", code="weird")).code == "internal_error"


def _stale(conn, j):
    conn.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (time.time() - 120, j))


def test_requeue_does_not_resurrect_a_job_finished_after_going_stale(conn):
    j = db.enqueue(conn, "p", "repair", "cpu", "declip")
    db.claim(conn, "cpu", "w")
    _stale(conn, j)
    assert db.finish(conn, j, "done", result={"ok": 1}, worker_id="w") is True  # worker finishes first
    assert db.requeue_stale(conn) == []
    assert db.get_job(conn, j)["state"] == "done"


def test_zombie_worker_cannot_touch_a_reclaimed_job(conn):
    j = db.enqueue(conn, "p", "repair", "cpu", "declip")
    db.claim(conn, "cpu", "w1")
    _stale(conn, j)
    db.requeue_stale(conn)
    assert db.claim(conn, "cpu", "w2")["id"] == j
    assert db.finish(conn, j, "done", worker_id="w1") is False
    assert db.touch(conn, j, worker_id="w1") is False
    assert db.report_progress(conn, j, 50.0, "x", worker_id="w1") is True  # told to stop
    job = db.get_job(conn, j)
    assert (job["state"], job["worker"], job["pct"]) == ("running", "w2", None)
    assert db.finish(conn, j, "done", worker_id="w2") is True


def test_finish_cannot_overwrite_cancelled_or_queued(conn):
    j = db.enqueue(conn, "p", "repair", "cpu", "declip")
    assert db.finish(conn, j, "done") is False and db.get_job(conn, j)["state"] == "queued"
    assert db.report_progress(conn, j, 5.0) is True and db.get_job(conn, j)["heartbeat"] is None
    db.request_cancel(conn, "p")
    assert db.finish(conn, j, "done") is False and db.get_job(conn, j)["state"] == "cancelled"


def test_stale_job_with_cancel_requested_becomes_cancelled(conn):
    j = db.enqueue(conn, "p", "repair", "cpu", "declip")
    db.claim(conn, "cpu", "w")
    db.request_cancel(conn, "p")
    _stale(conn, j)
    assert db.requeue_stale(conn) == [{"id": j, "project_id": "p", "state": "cancelled"}]
    assert db.get_job(conn, j)["state"] == "cancelled" and db.claim(conn, "cpu", "w2") is None


def test_unknown_project_column_is_rejected(conn):
    with pytest.raises(ValueError):
        db.create_project(conn, "p", "a.wav", bogus=1)
    db.create_project(conn, "p", "a.wav")
    with pytest.raises(ValueError):
        db.update_project(conn, "p", **{"state=1, id": 2})


def test_a_store_from_before_module_settings_gains_the_params_column(tmp_path):
    import sqlite3
    old = sqlite3.connect(str(tmp_path / "old.db"))
    old.executescript(db.SCHEMA.replace(", params TEXT", ""))
    old.close()
    conn = db.connect(tmp_path / "old.db")
    assert "params" in {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    db.connect(tmp_path / "old.db")  # a second open does not try to add it again


def test_step_rates_are_the_median_seconds_per_audio_second_of_finished_runs(conn):
    for i, (secs, dur) in enumerate([(10, 100), (30, 100), (20, 100)]):
        db.create_project(conn, f"p{i}", "a.wav", state="done", duration_s=dur)
        j = db.enqueue(conn, f"p{i}", "repair", "gpu", module="denoise")
        conn.execute("UPDATE jobs SET state='done', started=?, finished=? WHERE id=?", (1000.0, 1000.0 + secs, j))
        f = db.enqueue(conn, f"p{i}", "finalize", "cpu")
        conn.execute("UPDATE jobs SET state='done', started=?, finished=? WHERE id=?", (2000.0, 2000.0 + 1, f))
    # a run that never finished, or a project with no length, does not count
    db.create_project(conn, "px", "b.wav", state="failed", duration_s=0)
    k = db.enqueue(conn, "px", "repair", "gpu", module="denoise")
    conn.execute("UPDATE jobs SET state='done', started=0, finished=999 WHERE id=?", (k,))
    db.enqueue(conn, "p0", "repair", "cpu", module="speech")
    assert db.step_rates(conn) == {"repair:denoise": 0.2, "finalize:": 0.01}


def test_run_seconds_adds_up_a_projects_finished_steps_but_not_the_scan(conn):
    db.create_project(conn, "p1", "a.wav", state="done")
    for kind, secs in (("scan", 100), ("repair", 30), ("finalize", 2)):
        j = db.enqueue(conn, "p1", kind, "cpu")
        conn.execute("UPDATE jobs SET state='done', started=0, finished=? WHERE id=?", (secs, j))
    db.enqueue(conn, "p1", "repair", "cpu")  # queued: not counted
    assert db.run_seconds(conn) == {"p1": 32}
