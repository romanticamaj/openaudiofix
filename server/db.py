"""SQLite (WAL) store: projects, the job queue, worker heartbeats, GPU module state."""
import json
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL,
  sr INTEGER, channels INTEGER, duration_s REAL, codec TEXT, lossy INTEGER, has_video INTEGER,
  subtype TEXT, wav_original INTEGER, original_file TEXT, streams TEXT, stream INTEGER,
  modules TEXT, scan TEXT, result TEXT, error TEXT, params TEXT, export_dir TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, kind TEXT NOT NULL, module TEXT,
  seq INTEGER NOT NULL DEFAULT 0, device TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  worker TEXT, heartbeat REAL, pct REAL, message TEXT, error TEXT, result TEXT, cancel INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL, started REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, device, id);
CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, device TEXT NOT NULL, heartbeat REAL NOT NULL, info TEXT);
CREATE TABLE IF NOT EXISTS modules_state (id TEXT PRIMARY KEY, state TEXT NOT NULL, step TEXT, detail TEXT, updated REAL);
"""
JSON_COLS = {"streams", "modules", "params", "scan", "result", "error", "info", "detail"}
BOOL_COLS = {"lossy", "has_video", "wav_original"}
PROJECT_COLS = {"id", "name", "created", "state", "sr", "channels", "duration_s", "codec", "lossy", "has_video",
                "subtype", "wav_original", "original_file", "streams", "stream", "modules", "scan", "result", "error", "params",
                "export_dir"}


@contextmanager
def immediate(conn):
    """One write transaction (BEGIN IMMEDIATE takes the write lock up front); rolled back on any error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _check_cols(fields):
    bad = set(fields) - PROJECT_COLS
    if bad:
        raise ValueError(f"unknown project column(s): {sorted(bad)}")


def connect(path):
    """Open the store. A connection must not be used by several threads at once: give each thread its own."""
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    for col in ("params", "export_dir"):  # stores made before module settings / export folders existed
        if col not in cols:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {col} TEXT")
    return conn


def _enc(k, v):
    if k in JSON_COLS and v is not None:
        return json.dumps(v, ensure_ascii=False)
    if k in BOOL_COLS and v is not None:
        return int(bool(v))
    return v


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if d[k] is not None:
            d[k] = json.loads(d[k])
    for k in BOOL_COLS & d.keys():
        if d[k] is not None:
            d[k] = bool(d[k])
    return d


# ---------------------------------------------------------------- projects
def create_project(conn, pid, name, **fields):
    fields = dict(fields, id=pid, name=name, created=fields.get("created", time.time()),
                  state=fields.get("state", "scanning"))
    _check_cols(fields)
    cols = ", ".join(fields)
    conn.execute(f"INSERT INTO projects ({cols}) VALUES ({', '.join('?' * len(fields))})",
                 [_enc(k, v) for k, v in fields.items()])


def update_project(conn, pid, **fields):
    _check_cols(fields)
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE projects SET {sets} WHERE id=?", [_enc(k, v) for k, v in fields.items()] + [pid])


def get_project(conn, pid):
    return _row(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())


def list_projects(conn):
    return [_row(r) for r in conn.execute("SELECT * FROM projects ORDER BY created DESC")]


def delete_project(conn, pid):
    """The project row and its jobs go together (a running worker's guarded writes then change nothing)."""
    with immediate(conn):
        conn.execute("DELETE FROM jobs WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM projects WHERE id=?", (pid,))


# ---------------------------------------------------------------- jobs
def enqueue(conn, project_id, kind, device, module=None, seq=0):
    cur = conn.execute("INSERT INTO jobs (project_id, kind, module, seq, device, state, created) "
                       "VALUES (?, ?, ?, ?, ?, 'queued', ?)", (project_id, kind, module, seq, device, time.time()))
    return cur.lastrowid


def claim(conn, device, worker_id):
    """Atomically claim the oldest queued job for `device` (one UPDATE ... RETURNING, spec 1.4).

    The sub-select and the update run inside one statement under SQLite's write lock, so two
    workers can never claim the same row.
    """
    now = time.time()
    rows = conn.execute(
        "UPDATE jobs SET state='running', worker=?, heartbeat=?, started=?, attempts=attempts+1, "
        "pct=NULL, message=NULL "
        "WHERE state='queued' AND id=(SELECT id FROM jobs WHERE state='queued' AND device=? ORDER BY id LIMIT 1) "
        "RETURNING *", (worker_id, now, now, device)).fetchall()
    return _row(rows[0]) if rows else None


def _own(worker_id):
    """SQL guard: the job must still be running, and owned by `worker_id` when given."""
    return ("AND state='running'", ()) if worker_id is None else ("AND state='running' AND worker=?", (worker_id,))


def report_progress(conn, job_id, pct=None, message=None, worker_id=None):
    """Record progress + heartbeat. Returns True when the worker should stop: cancel was requested,
    or the job is no longer this worker's running job (requeued, cancelled, finished).
    A message without a percentage keeps the last percentage."""
    guard, args = _own(worker_id)
    n = conn.execute(f"UPDATE jobs SET pct=COALESCE(?, pct), message=?, heartbeat=? WHERE id=? {guard}",
                     (pct, message, time.time(), job_id, *args)).rowcount
    if n == 0:
        return True
    r = conn.execute("SELECT cancel FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(r and r["cancel"])


def touch(conn, job_id, worker_id=None):
    """Heartbeat only. Returns False if the job is no longer this worker's running job."""
    guard, args = _own(worker_id)
    return conn.execute(f"UPDATE jobs SET heartbeat=? WHERE id=? {guard}",
                        (time.time(), job_id, *args)).rowcount == 1


def finish(conn, job_id, state, error=None, result=None, worker_id=None):
    """Move a running job to a terminal state. Returns False (and changes nothing) if the job is
    not running any more, or belongs to another worker."""
    guard, args = _own(worker_id)
    return conn.execute(f"UPDATE jobs SET state=?, error=?, result=?, finished=? WHERE id=? {guard}",
                        (state, _enc("error", error), _enc("result", result), time.time(), job_id, *args)
                        ).rowcount == 1


def get_job(conn, job_id):
    return _row(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def jobs_for(conn, project_id):
    return [_row(r) for r in conn.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY id", (project_id,))]


def step_rates(conn, per_step=20):
    """Seconds each step took per second of audio, as the median of its last `per_step` finished runs.

    Keys are "kind:module" ("repair:denoise", "finalize:"). The UI multiplies them by a project's duration to
    estimate the time left before a step has reported any progress (a GPU step reports only per channel).
    """
    rows = conn.execute(
        "SELECT j.kind, COALESCE(j.module, ''), (j.finished - j.started) / p.duration_s FROM jobs j "
        "JOIN projects p ON p.id = j.project_id WHERE j.state = 'done' AND j.started IS NOT NULL "
        "AND j.finished IS NOT NULL AND p.duration_s > 0 ORDER BY j.id DESC").fetchall()
    seen = {}
    for kind, module, rate in rows:
        xs = seen.setdefault(f"{kind}:{module}", [])
        if len(xs) < per_step:
            xs.append(rate)
    return {k: round(sorted(xs)[len(xs) // 2], 5) for k, xs in seen.items()}


def run_seconds(conn):
    """{project id: seconds} its finished repair and finalize steps took (the scan is not counted)."""
    rows = conn.execute("SELECT project_id, SUM(finished - started) FROM jobs WHERE state = 'done' AND kind != 'scan' "
                        "AND started IS NOT NULL AND finished IS NOT NULL GROUP BY project_id").fetchall()
    return {pid: round(secs, 2) for pid, secs in rows}


def clear_jobs(conn, project_id):
    conn.execute("DELETE FROM jobs WHERE project_id=?", (project_id,))


def requeue_stale(conn, timeout=60.0, max_attempts=2):
    """Handle running jobs whose heartbeat is older than `timeout`. Each transition is one guarded
    UPDATE ... RETURNING, so a job finished meanwhile is never touched."""
    now = time.time()
    cutoff = now - timeout
    stale = "WHERE state='running' AND heartbeat < ?"
    err = {"code": "worker_lost", "message": "The worker stopped responding twice while running this step.",
           "hint": "Check the server log; the machine may be running out of memory."}
    out = []
    for state, sql, args in (
        ("cancelled", f"UPDATE jobs SET state='cancelled', finished=? {stale} AND cancel=1",
         (now, cutoff)),
        ("queued", f"UPDATE jobs SET state='queued', worker=NULL {stale} AND cancel=0 AND attempts < ?",
         (cutoff, max_attempts)),
        ("failed", f"UPDATE jobs SET state='failed', error=?, finished=? {stale} AND cancel=0 AND attempts >= ?",
         (json.dumps(err), now, cutoff, max_attempts)),
    ):
        for r in conn.execute(sql + " RETURNING id, project_id", args).fetchall():
            out.append({"id": r["id"], "project_id": r["project_id"], "state": state})
    return out


def request_cancel(conn, project_id):
    """Cancel a project's queued jobs and flag its running one, in one transaction, so a worker can't commit
    "step done, next step queued" in between. With nothing left running, a scanning/processing project
    becomes cancelled here; otherwise the worker records it when the flagged step stops."""
    with immediate(conn):
        n = conn.execute("UPDATE jobs SET state='cancelled', finished=? WHERE project_id=? AND state='queued'",
                         (time.time(), project_id)).rowcount
        running = conn.execute("UPDATE jobs SET cancel=1 WHERE project_id=? AND state='running'",
                               (project_id,)).rowcount
        p = conn.execute("SELECT state FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is not None and p["state"] in ("scanning", "processing") and not running:
            conn.execute("UPDATE projects SET state='cancelled' WHERE id=?", (project_id,))
    return n + running


def retry_failed(conn, project_id):
    r = conn.execute("SELECT id FROM jobs WHERE project_id=? AND state IN ('failed','cancelled') "
                     "ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()
    if r is None:
        return None
    conn.execute("UPDATE jobs SET state='queued', attempts=0, error=NULL, cancel=0, worker=NULL, pct=NULL, "
                 "message=NULL WHERE id=?", (r["id"],))
    return get_job(conn, r["id"])


# ---------------------------------------------------------------- workers / modules
def worker_beat(conn, worker_id, device, info=None):
    conn.execute("INSERT INTO workers (id, device, heartbeat, info) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat, info=excluded.info",
                 (worker_id, device, time.time(), _enc("info", info or {})))


def live_workers(conn, device, within=30.0):
    return [_row(r) for r in conn.execute("SELECT * FROM workers WHERE device=? AND heartbeat > ?",
                                          (device, time.time() - within))]


def get_module_state(conn, mid):
    r = _row(conn.execute("SELECT * FROM modules_state WHERE id=?", (mid,)).fetchone())
    return r or {"id": mid, "state": "not_installed", "step": None, "detail": {}, "updated": None}


def set_module_state(conn, mid, state, step=None, detail=None):
    conn.execute("INSERT INTO modules_state (id, state, step, detail, updated) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(id) DO UPDATE SET state=excluded.state, step=excluded.step, detail=excluded.detail, "
                 "updated=excluded.updated", (mid, state, step, _enc("detail", detail or {}), time.time()))
