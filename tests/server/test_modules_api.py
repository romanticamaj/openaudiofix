import json

import pytest
from fastapi.testclient import TestClient

from server import db, gpu_manager as GM
from server.app import create_app
from server.storage import Storage
from tests.server.fake_gpu import FakeGpu, Spawner


@pytest.fixture
def client(tmp_path):
    st = Storage(tmp_path)
    st.free_bytes = lambda: int(100e9)
    mgr = GM.GpuManager(lambda: db.connect(st.db_path), st, gpu=FakeGpu(), spawn=Spawner())
    with TestClient(create_app(tmp_path, gpu=mgr), base_url="http://127.0.0.1:8765") as c:
        c.mgr = mgr
        yield c


def wait(mgr):
    if mgr._thread:
        mgr._thread.join(10)


def assert_error(r, status, code):
    assert r.status_code == status
    body = r.json()
    assert body["code"] == code and set(body) == {"code", "message", "hint"} and body["message"]


def test_modules_listing(client):
    mods = {m["id"]: m for m in client.get("/api/modules").json()["modules"]}
    assert mods["glitch"]["state"] == "installed" and mods["denoise"]["state"] == "not_installed"
    assert mods["denoise"]["device"] == "gpu"


def test_install_flow_over_api_and_sse(client):
    assert client.post("/api/modules/denoise/install", json={"confirm": False}).status_code == 200
    wait(client.mgr)
    with client.stream("GET", "/api/modules/denoise/install/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert events[-1]["state"] == "ready_to_download"
    assert client.post("/api/modules/denoise/install", json={"confirm": True}).status_code == 200
    wait(client.mgr)
    assert client.get("/api/modules").json()["modules"][-1]["state"] == "installed"
    assert client.post("/api/modules/denoise/verify").status_code == 200
    wait(client.mgr)
    assert client.mgr.state()["state"] == "installed"
    assert client.delete("/api/modules/denoise").status_code == 200
    wait(client.mgr)
    assert client.mgr.state()["state"] == "not_installed"


def test_confirm_without_precheck_is_invalid(client):
    assert_error(client.post("/api/modules/denoise/install", json={"confirm": True}), 409, "invalid_state")


def test_busy_is_distinguished_from_wrong_state(client):
    client.mgr.busy = lambda: True
    client.mgr.start = lambda action: False
    assert_error(client.post("/api/modules/denoise/install", json={"confirm": False}), 409, "busy")
    assert_error(client.post("/api/modules/denoise/verify"), 409, "busy")
    assert_error(client.delete("/api/modules/denoise"), 409, "busy")


def test_bad_body_is_bad_request(client):
    assert_error(client.post("/api/modules/denoise/install", json={"confirm": "maybe"}), 400, "bad_request")
