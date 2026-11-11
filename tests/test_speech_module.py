import io

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from openaudiofix import chain, modules, speech as SP
from openaudiofix.errors import InputError
from server.app import create_app
from server.worker import Worker

SR = 48000


@pytest.fixture
def fake_vad(monkeypatch):
    """Speech wherever the 16 kHz mix is louder than -30 dBFS; no model, no download."""
    class Fake:
        def __init__(self, path):
            pass

        def probs(self, x16, progress=None):
            n = len(x16) // SP.CHUNK
            rms = np.sqrt((x16[:n * SP.CHUNK].reshape(n, -1) ** 2).mean(axis=1))
            return (rms > 10 ** (-30 / 20)).astype(np.float32)
    monkeypatch.setattr(SP, "SileroVAD", Fake)
    monkeypatch.setattr(SP, "ensure_model", lambda *a, **k: "fake.onnx")


def _talk(seconds=12.0, turns=((1.0, 3.0), (6.0, 8.5))):
    """Noise floor with two loud 'turns' of a tone; stereo."""
    rng = np.random.default_rng(0)
    n = int(seconds * SR)
    x = 1e-4 * rng.standard_normal((n, 2))
    t = np.arange(n) / SR
    for a, b in turns:
        m = (t >= a) & (t < b)
        x[m, 0] += 0.5 * np.sin(2 * np.pi * 220 * t[m])
        x[m, 1] += 0.4 * np.sin(2 * np.pi * 330 * t[m])
    return x


def test_speech_runs_last_and_keeps_the_gain():
    assert [m.id for m in modules.ordered(["speech", "declip", "glitch"])] == ["declip", "glitch", "speech"]
    assert chain.reduce_gain_for(["speech"]) is False


def test_speech_module_silences_only_inside_its_fixes(fake_vad):
    x = _talk()
    res = modules.get("speech").repair(x, SR, {}, lambda **k: None)
    assert res.audio.shape == x.shape
    # every changed sample is inside a fix: the chain's integrity check passes
    chain.verify_unchanged(x, res.audio, res.fixes)
    # before turn 1, between the turns, after turn 2
    assert [f.kind for f in res.fixes] == ["removed"] * 3 and res.fixes[0].start == 0 and res.fixes[-1].end == len(x)
    # the margins: kept from 50 ms before a turn to 100 ms after it (the fades lie inside the kept span)
    assert 1.0 - 0.07 <= res.fixes[0].end / SR <= 1.0 - 0.02 and 3.0 + 0.08 <= res.fixes[1].start / SR <= 3.0 + 0.14
    mid = slice(int(4.0 * SR), int(5.0 * SR))
    assert not res.audio[mid].any()
    turn = slice(int(1.0 * SR), int(3.0 * SR))
    assert np.array_equal(res.audio[turn], x[turn])
    assert res.notes["n_removed"] == 3 and res.notes["removed_s"] > 6


PARAMS_CONTRACT = {
    "type": "object",
    "properties": {
        "sensitivity": {"type": "string", "enum": ["low", "mid", "high"], "default": "mid"},
        "pad_before_ms": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 50},
        "pad_after_ms": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 100},
        "min_gap_ms": {"type": "integer", "minimum": 0, "maximum": 2000, "default": 300},
    },
    "additionalProperties": False,
}


def test_params_schema_is_the_contract_the_ui_draws():
    m = modules.get("speech")
    assert m.params == PARAMS_CONTRACT and m.describe()["params"] == PARAMS_CONTRACT


def test_params_change_margins_and_are_noted(fake_vad):
    x = _talk()
    m = modules.get("speech")
    res = m.repair(x, SR, {"pad_before_ms": 300, "pad_after_ms": 500}, lambda **k: None)
    chain.verify_unchanged(x, res.audio, res.fixes)
    assert res.fixes[0].end / SR == pytest.approx(0.7 + 0.02, abs=0.02)        # + the fade inside the span
    assert res.fixes[1].start / SR == pytest.approx(3.5 - 0.02, abs=0.04)
    assert res.notes["params"] == {"sensitivity": "mid", "pad_before_ms": 300, "pad_after_ms": 500, "min_gap_ms": 300}
    # a pause shorter than min_gap is kept whole: one turn, two fixes
    res = m.repair(x, SR, {"min_gap_ms": 2000, "pad_before_ms": 500, "pad_after_ms": 500}, lambda **k: None)
    assert len(res.fixes) == 2
    hi = m.repair(x, SR, {"sensitivity": "high"}, lambda **k: None).notes
    assert (hi["open_db"], hi["close_db"]) == (14.0, 6.0)


@pytest.mark.parametrize("bad", [{"pad_before_ms": -1}, {"pad_after_ms": 1001}, {"min_gap_ms": 2001},
                                 {"min_gap_ms": "300"}, {"pad_before_ms": 1.5}, {"pad_before_ms": True},
                                 {"sensitivity": "max"}, {"breaths": True}])
def test_bad_params_are_refused(fake_vad, bad):
    with pytest.raises(InputError) as e:
        modules.get("speech").repair(_talk(), SR, bad, lambda **k: None)
    assert e.value.code == "bad_request"


def test_notes_and_progress(fake_vad):
    msgs = []
    res = modules.get("speech").repair(_talk(), SR, {}, lambda pct=None, message=None: msgs.append(message))
    n = res.notes
    for k in ("floor_db", "speech_floor_db", "open_db", "close_db", "islands", "n_speech_spans", "n_removed",
              "removed_s", "speech_s", "params"):
        assert k in n, k
    assert n["islands"]["speech"] == 2 and set(n["islands"]) == set(SP.KINDS)
    assert n["floor_db"] < -70 and n["speech_s"] == pytest.approx(4.5, abs=0.1)
    assert {"estimating noise floor", "detecting speech", "classifying"} <= set(msgs)
    __import__("json").dumps(n)          # the notes go into report.json


def test_speech_export_gives_speech_removed_and_labels(fake_vad, tmp_path):
    src = tmp_path / "ep.wav"
    x = _talk()
    sf.write(src, x, SR, subtype="PCM_24")
    x24, _ = sf.read(src)
    with TestClient(create_app(tmp_path / "data"), base_url="http://127.0.0.1:8765") as c:
        w = Worker(tmp_path / "data", "cpu", "w", poll=0)
        with open(src, "rb") as f:
            pid = c.post("/api/projects", files={"file": ("ep.wav", f, "audio/wav")}).json()["id"]
        while w.run_once():
            pass
        p = c.get(f"/api/projects/{pid}").json()
        assert p["state"] == "ready" and not p["scan"]["speech"]["detected"]
        assert c.post(f"/api/projects/{pid}/run", json={"modules": ["speech"]}).status_code == 200
        while w.run_once():
            pass
        p = c.get(f"/api/projects/{pid}").json()
        assert p["state"] == "done", p.get("error")
        assert p["result"]["export_gain_db"] == 0.0 and p["result"]["counts"]["speech"] == 3
        ex = c.post(f"/api/projects/{pid}/export", json={"float": False}).json()
        files = {f["kind"]: f for f in ex["files"]}
        assert set(files) == {"speech", "removed", "labels", "csv", "report"}
        assert files["speech"]["name"] == "ep_afix_speech.wav" and files["removed"]["name"] == "ep_afix_removed.wav"
        sp, _ = sf.read(io.BytesIO(c.get(files["speech"]["url"]).content))
        rm, _ = sf.read(io.BytesIO(c.get(files["removed"]["url"]).content))
        assert sp.shape == rm.shape == x24.shape
        assert np.abs(sp + rm - x24).max() <= 2 * 2 ** -23     # the two tracks add up to the original
        assert ex["byte_exact"]                               # speech regions are the original bytes
        labels = c.get(files["labels"]["url"]).content.decode("utf-8").splitlines()
        assert len(labels) == 3 and labels[0].startswith("0.000000\t") and labels[0].endswith("\tremoved")
        fl = c.post(f"/api/projects/{pid}/export", json={"float": True}).json()
        assert {f["name"] for f in fl["files"]} >= {"ep_afix_speech_float.wav", "ep_afix_removed_float.wav"}


def test_speech_after_declip_removes_from_the_declipped_audio(fake_vad, tmp_path, monkeypatch):
    """With another module first, the removed track is the speech step's own input minus its output."""
    from openaudiofix.modules import declip as D
    from openaudiofix.modules.base import RepairResult

    def louder(self, audio, sr, params, progress):  # stands in for declip: raises the peaks above full scale
        return RepairResult(np.asarray(audio) * 1.5, [], {})
    monkeypatch.setattr(D.DeclipModule, "repair", louder)
    src = tmp_path / "hot.wav"
    sf.write(src, _talk() * 1.6, SR, subtype="PCM_24")
    with TestClient(create_app(tmp_path / "data"), base_url="http://127.0.0.1:8765") as c:
        w = Worker(tmp_path / "data", "cpu", "w", poll=0)
        with open(src, "rb") as f:
            pid = c.post("/api/projects", files={"file": ("hot.wav", f, "audio/wav")}).json()["id"]
        while w.run_once():
            pass
        c.post(f"/api/projects/{pid}/run", json={"modules": ["speech", "declip"]})
        monkeypatch.setattr(chain, "verify_unchanged", lambda *a: None)  # the stand-in changes every sample
        while w.run_once():
            pass
        ex = c.post(f"/api/projects/{pid}/export", json={"float": False}).json()
        assert ex["gain_db"] < 0
        files = {f["kind"]: f for f in ex["files"]}
        sp, _ = sf.read(io.BytesIO(c.get(files["speech"]["url"]).content))
        rm, _ = sf.read(io.BytesIO(c.get(files["removed"]["url"]).content))
        x, _ = sf.read(src)
        louder_x = x * 1.5
        g = 1 / np.abs(louder_x).max()  # the loud turns are kept, so this is the speech file's peak too
        assert ex["gain_db"] == pytest.approx(20 * np.log10(g), abs=0.01)
        assert np.abs(sp + rm - louder_x * g).max() <= 4 * 2 ** -23


def test_web_export_labels_name_what_was_removed_like_the_command_line(fake_vad, tmp_path):
    x = _talk()
    a = int(4.4 * SR)
    burst = 10 ** (-36 / 20) * np.random.default_rng(1).standard_normal((int(0.4 * SR), 2))
    x[a:a + len(burst)] += burst
    p = tmp_path / "t.wav"
    sf.write(p, x, SR, subtype="PCM_24")
    with TestClient(create_app(tmp_path / "d"), base_url="http://127.0.0.1:8765") as c:
        w = Worker(tmp_path / "d", "cpu", "w-lbl", poll=0)
        pid = c.post("/api/projects", files={"file": ("t.wav", p.read_bytes(), "audio/wav")}).json()["id"]
        while w.run_once():
            pass
        c.post(f"/api/projects/{pid}/run", json={"modules": ["speech"]})
        while w.run_once():
            pass
        ex = c.post(f"/api/projects/{pid}/export", json={"float": False}).json()
        url = next(f["url"] for f in ex["files"] if f["kind"] == "labels")
        names = [ln.split("\t")[2] for ln in c.get(url).text.splitlines()]
    assert "noise" in names and "removed" in names  # the gap with the burst, and the gaps of room only
