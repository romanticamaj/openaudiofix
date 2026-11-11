import io
import json
import subprocess
from urllib.parse import quote

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from openaudiofix import modules, synth
from server import app as app_mod
from server.app import create_app, export_stem
from server.worker import Worker
from tests import fakes

BASE = "http://127.0.0.1:8765"  # the Host the browser sends through the compose port mapping


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        c.worker = Worker(tmp_path, "cpu", "w-api", poll=0)
        yield c


def drain(c):
    for _ in range(30):
        if not c.worker.run_once():
            return


def upload(c, path, name=None):
    with open(path, "rb") as f:
        return c.post("/api/projects", files={"file": (name or path.name, f, "application/octet-stream")})


def _clipped(tmp_path, name="clip.wav"):
    p = tmp_path / name  # short and mildly clipped: declip is slow
    sf.write(p, synth.inject_clipping(synth.speech_like(seconds=0.5, seed=1), 3.0), 48000, subtype="PCM_24")
    return p


def _nothing_kept(tmp_path):
    return list((tmp_path / "projects").iterdir()) == [] and list((tmp_path / "tmp").iterdir()) == []


def test_health_reports_checks(client):
    r = client.get("/api/health").json()
    assert r["ok"] and {c["id"] for c in r["checks"]} == {"data_writable", "sqlite", "ffmpeg"}


def test_full_flow_with_unicode_filename(client, tmp_path):
    src = _clipped(tmp_path)
    r = upload(client, src, "訪談 第1集.wav")
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert r.json()["name"] == "訪談 第1集.wav"
    drain(client)
    p = client.get(f"/api/projects/{pid}").json()
    assert p["state"] == "ready" and p["scan"]["declip"]["detected"]
    assert client.post(f"/api/projects/{pid}/run", json={"modules": ["declip"]}).status_code == 200
    drain(client)
    p = client.get(f"/api/projects/{pid}").json()
    assert p["state"] == "done", p
    fx = client.get(f"/api/projects/{pid}/fixes").json()["fixes"]
    assert fx and fx[0]["module"] == "declip"
    peaks = client.get(f"/api/projects/{pid}/peaks", params={"src": "s0", "t0": 0, "t1": 1.0, "n": 200}).json()
    assert len(peaks["peaks"]) == 400
    png = client.get(f"/api/projects/{pid}/spectrogram", params={"src": "original", "t0": 0, "t1": 1.5, "w": 100, "h": 64})
    assert png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    again = client.get(f"/api/projects/{pid}/spectrogram", params={"src": "original", "t0": 0, "t1": 1.5, "w": 100, "h": 64})
    assert again.content == png.content  # served from cache/
    ex = client.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    wav = next(f for f in ex["files"] if f["kind"] == "repaired")
    assert {f["kind"] for f in ex["files"]} == {"repaired", "diff", "csv", "report"}
    assert wav["name"] == "訪談 第1集_afix.wav" and ex["gain_db"] < 0
    d = client.get(wav["url"])
    cd = d.headers["content-disposition"]
    assert d.status_code == 200 and cd.startswith("attachment; ")
    assert f"filename*=UTF-8''{quote('訪談 第1集_afix.wav', safe='')}" in cd
    assert 'filename="' in cd and cd.isascii()  # ASCII fallback for old clients
    assert sf.info(io.BytesIO(d.content)).subtype == "PCM_24"
    for f in ex["files"]:
        assert client.get(f["url"]).status_code == 200
    listing = client.get("/api/projects").json()
    assert listing["projects"][0]["name"] == "訪談 第1集.wav" and listing["total_bytes"] > 0
    assert "jobs" not in listing["projects"][0] and listing["projects"][0]["size_bytes"] > 0


def test_range_requests_on_previews(client, tmp_path):
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    drain(client)
    client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch"]})
    drain(client)
    url = f"/api/projects/{pid}/files/preview/original.webm"
    full = client.get(url)
    assert full.headers["accept-ranges"] == "bytes" and full.headers["content-type"] == "audio/webm"
    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.content == full.content[:100]
    assert part.headers["content-range"].startswith("bytes 0-99/")
    tail = client.get(url, headers={"Range": "bytes=-10"})
    assert tail.status_code == 206 and tail.content == full.content[-10:]
    assert client.get(url, headers={"Range": "bytes=999999999-"}).status_code == 416
    assert client.get(url, headers={"Range": "bytes=abc"}).status_code < 500
    assert client.get(f"/api/projects/{pid}/files/../../afix.db").status_code == 404


@pytest.mark.parametrize("rel", ["%2e%2e%2f%2e%2e%2fafix.db", "..%2f..%2fafix.db", "%2e%2e/%2e%2e/afix.db",
                                 "preview%2f..%2f..%2f..%2fafix.db", "%2fetc%2fpasswd", "C:%5cWindows%5cwin.ini"])
def test_encoded_path_traversal_reaches_the_guard(client, tmp_path, rel):
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    db_bytes = (tmp_path / "afix.db").read_bytes()[:64]
    r = client.get(f"/api/projects/{pid}/files/{rel}")
    assert 400 <= r.status_code < 500
    assert r.json()["code"] == "file_not_found"  # the route ran and the resolver refused it
    assert db_bytes not in r.content


def test_encoded_project_id_traversal_is_not_found(client):
    for pid in ("%2e%2e", "..%2f..", "ABCDEF012345", "zzz"):
        r = client.get(f"/api/projects/{pid}/files/afix.db")
        assert r.status_code == 404 and b"SQLite" not in r.content


def test_rejections_carry_codes(client, tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"nope" * 200)
    r = upload(client, bad)
    assert r.status_code == 422 and r.json()["code"] == "decode_failed"
    assert client.get("/api/projects").json()["projects"] == []
    assert _nothing_kept(tmp_path)
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    drain(client)
    r = client.post(f"/api/projects/{pid}/run", json={"modules": ["nope"]})
    assert r.status_code == 400 and r.json()["code"] == "unknown_module"
    r = client.post(f"/api/projects/{pid}/run", json={"modules": ["denoise"]})
    assert r.status_code == 409 and r.json()["code"] == "module_not_installed"
    assert client.get("/api/projects/zzz").json()["code"] == "project_not_found"
    r = client.get(f"/api/projects/{pid}/peaks", params={"src": "s0", "t0": 0, "t1": 1})
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"


@pytest.mark.parametrize("pid", ["zzz", "ABCDEF012345", "0123456789abc", ".."])
def test_malformed_project_ids_are_404_everywhere(client, pid):
    for method, url in (("get", f"/api/projects/{pid}"), ("delete", f"/api/projects/{pid}"),
                        ("post", f"/api/projects/{pid}/cancel"), ("post", f"/api/projects/{pid}/clean"),
                        ("get", f"/api/projects/{pid}/fixes"),
                        ("get", f"/api/projects/{pid}/peaks?src=s0&t0=0&t1=1")):
        r = getattr(client, method)(url)
        if r.status_code == 404 and pid == "..":
            continue  # httpx normalises "/projects/.." away; the router answers 404 itself
        assert r.status_code == 404 and r.json()["code"] == "project_not_found", (method, url, r.text)


def test_too_long_is_rejected_at_probe_and_nothing_is_written(client, tmp_path, monkeypatch):
    from openaudiofix import io as aio
    monkeypatch.setattr(aio, "MAX_DURATION_S", 0.25)
    r = upload(client, _clipped(tmp_path))
    assert r.status_code == 422 and r.json()["code"] == "too_long"
    assert _nothing_kept(tmp_path) and not list(tmp_path.rglob("source.npy"))
    assert client.get("/api/projects").json()["projects"] == []


def test_disk_full_is_refused(client, tmp_path, monkeypatch):
    from server import storage
    monkeypatch.setattr(storage.Storage, "free_bytes", lambda self: 10)
    r = upload(client, _clipped(tmp_path))
    assert r.status_code == 507 and r.json()["code"] == "disk_full"
    assert _nothing_kept(tmp_path)


def test_content_length_is_checked_before_the_body_is_read(client, tmp_path, monkeypatch):
    from server import storage

    async def must_not_run(*a, **k):
        raise AssertionError("the body was read")
    monkeypatch.setattr(storage.Storage, "free_bytes", lambda self: 10)
    monkeypatch.setattr(app_mod, "receive_upload", must_not_run)
    r = upload(client, _clipped(tmp_path))
    assert r.status_code == 507 and r.json()["code"] == "disk_full"


def test_disk_estimate_after_probe_deletes_the_upload(client, tmp_path, monkeypatch):
    from server import storage
    src = _clipped(tmp_path)  # 0.5 s mono 48 kHz (~72 kB): estimate = 0.5*48000*1*8*4*1.1 ~ 845 kB
    monkeypatch.setattr(storage.Storage, "free_bytes", lambda self: 300_000)
    r = upload(client, src)
    assert r.status_code == 507 and r.json()["code"] == "disk_full" and "GB" in r.json()["message"]
    assert _nothing_kept(tmp_path)


def test_multiple_audio_tracks_ask_for_a_stream(client, tmp_path):
    p = tmp_path / "m.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:duration=1", "-f", "lavfi",
                    "-i", "sine=f=600:duration=1", "-map", "0", "-map", "1", "-c:a", "flac", str(p)], check=True)
    r = upload(client, p).json()
    assert r["state"] == "needs_stream" and len(r["streams"]) == 2
    assert client.post(f"/api/projects/{r['id']}/stream", json={"index": 5}).status_code == 409
    client.post(f"/api/projects/{r['id']}/stream", json={"index": 1})
    drain(client)
    p = client.get(f"/api/projects/{r['id']}").json()
    assert p["state"] == "ready" and p["stream"] == 1


def test_upload_with_unknown_stream_is_refused(client, tmp_path):
    with open(_clipped(tmp_path), "rb") as f:
        r = client.post("/api/projects", files={"file": ("a.wav", f)}, data={"stream": "3"})
    assert r.status_code == 422 and r.json()["code"] == "decode_failed"
    assert _nothing_kept(tmp_path)


def test_peaks_window_is_clamped_and_sources_checked(client, tmp_path):
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    drain(client)
    client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch"]})
    drain(client)
    url = f"/api/projects/{pid}/peaks"
    r = client.get(url, params={"src": "final", "t0": -5, "t1": 99, "n": 100})
    assert r.status_code == 200 and len(r.json()["peaks"]) == 200
    assert client.get(url, params={"src": "original", "t0": 0.5, "t1": 3, "n": 10}).status_code == 200
    assert client.get(url, params={"src": "s7", "t0": 0, "t1": 1}).json()["code"] == "file_not_found"
    assert client.get(url, params={"src": "../x", "t0": 0, "t1": 1}).status_code == 404
    assert client.get(url, params={"src": "s0", "t0": "nan", "t1": 1}).status_code == 400


def test_retry_requeues_the_failed_step_and_sets_processing(client, tmp_path, monkeypatch):
    monkeypatch.setenv("AFIX_EXTRA_MODULES", "tests.fakes:FlakyModule")
    client.worker = Worker(tmp_path, "cpu", "w-api", poll=0)
    try:
        pid = upload(client, _clipped(tmp_path)).json()["id"]
        drain(client)
        assert client.post(f"/api/projects/{pid}/retry").status_code == 409  # nothing failed yet
        client.post(f"/api/projects/{pid}/run", json={"modules": ["flaky", "glitch"]})
        drain(client)
        assert client.get(f"/api/projects/{pid}").json()["state"] == "failed"
        fakes.FlakyModule.fail = False
        r = client.post(f"/api/projects/{pid}/retry").json()
        assert r["state"] == "processing" and r["error"] is None
        drain(client)
        assert client.get(f"/api/projects/{pid}").json()["state"] == "done"
    finally:
        modules.unregister("flaky")
        fakes.FlakyModule.fail, fakes.FlakyModule.runs = True, 0


def test_run_validates_module_settings_and_passes_them_to_the_module(client, tmp_path, monkeypatch):
    monkeypatch.setenv("AFIX_EXTRA_MODULES", "tests.fakes:SleepyModule")
    client.worker = Worker(tmp_path, "cpu", "w-api", poll=0)
    try:
        pid = upload(client, _clipped(tmp_path)).json()["id"]
        drain(client)
        run = lambda params: client.post(f"/api/projects/{pid}/run", json={"modules": ["sleepy"], "params": params})
        for bad in ({"sleepy": {"steps": 0}}, {"sleepy": {"steps": "2"}}, {"sleepy": {"steps": True}},
                    {"sleepy": {"mode": "c"}}, {"sleepy": {"nope": 1}}, {"glitch": {}}):
            r = run(bad)
            assert r.status_code == 400 and r.json()["code"] == "bad_request", (bad, r.text)
        seen = []
        orig = fakes.SleepyModule.repair
        monkeypatch.setattr(fakes.SleepyModule, "repair",
                            lambda self, a, sr, params, progress: seen.append(params) or orig(self, a, sr, params, progress))
        assert run({"sleepy": {"steps": 1}}).json()["params"] == {"sleepy": {"steps": 1, "mode": "a"}}
        drain(client)
        assert seen == [{"steps": 1, "mode": "a"}]
        assert json.loads(client.get(f"/api/projects/{pid}/files/report.json").content)["params"]["sleepy"]["steps"] == 1
        assert client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch"]}).json()["params"] == {}
    finally:
        modules.unregister("sleepy")


def test_cancel_leaves_a_finished_project_alone(client, tmp_path):
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    drain(client)
    assert client.post(f"/api/projects/{pid}/cancel").json()["state"] == "ready"
    pid2 = upload(client, _clipped(tmp_path, "b.wav")).json()["id"]
    assert client.post(f"/api/projects/{pid2}/cancel").json()["state"] == "cancelled"


def test_busy_projects_carry_their_jobs_and_finished_ones_their_timings(client, tmp_path):
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    listing = client.get("/api/projects").json()["projects"]
    assert listing[0]["state"] == "scanning" and [j["kind"] for j in listing[0]["jobs"]] == ["scan"]
    assert abs(listing[0]["now"] - __import__("time").time()) < 60
    drain(client)
    client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch", "declip"]})
    drain(client)
    p = client.get(f"/api/projects/{pid}").json()
    assert p["state"] == "done" and set(p["result"]["timings"]) == {"glitch", "declip"}
    assert all(v >= 0 for v in p["result"]["timings"].values())
    assert all(j["started"] and j["finished"] >= j["started"] for j in p["jobs"])


def test_glitch_only_export_does_not_reduce_gain(client, tmp_path):
    p = tmp_path / "hot.wav"
    x = synth.speech_like(seconds=1.0, seed=3)
    sf.write(p, x * (1.5 / np.abs(x).max()), 48000, subtype="FLOAT")  # peak 1.5
    assert float(np.abs(sf.read(p)[0]).max()) > 1.0
    pid = upload(client, p).json()["id"]
    drain(client)
    client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch"]})
    drain(client)
    ex = client.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert ex["gain_db"] == 0.0 and ex["byte_exact"] is False
    fl = client.post(f"/api/projects/{pid}/export", json={"float": True}).json()
    assert next(f for f in fl["files"] if f["kind"] == "repaired")["name"] == "hot_afix_float.wav"


def test_export_zip_holds_the_exported_files_and_open_folder_is_local_only(client, tmp_path, monkeypatch):
    import io
    import zipfile

    import server.app as appmod
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    drain(client)
    client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch"]})
    drain(client)
    ex = client.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert ex["can_open_folder"] is False and ex["folder"] is None  # the test client is not on loopback
    r = client.get(ex["zip_url"])
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == sorted(f["name"] for f in ex["files"])
    first = ex["files"][0]
    assert z.read(first["name"]) == client.get(first["url"]).content
    assert client.get(f"/api/projects/{pid}/exports.zip?name=..%2Freport.json").status_code == 404
    assert client.get(f"/api/projects/{pid}/exports.zip").status_code == 404

    assert client.post(f"/api/projects/{pid}/open-exports").json()["code"] == "not_local"
    opened = []
    monkeypatch.setattr(appmod, "LOOPBACK", {"testclient"})
    monkeypatch.setattr(appmod, "open_folder", opened.append)
    monkeypatch.setenv("AFIX_OPEN_FOLDER", "1")
    assert client.post(f"/api/projects/{pid}/export", json={"float": False}).json()["can_open_folder"] is True
    r = client.post(f"/api/projects/{pid}/open-exports")
    assert r.status_code == 200 and opened and opened[0].name == "exports"
    monkeypatch.setenv("AFIX_OPEN_FOLDER", "0")
    assert client.post(f"/api/projects/{pid}/open-exports").status_code == 403


def test_export_stem_is_a_safe_reachable_filename():
    assert export_stem("訪談 第1集.mp3") == "訪談 第1集"
    assert export_stem("100% a:b?.wav") == "100_ a_b_"
    assert export_stem(".wav") == "wav" and export_stem("...") == "audio" and export_stem("") == "audio"


def test_clean_and_delete(client, tmp_path):
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    drain(client)
    client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch", "declip"]})
    drain(client)
    assert client.post(f"/api/projects/{pid}/clean").status_code == 200
    assert client.get(f"/api/projects/{pid}/files/preview/s0.webm").status_code == 404
    assert client.get(f"/api/projects/{pid}/files/preview/s1.webm").status_code == 200
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert not (tmp_path / "projects" / pid).exists()


SHAPE = {"code", "message", "hint"}


def test_framework_errors_keep_the_error_shape(client, tmp_path):
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    r = client.get(f"/api/projects/{pid}/peaks", params={"t0": 0, "t1": 1})
    assert r.status_code == 400 and set(r.json()) == SHAPE and r.json()["code"] == "bad_request"
    assert "query.src" in r.json()["message"]
    r = client.post(f"/api/projects/{pid}/run", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400 and set(r.json()) == SHAPE and r.json()["code"] == "bad_request"
    r = client.get("/api/nope")
    assert r.status_code == 404 and set(r.json()) == SHAPE and r.json()["code"] == "file_not_found"
    r = client.put("/api/projects")
    assert r.status_code == 405 and set(r.json()) == SHAPE and r.json()["code"] == "invalid_state"
    r = client.post("/api/projects", content=b"x", headers={"content-type": "text/plain"})
    assert r.status_code == 400 and r.json()["code"] == "bad_request"


def test_peak_span_is_checked_first(client):
    r = client.get("/api/projects/0123456789ab/peaks", params={"src": "s0", "t0": 0, "t1": 500})
    assert r.status_code == 400 and r.json()["code"] == "bad_request"


def test_unexpected_errors_are_internal_error(tmp_path, monkeypatch):
    from server import db
    with TestClient(create_app(tmp_path), base_url=BASE, raise_server_exceptions=False) as c:
        def boom(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(db, "list_projects", boom)
        r = c.get("/api/projects")
        assert r.status_code == 500 and set(r.json()) == SHAPE and r.json()["code"] == "internal_error"


def test_failed_project_insert_removes_the_project_folder(tmp_path, monkeypatch):
    from server import db
    with TestClient(create_app(tmp_path), base_url=BASE, raise_server_exceptions=False) as c:
        def boom(*a, **k):
            raise RuntimeError("enqueue failed")
        monkeypatch.setattr(db, "enqueue", boom)
        r = upload(c, _clipped(tmp_path))
        assert r.status_code == 500 and r.json()["code"] == "internal_error"
        monkeypatch.undo()
        assert _nothing_kept(tmp_path) and c.get("/api/projects").json()["projects"] == []


def test_range_edge_cases(client, tmp_path):
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    url = f"/api/projects/{pid}/files/original.wav"
    full = client.get(url)
    size = len(full.content)
    r = client.get(url, headers={"Range": "bytes=999999999-"})
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{size}"
    for bad in ("bytes=abc", "items=0-5", "bytes=5-2", "bytes"):
        r = client.get(url, headers={"Range": bad})
        assert r.status_code == 200 and r.content == full.content, bad  # RFC 9110: ignore it
    r = client.get(url, headers={"Range": "bytes=-999999999"})
    assert r.status_code == 206 and r.content == full.content
    r = client.get(url, headers={"Range": "bytes=10-19, 999999999-"})  # unsatisfiable part dropped
    assert r.status_code == 206 and r.content == full.content[10:20]


def test_normalize_range():
    from server.ranges import normalize_range
    assert normalize_range("bytes=0-99", 50) == "bytes=0-49"
    assert normalize_range("bytes=-10", 50) == "bytes=40-49"
    assert normalize_range("bytes=60-", 50) == "" and normalize_range("bytes=-0", 50) == ""
    assert normalize_range("bytes=x", 50) is None and normalize_range("pages=1-2", 50) is None
    assert normalize_range("BYTES= 1 - 2 ,4-", 50) == "bytes=1-2,4-49"


@pytest.fixture
def gpu_client(tmp_path, monkeypatch):
    """API with an installed GPU module, a CPU and a GPU worker; denoise uses a fake (loud) backend."""
    m = modules.get("denoise")
    monkeypatch.setattr(m, "backend_factory", fakes.LoudDenoiseBackend)
    monkeypatch.setattr(m, "_backend", None)
    with TestClient(create_app(tmp_path, gpu=fakes.InstalledGpu()), base_url=BASE) as c:
        c.worker = Worker(tmp_path, "cpu", "w-api", poll=0)
        c.gpu_worker = Worker(tmp_path, "gpu", "g-api", poll=0)
        c.gpu_worker.run_once()  # first heartbeat
        yield c


def drain_all(c):
    for _ in range(30):
        if not (c.worker.run_once() | c.gpu_worker.run_once()):
            return


def test_denoise_chain_export_reduces_gain_instead_of_clipping(gpu_client, tmp_path):
    p = tmp_path / "n.wav"
    x = synth.speech_like(seconds=1.0, seed=4)
    sf.write(p, x * (0.8 / np.abs(x).max()), 48000, subtype="PCM_24")  # the fake backend doubles it: peak 1.6
    pid = upload(gpu_client, p).json()["id"]
    drain_all(gpu_client)
    r = gpu_client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch", "denoise"]})
    assert r.status_code == 200, r.text
    drain_all(gpu_client)
    proj = gpu_client.get(f"/api/projects/{pid}").json()
    assert proj["state"] == "done", proj["error"]
    assert proj["result"]["export_gain_db"] < 0
    ex = gpu_client.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert ex["gain_db"] == pytest.approx(proj["result"]["export_gain_db"], abs=0.01) and ex["gain_db"] < 0
    wav = next(f for f in ex["files"] if f["kind"] == "repaired")
    y, _ = sf.read(io.BytesIO(gpu_client.get(wav["url"]).content))
    assert np.abs(y).max() < 1.0 and np.sum(np.abs(y) > 0.999) <= 2  # scaled, not hard-clipped


def _stale_gpu(tmp_path):
    from server import db
    conn = db.connect(tmp_path / "afix.db")
    conn.execute("UPDATE workers SET heartbeat=0 WHERE device='gpu'")
    conn.close()


def test_denoise_run_and_retry_refuse_without_a_live_gpu_worker(gpu_client, tmp_path):
    """final review I4: a gpu job with no consumer would sit in "queued" forever."""
    from server import db
    pid = upload(gpu_client, _clipped(tmp_path)).json()["id"]
    drain_all(gpu_client)
    _stale_gpu(tmp_path)
    r = gpu_client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch", "denoise"]})
    assert r.status_code == 409 and set(r.json()) == SHAPE and r.json()["code"] == "gpu_worker_offline"
    assert gpu_client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch"]}).status_code == 200
    drain(gpu_client)  # CPU-only chains still run
    assert gpu_client.get(f"/api/projects/{pid}").json()["state"] == "done"
    gpu_client.gpu_worker.run_once()  # the GPU worker is back
    assert gpu_client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch", "denoise"]}).status_code == 200
    gpu_client.worker.run_once()  # glitch done; denoise is queued for the GPU
    conn = db.connect(tmp_path / "afix.db")
    conn.execute("UPDATE jobs SET state='failed' WHERE project_id=? AND module='denoise'", (pid,))
    db.update_project(conn, pid, state="failed")
    _stale_gpu(tmp_path)
    r = gpu_client.post(f"/api/projects/{pid}/retry")
    assert r.status_code == 409 and r.json()["code"] == "gpu_worker_offline"
    assert db.jobs_for(conn, pid)[-1]["state"] == "failed"  # nothing requeued
    gpu_client.gpu_worker.run_once()
    assert gpu_client.post(f"/api/projects/{pid}/retry").status_code == 200
    drain_all(gpu_client)
    assert gpu_client.get(f"/api/projects/{pid}").json()["state"] == "done"


# ---------------------------------------------------------------- final review I3: Host / Origin
def _forbidden(r, status):
    assert r.status_code == status and set(r.json()) == SHAPE and r.json()["code"] == "forbidden_origin", r.text


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example:8765", "testserver", "[::2]:8765"])
def test_unknown_host_is_refused(client, host):
    """DNS rebinding: a page on evil.example resolving to 127.0.0.1 sends Host: evil.example."""
    _forbidden(client.get("/api/projects", headers={"Host": host}), 400)
    _forbidden(client.post("/api/modules/denoise/verify", headers={"Host": host}), 400)


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST", "[::1]:8765", "127.0.0.1"])
def test_local_hosts_are_allowed(client, host):
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_extra_hosts_come_from_afix_allowed_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv("AFIX_ALLOWED_HOSTS", " studio.lan , NAS.local")
    with TestClient(create_app(tmp_path), base_url="http://studio.lan:8765") as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/health", headers={"Host": "nas.local"}).status_code == 200
        assert c.get("/api/health", headers={"Host": "127.0.0.1:8765"}).status_code == 200
        _forbidden(c.get("/api/health", headers={"Host": "other.lan"}), 400)


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:9999", "null", "http://localhost:8765"])
def test_cross_origin_state_changes_are_refused(client, tmp_path, origin):
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    h = {"Origin": origin}
    _forbidden(client.post("/api/modules/denoise/verify", headers=h), 403)
    _forbidden(client.delete(f"/api/projects/{pid}", headers=h), 403)
    with open(_clipped(tmp_path, "x.wav"), "rb") as f:
        _forbidden(client.post("/api/projects", files={"file": ("x.wav", f)}, headers=h), 403)
    assert client.get(f"/api/projects/{pid}").status_code == 200  # nothing was deleted
    assert len(client.get("/api/projects").json()["projects"]) == 1
    assert client.get("/api/projects", headers=h).status_code == 200  # reads are guarded by the Host check


def test_same_origin_and_origin_less_requests_work(client, tmp_path):
    with open(_clipped(tmp_path), "rb") as f:
        r = client.post("/api/projects", files={"file": ("a.wav", f)}, headers={"Origin": BASE})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert client.post(f"/api/projects/{pid}/cancel").status_code == 200  # no Origin (curl, the CLI)
    r = client.delete(f"/api/projects/{pid}", headers={"Origin": "http://localhost:5174", "Host": "localhost:5174"})
    assert r.status_code == 200  # the Vite dev proxy keeps the browser's Host


def test_rename_project(client, tmp_path):
    pid = upload(client, _clipped(tmp_path), "a.wav").json()["id"]
    r = client.patch(f"/api/projects/{pid}", json={"name": "  訪談 第2集  "})
    assert r.status_code == 200 and r.json()["name"] == "訪談 第2集" and r.json()["id"] == pid
    assert client.get(f"/api/projects/{pid}").json()["name"] == "訪談 第2集"


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_rename_rejects_bad_names(client, tmp_path, name):
    pid = upload(client, _clipped(tmp_path), "a.wav").json()["id"]
    r = client.patch(f"/api/projects/{pid}", json={"name": name})
    assert r.status_code == 422 and r.json()["code"] == "bad_request"
    assert client.get(f"/api/projects/{pid}").json()["name"] == "a.wav"


def test_rename_unknown_project_is_404(client):
    r = client.patch("/api/projects/abcdef123456", json={"name": "x"})
    assert r.status_code == 404 and r.json()["code"] == "project_not_found"


def test_export_names_follow_rename(client, tmp_path):
    pid = upload(client, _clipped(tmp_path), "a.wav").json()["id"]
    drain(client)
    client.post(f"/api/projects/{pid}/run", json={"modules": ["declip"]}); drain(client)
    client.patch(f"/api/projects/{pid}", json={"name": "新名稱"})
    ex = client.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert next(f for f in ex["files"] if f["kind"] == "repaired")["name"] == "新名稱_afix.wav"


def test_export_name_keeps_dots_after_rename(client, tmp_path):
    pid = upload(client, _clipped(tmp_path), "a.wav").json()["id"]
    drain(client)
    client.post(f"/api/projects/{pid}/run", json={"modules": ["declip"]}); drain(client)
    client.patch(f"/api/projects/{pid}", json={"name": "Interview v2.1"})
    ex = client.post(f"/api/projects/{pid}/export", json={"float": False}).json()
    assert next(f for f in ex["files"] if f["kind"] == "repaired")["name"] == "Interview v2.1_afix.wav"


def test_export_stem_strips_only_known_media_extensions():
    assert export_stem("a.wav") == "a"
    assert export_stem("Mix 1.0 final") == "Mix 1.0 final"
    assert export_stem("x.FLAC") == "x"
    for ext in (".wav", ".flac", ".mp3", ".m4a", ".mp4", ".mkv", ".mov", ".aac", ".ogg", ".opus",
                ".aif", ".aiff", ".wma", ".webm"):
        assert export_stem(f"take{ext.upper()}") == "take"


def test_every_run_has_a_new_rev_so_no_cache_can_serve_the_old_result(client, tmp_path):
    pid = upload(client, _clipped(tmp_path)).json()["id"]
    drain(client)
    client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch"]})
    drain(client)
    first = client.get(f"/api/projects/{pid}").json()["result"]["rev"]
    client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch"]})
    drain(client)
    assert client.get(f"/api/projects/{pid}").json()["result"]["rev"] > first


def test_a_busy_project_carries_step_speeds_so_the_time_left_shows_from_the_start(client, tmp_path):
    earlier = upload(client, _clipped(tmp_path)).json()["id"]
    drain(client)
    client.post(f"/api/projects/{earlier}/run", json={"modules": ["glitch"]})
    drain(client)
    assert "rates" not in client.get(f"/api/projects/{earlier}").json()
    pid = upload(client, _clipped(tmp_path, "b.wav")).json()["id"]
    drain(client)
    client.post(f"/api/projects/{pid}/run", json={"modules": ["glitch"]})
    busy = client.get(f"/api/projects/{pid}").json()
    assert busy["state"] == "processing" and busy["rates"]["repair:glitch"] >= 0
    drain(client)
