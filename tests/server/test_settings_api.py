"""Settings, the folder browser, export folders and moving the work data (phase 1 of the voicebox-style rework)."""
import json
import os

import pytest
from fastapi.testclient import TestClient

import server.app as appmod
from openaudiofix import paths
from server import locations
from server.app import create_app
from server.worker import Worker

from .test_api import BASE, _clipped, drain, upload


@pytest.fixture
def local(tmp_path, monkeypatch):
    """A client the server treats as on its own computer, with its config folder in tmp."""
    monkeypatch.setattr(appmod, "LOOPBACK", {"testclient"})
    monkeypatch.setenv("AFIX_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("AFIX_MODELS", str(tmp_path / "models"))
    monkeypatch.setenv("AFIX_LOCATION_SOURCES", json.dumps({"data": "settings", "models": "env"}))
    monkeypatch.delenv("AFIX_IN_CONTAINER", raising=False)
    data = tmp_path / "data"
    with TestClient(create_app(data), base_url=BASE) as c:
        c.worker = Worker(data, "cpu", "w-set", poll=0)
        c.data = data
        yield c


def _done_project(c, tmp_path):
    pid = upload(c, _clipped(tmp_path)).json()["id"]
    drain(c)
    c.post(f"/api/projects/{pid}/run", json={"modules": ["glitch"]})
    drain(c)
    return pid


def test_settings_report_folders_and_keep_a_default_export_folder(local, tmp_path):
    s = local.get("/api/settings").json()
    assert s["export_dir"] is None and s["data_dir"] == str(local.data.resolve())
    assert s["sources"] == {"data": "settings", "models": "env"} and s["can_browse"] is True
    out = tmp_path / "out"
    out.mkdir()
    s = local.put("/api/settings", json={"export_dir": str(out)}).json()
    assert s["export_dir"] == str(out.resolve())
    assert json.loads((local.data / "settings.json").read_text())["export_dir"] == str(out.resolve())
    r = local.put("/api/settings", json={"export_dir": str(tmp_path / "missing")})
    assert r.status_code == 404 and r.json()["code"] == "folder_not_found"
    assert local.put("/api/settings", json={"export_dir": "relative/dir"}).json()["code"] == "bad_request"
    assert local.put("/api/settings", json={"export_dir": None}).json()["export_dir"] is None


def test_folder_browser_lists_folders_only_and_creates_folders(local, tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "A").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.wav").write_bytes(b"x")
    r = local.get("/api/fs/dirs", params={"path": str(tmp_path)}).json()
    names = [d["name"] for d in r["dirs"]]
    assert "A" in names and "b" in names and ".hidden" not in names and "file.wav" not in names
    assert names.index("A") < names.index("b") and r["parent"] == str(tmp_path.parent) and r["writable"]
    assert local.get("/api/fs/dirs").json()["roots"]  # no path: the starting points
    made = local.post("/api/fs/mkdir", json={"path": str(tmp_path), "name": "新資料夾"}).json()
    assert made["path"] == str((tmp_path / "新資料夾").resolve()) and (tmp_path / "新資料夾").is_dir()
    assert local.post("/api/fs/mkdir", json={"path": str(tmp_path), "name": "a/b"}).json()["code"] == "bad_request"


def test_choosing_folders_is_refused_from_another_computer(tmp_path, monkeypatch):
    monkeypatch.delenv("AFIX_IN_CONTAINER", raising=False)
    with TestClient(create_app(tmp_path / "data"), base_url=BASE) as c:  # "testclient" is not loopback
        assert c.get("/api/fs/dirs", params={"path": str(tmp_path)}).json()["code"] == "not_local"
        assert c.put("/api/settings", json={"export_dir": str(tmp_path)}).json()["code"] == "not_local"


def test_in_a_container_the_browser_stays_inside_the_allowed_roots(tmp_path, monkeypatch):
    root = tmp_path / "mnt"
    root.mkdir()
    monkeypatch.setenv("AFIX_IN_CONTAINER", "1")
    monkeypatch.setenv("AFIX_BROWSE_ROOTS", str(root))
    with TestClient(create_app(tmp_path / "data"), base_url=BASE) as c:
        assert c.get("/api/fs/dirs").json()["roots"] == [str(root.resolve())]
        assert c.get("/api/fs/dirs", params={"path": str(root)}).json()["parent"] is None
        assert c.get("/api/fs/dirs", params={"path": str(tmp_path)}).json()["code"] == "not_allowed"
        assert c.put("/api/settings", json={"export_dir": str(tmp_path)}).json()["code"] == "not_allowed"


def test_export_saves_into_the_project_folder_else_the_default_without_overwriting(local, tmp_path):
    pid = _done_project(local, tmp_path)
    ex = local.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert ex["target"] is None and ex["saved"] == []  # nothing chosen: downloads only
    default, own = tmp_path / "default", tmp_path / "own"
    default.mkdir()
    own.mkdir()
    local.put("/api/settings", json={"export_dir": str(default)})
    ex = local.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert ex["target_from"] == "default" and ex["folder"] == str(default.resolve())
    assert sorted(os.listdir(default)) == sorted(f["name"] for f in ex["files"])
    again = local.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert all(" (2)" in os.path.basename(x) for x in again["saved"])  # an existing file is never overwritten
    p = local.patch(f"/api/projects/{pid}", json={"export_dir": str(own)}).json()
    assert p["export_dir"] == str(own.resolve()) and p["name"].endswith(".wav")
    ex = local.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert ex["target_from"] == "project" and len(os.listdir(own)) == len(ex["files"])
    assert local.patch(f"/api/projects/{pid}", json={"clear_export_dir": True}).json()["export_dir"] is None
    gone = tmp_path / "gone"
    gone.mkdir()
    local.patch(f"/api/projects/{pid}", json={"export_dir": str(gone)})
    gone.rmdir()  # a folder deleted since it was chosen is made again
    assert local.post(f"/api/projects/{pid}/export", json={"float": False}).json()["saved"] and gone.is_dir()
    for f in os.listdir(gone):
        os.remove(gone / f)
    gone.rmdir()
    gone.write_text("now a file")  # a path that cannot become a folder
    ex = local.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert ex["save_error"] and ex["files"]  # the in-app downloads still work


def test_moving_the_work_data_copies_it_and_saves_the_choice_for_the_next_start(local, tmp_path):
    pid = _done_project(local, tmp_path)
    new = tmp_path / "newdata"
    new.mkdir()
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    assert local.post("/api/settings/move", json={"what": "data", "path": str(tmp_path / "full")}).json()["code"] \
        == "folder_not_empty"
    assert local.post("/api/settings/move", json={"what": "data", "path": str(local.data / "projects")}).json()[
        "code"] == "bad_request"
    assert local.post("/api/settings/move", json={"what": "models", "path": str(new)}).json()["code"] == "set_by_env"
    local.post("/api/settings/move", json={"what": "data", "path": str(new)})
    app = local.app
    for _ in range(200):
        if not app.state.copier.busy():
            break
        import time
        time.sleep(0.05)
    s = local.get("/api/settings").json()
    assert s["move"]["state"] == "done" and s["saved"] == {"data": str(new.resolve())}
    assert (new / "projects" / pid / "report.json").is_file() and (local.data / "projects" / pid).is_dir()
    import sqlite3
    with sqlite3.connect(new / "afix.db") as conn:
        assert conn.execute("SELECT id FROM projects").fetchone()[0] == pid


def test_apply_location_prefers_the_environment_then_the_saved_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("AFIX_CONFIG_DIR", str(tmp_path))
    locations.save_location(data=str(tmp_path / "d"), models=str(tmp_path / "m"))
    env = {"AFIX_MODELS": "/from/env"}
    src = locations.apply_location(env)
    assert src == {"data": "settings", "models": "env"}
    assert env["AFIX_DATA"] == str(tmp_path / "d") and env["AFIX_MODELS"] == "/from/env"
    locations.save_location(data=None)
    env = {}
    monkeypatch.chdir(tmp_path)
    assert locations.apply_location(env)["data"] == "default"
    assert env["AFIX_DATA"] == str(paths.default_data_dir())  # never relative to where the server was started
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "afix.db").write_text("")
    env = {}
    locations.apply_location(env)
    assert env["AFIX_DATA"] == "data"  # a store from before the per-system defaults keeps working
    (tmp_path / "location.json").write_text("not json")
    assert locations.read_location() == {}


def test_restart_needs_the_launcher(local, monkeypatch):
    monkeypatch.delenv("AFIX_LAUNCHER", raising=False)
    assert local.post("/api/restart").json()["code"] == "invalid_state"


def test_models_page_lists_the_models_and_manages_the_speech_model(local, tmp_path, monkeypatch):
    from openaudiofix import speech as speech_core
    r = local.get("/api/models").json()
    by = {m["id"]: m for m in r["models"]}
    assert set(by) == {"silero", "mossformer2", "gpu_packages"} and r["dir"] == str((tmp_path / "models").resolve())
    assert by["silero"]["state"] == "missing" and by["silero"]["used_by"] == ["speech"]
    assert by["mossformer2"]["managed_by"] == "denoise"

    def fake_download(say=print, **_):
        p = speech_core.model_dir() / speech_core.MODEL_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"onnx")
        return p
    monkeypatch.setattr(speech_core, "ensure_model", fake_download)
    m = local.post("/api/models/silero/download").json()
    assert m["state"] == "corrupt" and m["size_bytes"] == 4  # the checksum is still checked when listing
    assert local.delete("/api/models/silero").json()["state"] == "missing"

    def offline(say=print, **_):
        raise speech_core.ModuleUnavailable("Could not download the speech model: offline", hint="Check the network")
    monkeypatch.setattr(speech_core, "ensure_model", offline)
    r = local.post("/api/models/silero/download")
    assert r.status_code == 502 and r.json()["code"] == "download_failed" and "offline" in r.json()["message"]


def test_the_export_folder_has_a_default_and_null_means_download_only(local, tmp_path, monkeypatch):
    d = tmp_path / "docs" / "OpenAudioFix" / "Exports"
    monkeypatch.setenv("AFIX_DEFAULT_EXPORT", str(d))
    s = local.get("/api/settings").json()
    assert s["export_dir"] == str(d) and s["defaults"]["export"] == str(d) and not d.exists()
    pid = _done_project(local, tmp_path)
    ex = local.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert ex["target_from"] == "default" and len(os.listdir(d)) == len(ex["files"])  # made when first needed
    assert local.put("/api/settings", json={"export_dir": None}).json()["export_dir"] is None  # stays off
    assert local.post(f"/api/projects/{pid}/export", json={"float": False}).json()["saved"] == []
    d2 = tmp_path / "other" / "Exports"
    monkeypatch.setenv("AFIX_DEFAULT_EXPORT", str(d2))
    assert local.put("/api/settings", json={"export_dir": str(d2)}).json()["export_dir"] == str(d2.resolve())
    assert d2.is_dir()  # going back to the default makes the folder


def test_in_a_container_there_is_no_default_export_folder(monkeypatch):
    monkeypatch.delenv("AFIX_DEFAULT_EXPORT", raising=False)
    monkeypatch.setenv("AFIX_IN_CONTAINER", "1")
    assert locations.default_export() is None
    monkeypatch.delenv("AFIX_IN_CONTAINER")
    assert locations.default_export() == str(paths.default_export_dir())


def test_the_browser_falls_back_to_the_nearest_folder_and_lists_common_places(local, tmp_path):
    r = local.get("/api/fs/dirs", params={"path": str(tmp_path / "not" / "yet")}).json()
    assert r["path"] == str(tmp_path.resolve())
    assert str(paths.user_folder("documents")) in r["roots"] or not paths.user_folder("documents").is_dir()


def test_settings_warn_about_folders_inside_the_system_temp_folder(local, tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    assert local.get("/api/settings").json()["in_temp"] == {"data": True, "models": True}
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "elsewhere"))
    assert local.get("/api/settings").json()["in_temp"] == {"data": False, "models": False}


def test_default_folders_on_each_system(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert paths.default_data_dir() == tmp_path / ".local" / "share" / "openaudiofix"
    assert paths.default_models_dir() == tmp_path / ".cache" / "openaudiofix" / "models"
    assert paths.default_export_dir() == tmp_path / "Documents" / "OpenAudioFix" / "Exports"
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    assert paths.default_data_dir() == tmp_path / "Library" / "Application Support" / "OpenAudioFix"


def test_windows_keeps_models_already_in_the_old_cache_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "user_folder", lambda n: tmp_path / n)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    new = tmp_path / "local" / "OpenAudioFix" / "models"
    assert paths.default_models_dir() == new
    old = tmp_path / "cache" / "openaudiofix" / "models"
    old.mkdir(parents=True)
    (old / "m.onnx").write_bytes(b"x")
    assert paths.default_models_dir() == old and paths.preferred_models_dir() == new
    new.mkdir(parents=True)
    assert paths.default_models_dir() == new


def test_the_test_suite_itself_never_saves_exports_into_the_real_documents_folder():
    assert os.environ["AFIX_DEFAULT_EXPORT"] == "" and locations.default_export() is None
