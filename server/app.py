"""OpenAudioFix HTTP API.  uvicorn server.app:create_app --factory"""
import asyncio
import json
import logging
import math
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

import numpy as np
import soundfile as sf
from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.exceptions import HTTPException as StarletteHTTPException

from openaudiofix import chain, io as aio, modules, paths
from openaudiofix.errors import InputError

from . import db, fsbrowse, locations, visual
from .errors import ApiError, from_afix
from .gpu_manager import ACTIONS, TERMINAL
from .migrate import Copier, tree_size
from .ranges import file_response
from .storage import PID_RE, Storage, estimate_bytes, write_text_atomic

log = logging.getLogger("openaudiofix.api")

MAX_PEAK_SPAN_S = 120.0
MAX_PEAK_BUCKETS = 4000
DEFAULT_STAGES = 3
MAX_FIELD_BYTES = 1024
SRC_RE = re.compile(r"^(original|final|d_chain|s\d{1,2}|d\d{1,2})$")
UNSAFE_NAME_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*%]')


class NullGpu:
    def state(self):
        return {"id": "denoise", "state": "not_installed", "step": None, "detail": {}}

    def busy(self):
        return False

    def tick(self):
        pass

    def startup(self):
        pass

    def start(self, action):
        return False

    def shutdown(self):
        pass


class RunBody(BaseModel):
    modules: list[str]
    params: dict[str, dict] = {}


def check_params(module, given):
    """Validate one module's settings against its `params` schema (the small subset modules use:
    integer/number with minimum/maximum, string enum, boolean; no unknown keys). -> the settings, defaults filled."""
    props = module.params.get("properties", {})
    out = {}
    for k, v in given.items():
        spec = props.get(k)
        if spec is None:
            raise ApiError(400, "bad_request", f"{module.id}: unknown setting {k!r}.")
        t = spec.get("type")
        ok = {"integer": isinstance(v, int) and not isinstance(v, bool),
              "number": isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v),
              "string": isinstance(v, str), "boolean": isinstance(v, bool)}.get(t, False)
        if ok and "enum" in spec:
            ok = v in spec["enum"]
        if ok and t in ("integer", "number"):
            ok = spec.get("minimum", -math.inf) <= v <= spec.get("maximum", math.inf)
        if not ok:
            raise ApiError(400, "bad_request", f"{module.id}: invalid value for {k!r}.")
        out[k] = v
    return {k: spec["default"] for k, spec in props.items() if "default" in spec} | out


class RenameBody(BaseModel):
    name: str | None = None
    export_dir: str | None = None
    clear_export_dir: bool = False


class SettingsBody(BaseModel):
    export_dir: str | None = None


class MkdirBody(BaseModel):
    path: str
    name: str


class MoveBody(BaseModel):
    what: str
    path: str


class StreamBody(BaseModel):
    index: int


class ExportBody(BaseModel):
    float: bool = False


def disk_full(need):
    return ApiError(507, "disk_full", f"About {need / 1e9:.1f} GB is needed; not enough free disk space.",
                    "Delete old projects or free space on the data disk.")


MEDIA_EXTS = frozenset((".wav", ".flac", ".mp3", ".m4a", ".mp4", ".mkv", ".mov", ".aac", ".ogg", ".opus",
                        ".aif", ".aiff", ".wma", ".webm", ".wave", ".m4v", ".avi", ".3gp", ".caf", ".ac3"))


LOOPBACK = {"127.0.0.1", "::1", "localhost"}
RESTART_CODE = 75  # the launcher restarts the server when its web process exits with this


def folder_opener():
    """True when "open the export folder" can show a window to the person at the browser: the server runs on
    their own desktop, not in a container. AFIX_OPEN_FOLDER=0 turns it off, =1 forces it on."""
    flag = os.environ.get("AFIX_OPEN_FOLDER", "auto")
    if flag in ("0", "1"):
        return flag == "1"
    return not Path("/.dockerenv").exists()


def can_open_folder(request):
    return folder_opener() and request.client is not None and request.client.host in LOOPBACK


in_container = locations.in_container


def can_browse(request):
    """Folders can be chosen from a browser on the server's own computer, or anywhere in a container (where the
    listing is confined to the mounted folders)."""
    if in_container():
        return True
    return request.client is not None and request.client.host in LOOPBACK


def open_folder(path):
    """Show `path` in the desktop's file manager."""
    import subprocess
    import sys
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - a folder this server created
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def export_stem(name):
    """Base name for export files: no path or reserved characters (and no '%', which the file resolver refuses).

    Only a known media extension is dropped, so a typed name such as "Interview v2.1" keeps its dots."""
    root, ext = os.path.splitext(name)
    stem = root if ext.lower() in MEDIA_EXTS else name
    stem = UNSAFE_NAME_RE.sub("_", stem).strip(" .")
    return stem or "audio"


def write_removed(dst, before, speech, sr, info, reduce_gain, float_out):
    """What the speech step took out, at the level and in the format of the exported speech file, so the two
    tracks played together are the speech step's input."""
    speech = np.asarray(speech, dtype=np.float64)
    # the exact gain aio.export applied (info["gain_db"] is rounded for display)
    gain_db = aio.peak_gain_db(speech) if reduce_gain and not float_out else 0.0
    d = (np.asarray(before, dtype=np.float64) - speech) * 10 ** (gain_db / 20)
    sf.write(str(dst), d[:, 0] if d.shape[1] == 1 else d, sr, subtype=info["subtype"])


def _health(st):
    checks = []
    try:
        probe = st.root / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        checks.append({"id": "data_writable", "ok": True, "message": ""})
    except OSError as e:
        checks.append({"id": "data_writable", "ok": False, "message": str(e)})
    try:
        c = db.connect(st.db_path)
        try:
            c.execute("SELECT 1")
        finally:
            c.close()
        checks.append({"id": "sqlite", "ok": True, "message": ""})
    except Exception as e:
        checks.append({"id": "sqlite", "ok": False, "message": str(e)})
    ok = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    checks.append({"id": "ffmpeg", "ok": ok, "message": "" if ok else "ffmpeg/ffprobe not found on PATH"})
    return checks


async def receive_upload(request, st, limit):
    """Stream a multipart body without buffering it: the `file` part is written straight to data/tmp.

    Returns (tmp_path, filename, fields). The temp file is removed if anything goes wrong; more than
    `limit` bytes of file data is refused with disk_full.
    """
    ctype, params = parse_options_header(request.headers.get("content-type"))
    if ctype != b"multipart/form-data" or not params.get(b"boundary"):
        raise ApiError(400, "bad_request", "Send the audio as multipart/form-data in a field named 'file'.")
    state = {"name": b"", "filename": None, "hname": b"", "hval": b"", "disp": b"", "target": None}
    fields, pending = {}, []
    out = {"path": None, "filename": None, "f": None, "size": 0}

    def on_part_begin():
        state.update(disp=b"", filename=None, target=None)

    def on_header_field(data, start, end):
        state["hname"] += data[start:end]

    def on_header_value(data, start, end):
        state["hval"] += data[start:end]

    def on_header_end():
        if state["hname"].lower() == b"content-disposition":
            state["disp"] = state["hval"]
        state["hname"] = state["hval"] = b""

    def on_headers_finished():
        _, opts = parse_options_header(state["disp"])
        name = opts.get(b"name", b"").decode("utf-8", "replace")
        if b"filename" in opts:
            if name == "file" and out["path"] is None:
                out["filename"] = opts[b"filename"].decode("utf-8", "replace")
                out["path"] = st.upload_tmp_path(out["filename"])
                out["f"] = open(out["path"], "wb")
                state["target"] = "file"
            else:
                state["target"] = None  # extra file parts are ignored
        else:
            state["target"] = name
            fields[name] = b""

    def on_part_data(data, start, end):
        t = state["target"]
        if t == "file":
            pending.append(data[start:end])
        elif t is not None:
            if len(fields[t]) + end - start > MAX_FIELD_BYTES:
                raise ApiError(400, "bad_request", f"Form field {t!r} is too large.")
            fields[t] += data[start:end]

    parser = MultipartParser(params[b"boundary"], {
        "on_part_begin": on_part_begin, "on_header_field": on_header_field, "on_header_value": on_header_value,
        "on_header_end": on_header_end, "on_headers_finished": on_headers_finished, "on_part_data": on_part_data})
    try:
        try:
            async for chunk in request.stream():
                parser.write(chunk)
                if pending:
                    blob = b"".join(pending)
                    pending.clear()
                    out["size"] += len(blob)
                    if out["size"] > limit:
                        raise disk_full(out["size"])
                    await run_in_threadpool(out["f"].write, blob)
            parser.finalize()
        except MultipartParseError as e:
            raise ApiError(400, "bad_request", f"The upload is not valid multipart data: {e}") from None
        finally:
            if out["f"] is not None:
                out["f"].close()
        if out["path"] is None:
            raise ApiError(400, "bad_request", "No file was uploaded (expected a form field named 'file').")
    except BaseException:
        if out["path"] is not None:
            out["path"].unlink(missing_ok=True)
        raise
    return out["path"], out["filename"], {k: v.decode("utf-8", "replace") for k, v in fields.items()}


TICK_EVERY_S = 10
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def _hostname(host):
    """'Example:8765' -> 'example', '[::1]:8765' -> '[::1]' (lowercase, port dropped)."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[: host.find("]") + 1] if "]" in host else host
    return host.split(":", 1)[0]


def allowed_hosts():
    extra = [_hostname(h) for h in os.environ.get("AFIX_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return set(LOCAL_HOSTS) | set(extra)


class LocalOnlyMiddleware:
    """Keep other websites out of the local API (DNS rebinding and cross-site requests).

    Like Starlette's TrustedHostMiddleware, the Host header must name an allowed host (127.0.0.1, localhost,
    [::1] and AFIX_ALLOWED_HOSTS); unlike it, IPv6 literals work and refusals use the {code, message, hint}
    shape. A state-changing request (POST/PUT/PATCH/DELETE) that carries an Origin header must come from the
    page's own origin (Origin host:port == Host). Requests without Origin (curl, scripts) are not affected.
    """

    def __init__(self, app, hosts):
        self.app, self.hosts = app, frozenset(hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        err = None
        if _hostname(host) not in self.hosts:
            err = ApiError(400, "forbidden_origin", f"Host {host!r} is not allowed.",
                           "Open the UI at http://127.0.0.1:8765/, or add this name to AFIX_ALLOWED_HOSTS.")
        elif scope["method"] in STATE_CHANGING and "origin" in headers:
            origin = headers["origin"]
            o = urlsplit(origin) if origin != "null" else None
            if o is None or o.scheme not in ("http", "https") or o.netloc.lower() != host.strip().lower():
                err = ApiError(403, "forbidden_origin", f"Requests from {origin} are not accepted.",
                               "Use the OpenAudioFix page itself; other websites cannot change your projects.")
        if err is None:
            return await self.app(scope, receive, send)
        await JSONResponse(err.body(), status_code=err.status)(scope, receive, send)


async def _tick_loop(gpu):
    """Periodically let the GPU manager start again a GPU worker that died (runs in a thread: it touches SQLite)."""
    while True:
        await asyncio.sleep(TICK_EVERY_S)
        try:
            await run_in_threadpool(gpu.tick)
        except Exception:
            log.exception("GPU module tick failed")


def create_app(data_dir=None, gpu=None):
    st = Storage(data_dir or os.environ.get("AFIX_DATA") or locations.default_data())
    local = threading.local()

    def cx():
        """One SQLite connection per thread (endpoints run in a thread pool)."""
        c = getattr(local, "conn", None)
        if c is None:
            c = local.conn = db.connect(st.db_path)
        return c

    cx()  # create the schema now
    gpu = gpu if gpu is not None else _default_gpu(st)

    @asynccontextmanager
    async def lifespan(app):
        for leftover in st.tmp_dir.glob("upload-*"):  # interrupted uploads from a previous run
            leftover.unlink(missing_ok=True)
        app.state.gpu.startup()
        ticker = asyncio.create_task(_tick_loop(app.state.gpu))
        try:
            yield
        finally:
            ticker.cancel()
            app.state.gpu.shutdown()  # the GPU worker is this process's child

    app = FastAPI(title="OpenAudioFix", version="0.1.1", lifespan=lifespan)
    app.add_middleware(LocalOnlyMiddleware, hosts=allowed_hosts())
    app.state.storage, app.state.db = st, cx
    app.state.gpu = gpu
    app.state.checks = _health(st)

    # every error, including FastAPI's own, leaves as {code, message, hint}
    @app.exception_handler(ApiError)
    async def _api_error(request, e):
        return JSONResponse(e.body(), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request, e):
        parts = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()))
            parts.append(f"{loc}: {err.get('msg', 'invalid')}" if loc else str(err.get("msg", "invalid")))
        return JSONResponse(ApiError(400, "bad_request", "Invalid request: " + "; ".join(parts)).body(),
                            status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request, e):
        code = "file_not_found" if e.status_code == 404 else "invalid_state"
        body = ApiError(e.status_code, code, str(e.detail) if e.detail else "Request refused.").body()
        return JSONResponse(body, status_code=e.status_code, headers=getattr(e, "headers", None))

    @app.exception_handler(Exception)
    async def _unexpected(request, e):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        body = ApiError(500, "internal_error", f"{type(e).__name__}: {e}",
                        "This is a bug; please report it with the server log.").body()
        return JSONResponse(body, status_code=500)

    def project_or_404(pid):
        p = db.get_project(cx(), pid) if isinstance(pid, str) and PID_RE.fullmatch(pid) else None
        if p is None:
            raise ApiError(404, "project_not_found", f"Project {pid} does not exist.")
        return p

    def done_or_409(pid):
        p = project_or_404(pid)
        if p["state"] != "done":
            raise ApiError(409, "invalid_state", "Results are not ready yet.")
        return p

    def with_size(p, jobs=True):
        # now: the server clock, so the UI can show how long a running step has taken without trusting its own
        p = dict(p, size_bytes=st.size(p["id"]) if st.pdir(p["id"]).exists() else 0, now=round(time.time(), 3))
        if jobs or p["state"] in ("scanning", "processing"):  # the list shows progress for busy projects
            p["jobs"] = db.jobs_for(cx(), p["id"])
        if p["state"] in ("scanning", "processing"):
            p["rates"] = db.step_rates(cx())
        return p

    copier = Copier()
    app.state.copier = copier

    def browse_roots():
        """The folders a container may use; None (anything) outside a container."""
        return fsbrowse.roots(st.root, True) if in_container() else None

    def check_folder(request, path):
        """An existing, writable folder the person may choose; returns its absolute path."""
        if not can_browse(request):
            raise ApiError(403, "not_local", "Folders can only be chosen on the computer running the server.")
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise ApiError(422, "bad_request", "Choose a full folder path.")
        if not fsbrowse.allowed(p, browse_roots()):
            raise ApiError(403, "not_allowed", "That folder is outside the folders this server may use.",
                           "In Docker, mount the folder and list it in AFIX_BROWSE_ROOTS.")
        if not p.is_dir():
            raise ApiError(404, "folder_not_found", f"The folder {p} does not exist.")
        if not os.access(p, os.W_OK):
            raise ApiError(403, "folder_read_only", f"The folder {p} cannot be written to.")
        return str(p.resolve())

    def export_target(p):
        """(folder, from) the export goes to: the project's own folder, else the default, else (None, None)."""
        if p.get("export_dir"):
            return p["export_dir"], "project"
        d = locations.read_settings(st.root)["export_dir"]
        return (d, "default") if d else (None, None)

    def data_bytes():
        return sum(st.size(x["id"]) for x in db.list_projects(cx()) if st.pdir(x["id"]).exists())

    def defaults():
        return {"data": str(Path(locations.default_data()).resolve()), "models": str(paths.preferred_models_dir()),
                "export": locations.default_export()}

    def in_temp(p):
        t = Path(tempfile.gettempdir()).resolve()
        p = Path(p).resolve()
        return p == t or t in p.parents

    def settings_out(request):
        from openaudiofix import speech as speech_core
        models = speech_core.model_dir()
        try:
            free = shutil.disk_usage(st.root).free
        except OSError:
            free = None
        return {
            **locations.read_settings(st.root),
            "data_dir": str(Path(st.root).resolve()), "models_dir": str(models.resolve()),
            "sources": locations.sources(), "data_bytes": data_bytes(),
            "models_bytes": tree_size(models) if models.is_dir() else 0, "free_bytes": free,
            "can_browse": can_browse(request), "can_open_folder": can_open_folder(request),
            "in_container": in_container(), "can_restart": os.environ.get("AFIX_LAUNCHER") == "1",
            "saved": locations.read_location(), "move": copier.state(), "defaults": defaults(),
            "in_temp": {"data": in_temp(st.root), "models": in_temp(models)},
        }

    @app.get("/api/settings")
    def get_settings(request: Request):
        return settings_out(request)

    @app.put("/api/settings")
    def put_settings(body: SettingsBody, request: Request):
        if body.export_dir and body.export_dir == defaults()["export"]:
            Path(body.export_dir).mkdir(parents=True, exist_ok=True)
        d = check_folder(request, body.export_dir) if body.export_dir else None
        locations.write_settings(st.root, export_dir=d)
        return settings_out(request)

    @app.post("/api/settings/open")
    def open_setting_folder(request: Request, what: str = Query(...)):
        if not can_open_folder(request):
            raise ApiError(403, "not_local", "Folders can only be opened on the computer running the server.")
        from openaudiofix import speech as speech_core
        d = {"data": Path(st.root), "models": speech_core.model_dir(),
             "export": Path(locations.read_settings(st.root)["export_dir"] or st.root)}.get(what)
        if d is None:
            raise ApiError(422, "bad_request", "Choose data, models or export.")
        d.mkdir(parents=True, exist_ok=True)
        open_folder(d)
        return {"folder": str(d.resolve())}

    @app.get("/api/fs/dirs")
    def fs_dirs(request: Request, path: str = ""):
        if not can_browse(request):
            raise ApiError(403, "not_local", "Folders can only be chosen on the computer running the server.")
        roots = fsbrowse.roots(st.root, in_container())
        if not path:
            return {"path": None, "parent": None, "dirs": [{"name": r, "path": r} for r in roots], "roots": roots,
                    "writable": False}
        if not fsbrowse.allowed(path, browse_roots()):
            raise ApiError(403, "not_allowed", "That folder is outside the folders this server may use.")
        near = fsbrowse.nearest_folder(path)
        if near is None or not fsbrowse.allowed(near, browse_roots()):
            raise ApiError(404, "folder_not_found", f"The folder {path} does not exist.")
        out = fsbrowse.list_dirs(near)
        if out["parent"] and not fsbrowse.allowed(out["parent"], browse_roots()):
            out["parent"] = None
        return {**out, "roots": roots}

    @app.post("/api/fs/mkdir")
    def fs_mkdir(body: MkdirBody, request: Request):
        parent = check_folder(request, body.path)
        name = body.name.strip()
        if not name or name in (".", "..") or re.search(r'[\\/:*?"<>|]', name):
            raise ApiError(422, "bad_request", 'Use a folder name without \\ / : * ? " < > |.')
        p = Path(parent) / name
        try:
            p.mkdir(exist_ok=True)
        except OSError as e:
            raise ApiError(403, "folder_read_only", f"Could not create {p}: {e}") from None
        return {**fsbrowse.list_dirs(p), "roots": fsbrowse.roots(st.root, in_container())}

    @app.post("/api/settings/move")
    def move_folder(body: MoveBody, request: Request):
        """Copy the work data or the models to a new, empty folder, used from the next start on."""
        if body.what not in ("data", "models"):
            raise ApiError(422, "bad_request", "Choose data or models.")
        if locations.sources().get(body.what) == "env":
            var = "AFIX_DATA" if body.what == "data" else "AFIX_MODELS"
            raise ApiError(409, "set_by_env", f"This folder is set by {var} when the server starts.",
                           f"Change {var} (in docker-compose.yml or the shell that starts the server) instead.")
        if body.path == defaults()[body.what]:
            Path(body.path).mkdir(parents=True, exist_ok=True)
        target = Path(check_folder(request, body.path))
        from openaudiofix import speech as speech_core
        src = Path(st.root if body.what == "data" else speech_core.model_dir()).resolve()
        if target == src or src in target.parents or target in src.parents:
            raise ApiError(422, "bad_request", "Choose a folder outside the current one.")
        if any(target.iterdir()):
            raise ApiError(409, "folder_not_empty", "Choose an empty folder (or create a new one).")
        if body.what == "data" and any(x["state"] in ("scanning", "processing") for x in db.list_projects(cx())):
            raise ApiError(409, "invalid_state", "Wait until the running projects finish.")
        save = lambda dst: locations.save_location(**{body.what: str(dst)})  # noqa: E731
        if not src.exists():
            save(target)
        elif not copier.start(body.what, src, target, save):
            raise ApiError(409, "invalid_state", "A folder is already being copied.")
        return settings_out(request)

    @app.post("/api/restart")
    def restart(request: Request):
        """Restart through the launcher, which reads the saved folders again."""
        if os.environ.get("AFIX_LAUNCHER") != "1":
            raise ApiError(409, "invalid_state", "Restart the server by hand: it was not started by the launcher.")
        if not (in_container() or (request.client and request.client.host in LOOPBACK)):
            raise ApiError(403, "not_local", "Only the computer running the server can restart it.")
        if copier.busy():
            raise ApiError(409, "invalid_state", "Wait until the copy finishes.")
        def bye():
            try:
                app.state.gpu.shutdown()  # the GPU worker is this process's child
            finally:
                os._exit(RESTART_CODE)
        threading.Timer(0.5, bye).start()
        return {"ok": True}

    def project_out(pid):
        return with_size(db.get_project(cx(), pid))

    def require_gpu(module_ids):
        """GPU steps need an installed module and a live GPU worker; otherwise their job would never be taken."""
        if not any(m.device == "gpu" for m in modules.all_modules() if m.id in module_ids):
            return
        if app.state.gpu.state()["state"] != "installed":
            raise ApiError(409, "module_not_installed", "Background repair needs the GPU module.",
                           "Install it from the Modules panel first.")
        if not db.live_workers(cx(), "gpu"):
            raise ApiError(409, "gpu_worker_offline", "The GPU worker is not running.",
                           "Re-test the GPU module in the Modules panel; it starts the worker again.")

    # ------------------------------------------------------------ health
    @app.get("/api/health")
    def health():
        app.state.checks = _health(st)
        return {"ok": all(c["ok"] for c in app.state.checks), "checks": app.state.checks}

    # ------------------------------------------------------------ projects
    @app.get("/api/projects")
    def list_projects():
        run = db.run_seconds(cx())
        return {"projects": [dict(with_size(p, jobs=False), run_s=run.get(p["id"]) if p["state"] == "done" else None)
                             for p in db.list_projects(cx())],
                "total_bytes": st.total_size(), "free_bytes": st.free_bytes()}

    @app.post("/api/projects")
    async def create_project(request: Request):
        # spec 4.1: refuse before reading the body when the upload alone cannot fit
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        free = st.free_bytes()
        if length > free:
            raise disk_full(length)
        tmp, filename, fields = await receive_upload(request, st, free)
        try:
            name = os.path.basename((filename or "").replace("\\", "/")) or "upload"
            stream = None
            if fields.get("stream", "").strip():
                try:
                    stream = int(fields["stream"])
                except ValueError:
                    raise ApiError(422, "decode_failed", f"Invalid audio track {fields['stream']!r}.") from None
            try:
                pr = await run_in_threadpool(aio.probe, tmp)
            except InputError as e:
                raise from_afix(e, 422) from None
            if stream is not None and not 0 <= stream < len(pr.streams):
                raise ApiError(422, "decode_failed", f"Audio track {stream} does not exist.")
            ch = max(s.channels for s in pr.streams)
            sr = max(s.sr for s in pr.streams)
            need = estimate_bytes(pr.duration_s, sr, ch, DEFAULT_STAGES)
            if need > st.free_bytes():
                raise disk_full(need)
            pid = st.create()
            dst = st.original_path(pid, name)
            try:
                os.replace(tmp, dst)
            except OSError:
                st.delete(pid)
                raise
        finally:
            tmp.unlink(missing_ok=True)
        streams = [dict(index=s.index, codec=s.codec, sr=s.sr, channels=s.channels) for s in pr.streams]
        needs = len(streams) > 1 and stream is None
        conn = cx()
        conn.execute("BEGIN IMMEDIATE")  # project row and its scan job together, or neither
        try:
            db.create_project(conn, pid, name, state="needs_stream" if needs else "scanning",
                              original_file=dst.name, streams=streams, stream=stream or 0, has_video=pr.has_video,
                              duration_s=round(pr.duration_s, 3), codec=pr.streams[stream or 0].codec)
            if not needs:
                db.enqueue(conn, pid, "scan", "cpu")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            st.delete(pid)
            raise
        return project_out(pid)

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return with_size(project_or_404(pid))

    @app.patch("/api/projects/{pid}")
    def rename(pid: str, body: RenameBody, request: Request):
        """Rename, and/or set (export_dir) or clear (clear_export_dir) the project's own export folder."""
        project_or_404(pid)
        fields = {}
        if body.name is not None:
            name = body.name.strip()
            if not 1 <= len(name) <= 200:
                raise ApiError(422, "bad_request", "Name must be 1–200 characters.")
            fields["name"] = name
        if body.clear_export_dir:
            fields["export_dir"] = None
        elif body.export_dir is not None:
            fields["export_dir"] = check_folder(request, body.export_dir)
        if fields:
            db.update_project(cx(), pid, **fields)
        return project_out(pid)

    @app.post("/api/projects/{pid}/stream")
    def choose_stream(pid: str, body: StreamBody):
        p = project_or_404(pid)
        if p["state"] != "needs_stream" or not 0 <= body.index < len(p["streams"]):
            raise ApiError(409, "invalid_state", "This project is not waiting for a track choice.")
        conn = cx()
        db.update_project(conn, pid, stream=body.index, state="scanning", codec=p["streams"][body.index]["codec"])
        db.enqueue(conn, pid, "scan", "cpu")
        return project_out(pid)

    @app.post("/api/projects/{pid}/run")
    def run(pid: str, body: RunBody):
        p = project_or_404(pid)
        if p["state"] not in ("ready", "done", "failed", "cancelled") or not p.get("sr"):
            raise ApiError(409, "invalid_state", f"Cannot start a repair while the project is {p['state']}.")
        known = {m.id for m in modules.all_modules()}
        bad = [m for m in body.modules if m not in known]
        if bad or not body.modules:
            raise ApiError(400, "unknown_module", f"Unknown module(s): {', '.join(bad) or '(none chosen)'}")
        ordered = [m.id for m in modules.ordered(list(dict.fromkeys(body.modules)))]
        if set(body.params) - set(ordered):
            raise ApiError(400, "bad_request", "Settings given for a module that is not in the chain.")
        params = {m: check_params(modules.get(m), body.params[m]) for m in ordered if m in body.params}
        require_gpu(ordered)
        need = estimate_bytes(p["duration_s"], p["sr"], p["channels"], len(ordered))
        if need > st.free_bytes() + st.size(pid):
            raise disk_full(need)
        conn = cx()
        st.reset_outputs(pid)
        db.clear_jobs(conn, pid)
        db.update_project(conn, pid, modules=ordered, params=params, state="processing", result=None, error=None)
        first = modules.get(ordered[0])
        db.enqueue(conn, pid, "repair", first.device, first.id, 0)
        return project_out(pid)

    @app.post("/api/projects/{pid}/cancel")
    def cancel(pid: str):
        project_or_404(pid)
        db.request_cancel(cx(), pid)  # one transaction; it also marks the project when nothing is running
        return project_out(pid)

    @app.post("/api/projects/{pid}/retry")
    def retry(pid: str):
        p = project_or_404(pid)
        conn = cx()
        last = [j for j in db.jobs_for(conn, pid) if j["state"] in ("failed", "cancelled")]
        if last and last[-1]["kind"] == "repair":  # the retried step and the ones after it run again
            require_gpu((p.get("modules") or [])[int(last[-1]["seq"]):])
        job = db.retry_failed(conn, pid)
        if job is None:
            raise ApiError(409, "invalid_state", "There is no failed step to retry.")
        # the worker never resets the project state, so the API does it when it requeues the step
        db.update_project(conn, pid, state="scanning" if job["kind"] == "scan" else "processing", error=None)
        return project_out(pid)

    @app.get("/api/projects/{pid}/fixes")
    def fixes(pid: str):
        p = project_or_404(pid)
        out = []
        for k, m in enumerate(p.get("modules") or []):
            if not (st.pdir(pid) / "fixes" / f"{k}_{m}.json").exists():
                continue
            fx, _ = st.load_fixes(pid, k, m)
            out += [dict(module=m, kind=f.kind, channel=f.channel, start=f.start, end=f.end,
                         seconds=round(f.start / p["sr"], 4)) for f in fx]
        out.sort(key=lambda r: r["start"])
        return {"fixes": out}

    def view_source(pid, src, t0, t1):
        """Shared by peaks and spectrogram: -> (project, get, n, i0, i1, gain) with 0 <= i0 <= i1 <= n."""
        p = done_or_409(pid)
        if not SRC_RE.match(src):
            raise ApiError(404, "file_not_found", f"No source {src}.")
        if not (math.isfinite(t0) and math.isfinite(t1)):
            raise ApiError(400, "bad_request", "t0 and t1 must be finite numbers of seconds.")
        try:
            get, n, gain = visual.resolve(st, p, src)
        except (KeyError, FileNotFoundError):
            raise ApiError(404, "file_not_found", f"No source {src}.") from None
        sr = p["sr"]
        i0 = max(0, min(n - 1, int(t0 * sr)))
        i1 = min(n, max(i0 + 1, int(t1 * sr)))
        return p, get, n, i0, i1, gain

    @app.get("/api/projects/{pid}/peaks")
    def peaks(pid: str, src: str, t0: float, t1: float, n: int = 1000):
        if t1 - t0 > MAX_PEAK_SPAN_S:
            raise ApiError(400, "bad_request", f"Use the overview peaks for spans over {MAX_PEAK_SPAN_S:.0f} s.")
        _, get, _, i0, i1, gain = view_source(pid, src, t0, t1)
        return {"peaks": visual.peaks_minmax(get, i0, i1, max(1, min(n, MAX_PEAK_BUCKETS)), gain)}

    @app.get("/api/projects/{pid}/spectrogram")
    def spectrogram(pid: str, src: str, t0: float, t1: float, w: int = 1200, h: int = 256):
        p, get, n, i0, i1, gain = view_source(pid, src, t0, t1)
        w, h = max(16, min(w, 4000)), max(32, min(h, 512))
        key = st.pdir(pid) / "cache" / f"spec_{src}_{i0}_{i1}_{w}x{h}.png"
        if not key.exists():
            tmp = aio.tmp_path_for(key)
            try:
                Path(tmp).write_bytes(visual.spectrogram_png(get, n, p["sr"], i0, i1, w, h, gain))
                os.replace(tmp, key)
            finally:
                Path(tmp).unlink(missing_ok=True)
        return Response(key.read_bytes(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/projects/{pid}/files/{rel:path}")
    def files(pid: str, rel: str, request: Request):
        project_or_404(pid)
        path = st.safe_file(pid, rel)
        if path is None:
            raise ApiError(404, "file_not_found", f"No file {rel}.")
        name = path.name if rel.startswith("exports/") else None
        return file_response(request, path, download_name=name)

    @app.get("/api/projects/{pid}/exports.zip")
    def exports_zip(pid: str, request: Request, name: list[str] = Query(default=[])):
        """The named export files in one zip (stored, not compressed: WAV barely compresses)."""
        p = project_or_404(pid)
        paths = [st.safe_file(pid, f"exports/{n}") for n in name]
        if not name or any(x is None for x in paths):
            raise ApiError(404, "file_not_found", "No such export file; export again.")
        dst = st.pdir(pid) / "cache" / "exports.zip"
        dst.parent.mkdir(exist_ok=True)
        tmp = aio.tmp_path_for(dst)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                for x in paths:
                    z.write(x, x.name)
            os.replace(tmp, dst)
        finally:
            Path(tmp).unlink(missing_ok=True)
        return file_response(request, dst, download_name=f"{export_stem(p['name'])}_afix.zip")

    @app.post("/api/projects/{pid}/open-exports")
    def open_exports(pid: str, request: Request):
        p = project_or_404(pid)
        if not can_open_folder(request):
            raise ApiError(403, "not_local", "The export folder can only be opened on the computer running the server.")
        target, _ = export_target(p)
        ex = Path(target) if target and Path(target).is_dir() else st.pdir(pid) / "exports"
        ex.mkdir(exist_ok=True)
        open_folder(ex)
        return {"folder": str(ex)}

    @app.post("/api/projects/{pid}/export")
    def export(pid: str, body: ExportBody, request: Request):
        p = done_or_409(pid)
        mods = p["modules"]
        ex = st.pdir(pid) / "exports"
        stem = export_stem(p["name"])
        out = []

        def add(kind, name):
            out.append({"kind": kind, "name": name, "url": f"/api/projects/{pid}/files/exports/{quote(name)}"})

        final = np.asarray(st.load(st.stage_path(pid, len(mods) - 1, mods[-1])))
        source_audio = np.asarray(st.load(st.source_path(pid)))
        wav_path = str(st.pdir(pid) / p["original_file"]) if p.get("wav_original") else None
        src = aio.Source(source_audio, p["sr"], p.get("subtype"), wav_path, bool(p.get("lossy")), p.get("codec") or "")
        fl = "_float" if body.float else ""
        speech = "speech" in mods
        name = f"{stem}_afix{'_speech' if speech else ''}{fl}.wav"
        info = aio.export(final, p["sr"], ex / name, src, float_out=body.float,
                          reduce_gain=chain.reduce_gain_for(mods))
        add("speech" if speech else "repaired", name)
        if speech:
            k = mods.index("speech")
            before = source_audio if k == 0 else np.asarray(st.load(st.stage_path(pid, k - 1, mods[k - 1])))
            write_removed(ex / f"{stem}_afix_removed{fl}.wav", before, final, p["sr"], info,
                          chain.reduce_gain_for(mods), body.float)
            add("removed", f"{stem}_afix_removed{fl}.wav")
            fx, _ = st.load_fixes(pid, k, "speech")
            labels = ((p["result"].get("notes") or {}).get("speech") or {}).get("labels") or []
            write_text_atomic(ex / f"{stem}_afix_removed.txt", "".join(
                f"{f.start / p['sr']:.6f}\t{f.end / p['sr']:.6f}\t{labels[i] if i < len(labels) else 'removed'}\n"
                for i, f in enumerate(fx)))
            add("labels", f"{stem}_afix_removed.txt")
        elif aio.write_diff(ex / f"{stem}_afix_diff.wav", source_audio, final, p["sr"]) is not None:
            add("diff", f"{stem}_afix_diff.wav")
        for kind, fname in (("csv", "fixes.csv"), ("report", "report.json")):
            shutil.copy(st.pdir(pid) / fname, ex / f"{stem}_afix_{fname}")
            add(kind, f"{stem}_afix_{fname}")
        local = can_open_folder(request)
        target, origin = export_target(p)
        saved, save_error = [], None
        if target:
            try:
                saved = fsbrowse.copy_into([ex / f["name"] for f in out], target)
            except OSError as e:
                save_error = f"Could not save into {target}: {e}"
        folder = target if saved else (str(ex.resolve()) if local else None)
        return {"files": out, **info, "zip_url": f"/api/projects/{pid}/exports.zip?"
                + "&".join(f"name={quote(f['name'])}" for f in out),
                "can_open_folder": local, "folder": folder,
                "target": target, "target_from": origin, "saved": saved, "save_error": save_error}

    @app.post("/api/projects/{pid}/clean")
    def clean(pid: str):
        p = project_or_404(pid)
        if p["state"] != "done":
            raise ApiError(409, "invalid_state", "Only finished projects can be cleaned.")
        st.clean_intermediates(pid, p["modules"])
        db.update_project(cx(), pid, result=dict(p["result"], cleaned=True))
        return project_out(pid)

    @app.delete("/api/projects/{pid}")
    def delete(pid: str):
        project_or_404(pid)
        conn = cx()
        db.request_cancel(conn, pid)
        db.delete_project(conn, pid)
        st.delete(pid)
        return {"ok": True}

    _register_module_routes(app)
    dist = os.environ.get("AFIX_WEB_DIST")
    if dist and Path(dist).is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def _default_gpu(st):
    from .gpu_manager import GpuManager
    return GpuManager(lambda: db.connect(st.db_path), st)


class InstallBody(BaseModel):
    confirm: bool = False


def _begin_gpu_action(gpu, action):
    """Start a background GPU module action, or raise 409: `busy` while a step runs, else `invalid_state`."""
    if action not in ACTIONS:
        raise ApiError(400, "bad_request", f"Unknown module action {action!r}.")
    if gpu.start(action):
        return gpu.state()
    if gpu.busy():
        raise ApiError(409, "busy", "The GPU module is busy with another step.", "Wait for it to finish.")
    if action == "download":
        raise ApiError(409, "invalid_state", "Run the GPU check before downloading.",
                       "Start the install without confirming first.")
    raise ApiError(409, "invalid_state", f"Cannot {action} the GPU module in its current state.")


def _register_module_routes(app):
    gpu = app.state.gpu

    @app.get("/api/modules")
    def list_modules():
        out = []
        for m in modules.all_modules():
            d = m.describe()
            if m.device == "gpu":
                s = gpu.state()
                d.update(state=s["state"], step=s.get("step"), detail=s.get("detail") or {})
            else:
                d.update(state="installed", step=None, detail={})
            out.append(d)
        return {"modules": out}

    @app.get("/api/models")
    def list_models(request: Request):
        from openaudiofix import speech as speech_core
        from . import models as models_mod
        return {"dir": str(speech_core.model_dir().resolve()), "models": models_mod.all_models(),
                "can_open_folder": can_open_folder(request)}

    def speech_running():
        return any(x["state"] in ("scanning", "processing") and "speech" in (x.get("modules") or [])
                   for x in db.list_projects(app.state.db()))

    @app.post("/api/models/silero/download")
    def download_speech_model():
        from openaudiofix import speech as speech_core
        from . import models as models_mod
        try:
            speech_core.ensure_model(say=lambda *_: None)
        except InputError as e:
            raise from_afix(e) from None
        except Exception as e:  # ModuleUnavailable and network errors carry their own message
            raise ApiError(502, "download_failed", str(e), getattr(e, "hint", "") or "Check the network and try again.") from None
        return models_mod.speech_model()

    @app.delete("/api/models/silero")
    def delete_speech_model():
        from openaudiofix import speech as speech_core
        from . import models as models_mod
        if speech_running():
            raise ApiError(409, "invalid_state", "A project is using the speech model; wait until it finishes.")
        (speech_core.model_dir() / speech_core.MODEL_FILE).unlink(missing_ok=True)
        return models_mod.speech_model()

    @app.post("/api/modules/denoise/install")
    def install(body: InstallBody):
        return _begin_gpu_action(gpu, "download" if body.confirm else "precheck")

    @app.get("/api/modules/denoise/install/events")
    async def install_events(request: Request):
        async def gen():
            last = None
            while not await request.is_disconnected():
                s = gpu.state()
                text = json.dumps(s, sort_keys=True, ensure_ascii=False)
                if text != last:
                    last = text
                    yield f"data: {text}\n\n"
                if s["state"] in TERMINAL and not gpu.busy():
                    break
                await asyncio.sleep(0.5)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/api/modules/denoise/verify")
    def verify():
        return _begin_gpu_action(gpu, "verify")

    @app.delete("/api/modules/denoise")
    def uninstall():
        return _begin_gpu_action(gpu, "remove")
