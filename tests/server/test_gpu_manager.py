import re
from pathlib import Path

import pytest

from server import db, errors, gpu_manager as GM
from server.storage import Storage
from tests.server.fake_gpu import FakeGpu, Spawner


@pytest.fixture
def make(tmp_path):
    def _make(**kw):
        st = Storage(tmp_path)
        st.free_bytes = lambda: int(100e9)
        fake, spawn = FakeGpu(**kw), Spawner()
        m = GM.GpuManager(lambda: db.connect(st.db_path), st, gpu=fake, spawn=spawn)
        return m, fake, spawn
    return _make


def run(m, action):
    assert m.start(action)
    m._thread.join(10)


def test_precheck_reports_gpu_size_and_space(make):
    m, fake, _ = make()
    m.precheck()
    s = m.state()
    assert s["state"] == "ready_to_download" and s["step"] == "confirm"
    d = s["detail"]
    assert (d["gpu"], d["vram"], d["driver"]) == ("RTX Test", "16303 MiB", "591.86")
    assert d["download_bytes"] == fake.ESTIMATED_BYTES and d["enough_space"] is True and d["local_image"] is False


def test_no_nvidia_gpu_blocks(make):
    m, _, _ = make(gpu=False)
    m.precheck()
    s = m.state()
    assert s["state"] == "blocked" and s["detail"]["code"] == "nvidia_missing"


def test_too_little_space_is_shown_and_rechecked_before_download(make):
    m, fake, _ = make()
    m.storage.free_bytes = lambda: int(5e9)  # < 3.6 GB * SPACE_FACTOR
    m.precheck()
    assert m.state()["detail"]["enough_space"] is False
    m.storage.free_bytes = lambda: int(100e9)
    m.precheck()
    m.storage.free_bytes = lambda: int(1e9)
    m.download()
    s = m.state()
    assert s["state"] == "ready_to_download" and s["detail"]["code"] == "not_enough_space"
    assert "install" not in fake.calls


def test_install_flow_installs_tests_and_starts_the_worker(make):
    m, fake, spawn = make()
    m.precheck()
    m.download()
    s = m.state()
    assert s["state"] == "installed" and s["step"] == "done" and s["detail"]["speed_x"] == 40.0
    assert fake.calls[-3:] == ["install", "weights", "selftest"]
    assert len(spawn.procs) == 1 and m.worker_running()


def test_download_progress_is_recorded(make, monkeypatch):
    m, fake, _ = make()
    seen = []
    orig = m._set

    def spy(state, step=None, **detail):
        if state == "downloading":
            seen.append((detail.get("phase"), detail.get("pct")))
        return orig(state, step, **detail)
    monkeypatch.setattr(m, "_set", spy)
    m.precheck()
    m.download()
    pcts = [p for _, p in seen]
    assert pcts == sorted(pcts) and pcts[-1] == 95.0 and seen[-1][0] == "weights"


def test_packages_already_there_skip_the_download(make):
    m, fake, _ = make(present=True)
    m.precheck()
    d = m.state()["detail"]
    assert d["local_image"] is True and d["download_bytes"] == 0 and d["enough_space"] is True
    m.download()
    assert m.state()["state"] == "installed" and "install" not in fake.calls and "weights" in fake.calls


def test_pip_failure_is_reported_at_download(make):
    m, fake, spawn = make()
    fake.install_error = "pip install failed:\nERROR: no matching distribution"
    m.precheck()
    m.download()
    s = m.state()
    assert s["state"] == "failed" and s["step"] == "download" and s["detail"]["code"] == "install_failed"
    assert "no matching distribution" in s["detail"]["error"] and not spawn.procs


def test_failed_selftest_stops_the_worker(make):
    m, fake, spawn = make(present=True, selftest_ok=False)
    m.verify()
    s = m.state()
    assert s["state"] == "failed" and s["detail"]["code"] == "selftest_failed" and s["detail"]["error"] == "boom"
    assert not m.worker_running()


def test_verify_without_packages_says_so(make):
    m, _, _ = make()
    m.verify()
    s = m.state()
    assert s["state"] == "failed" and s["detail"]["code"] == "packages_missing"


def test_remove_stops_the_worker_and_uninstalls(make):
    m, fake, spawn = make(present=True)
    m.verify()
    m.remove()
    assert m.state()["state"] == "not_installed" and "uninstall" in fake.calls
    assert spawn.procs[0].terminated and not m.worker_running()


def test_inside_a_docker_image_nothing_is_installed_or_removed(make):
    m, fake, _ = make(disabled=True)
    m.precheck()
    s = m.state()
    assert s["state"] == "blocked" and s["detail"]["code"] == "install_disabled" and s["detail"]["managed"] == "image"
    m2, fake2, _ = make(disabled=True, present=True)
    m2.verify()
    assert m2.state()["state"] == "installed" and m2.state()["detail"]["managed"] == "image"
    m2.remove()
    assert "uninstall" not in fake2.calls


def test_startup_enables_packages_that_are_already_installed(make):
    m, fake, spawn = make(present=True)
    m.startup()
    m._thread.join(10)
    assert m.state()["state"] == "installed" and m.worker_running()


@pytest.mark.parametrize("left", [("blocked", "precheck", {"code": "docker_unavailable"}),
                                  ("failed", "verify", {"code": "selftest_failed"}),
                                  ("downloading", "download", {"pct": 40})])
def test_startup_retests_installed_packages_whatever_was_left_behind(make, left):
    m, fake, spawn = make(present=True)
    db.set_module_state(m.conn, GM.MID, *left)
    m.startup()
    m._thread.join(10)
    assert m.state()["state"] == "installed" and m.worker_running()


def test_startup_without_a_gpu_leaves_the_module_alone(make):
    m, fake, spawn = make(present=True, gpu=False)
    m.startup()
    assert m.state()["state"] == "not_installed" and not spawn.procs


def test_startup_restarts_the_worker_of_an_installed_module(make):
    m, fake, spawn = make(present=True)
    db.set_module_state(m.conn, GM.MID, "installed", "done", {"gpu": "RTX Test"})
    m.startup()
    for _ in range(100):
        if spawn.procs:
            break
        __import__("time").sleep(0.05)
    assert len(spawn.procs) == 1 and "selftest" not in fake.calls


def test_startup_marks_an_interrupted_step_failed(make):
    m, _, _ = make()
    db.set_module_state(m.conn, GM.MID, "downloading", "download", {"pct": 40})
    m.startup()
    s = m.state()
    assert s["state"] == "failed" and s["detail"]["code"] == "interrupted"


def test_quick_recheck_fails_when_the_packages_are_gone(make):
    m, fake, spawn = make(present=True)
    m.verify()
    fake.present = False
    assert m.quick_recheck() is False
    assert m.state()["detail"]["code"] == "packages_missing" and not m.worker_running()


def test_tick_restarts_a_dead_worker_then_gives_up(make, monkeypatch):
    m, fake, spawn = make(present=True)
    m.verify()
    clock = [m._last_spawn]
    monkeypatch.setattr(GM.time, "time", lambda: clock[0])
    for k in range(GM.MAX_RESTARTS):
        spawn.procs[-1].alive = False
        clock[0] += GM.RESTART_EVERY_S + 1
        m.tick()
        if k < GM.MAX_RESTARTS - 1:
            assert m.state()["state"] == "installed" and m.worker_running()
    s = m.state()
    assert s["state"] == "failed" and s["detail"]["code"] == "worker_lost"


def test_tick_leaves_a_live_worker_alone(make):
    m, _, spawn = make(present=True)
    m.verify()
    m.tick()
    assert len(spawn.procs) == 1


def test_actions_run_one_at_a_time_and_download_needs_the_confirm_state(make):
    m, _, _ = make()
    assert m.start("download") is False
    run(m, "precheck")
    assert m.state()["state"] == "ready_to_download"
    run(m, "download")
    assert m.state()["state"] == "installed"
    with pytest.raises(ValueError):
        m.start("format_disk")


def test_unexpected_error_becomes_failed_at_its_step(make, monkeypatch):
    m, fake, _ = make()
    monkeypatch.setattr(fake, "nvidia", lambda: 1 / 0)
    m.precheck()
    s = m.state()
    assert s["state"] == "failed" and s["step"] == "precheck" and s["detail"]["code"] == "internal_error"


def test_shutdown_stops_the_worker(make):
    m, _, spawn = make(present=True)
    m.verify()
    m.shutdown()
    assert spawn.procs[0].terminated


def test_every_recorded_code_is_known():
    src = Path(GM.__file__).read_text(encoding="utf-8")
    codes = set(re.findall(r'code="([a-z_]+)"', src)) | {"nvidia_missing"}
    assert codes and codes <= set(errors.CODES), codes - set(errors.CODES)


def test_every_recorded_step_is_listed_in_steps():
    """The UI translates gpu.step.<step> for every STEPS entry (web i18n test)."""
    src = Path(GM.__file__).read_text(encoding="utf-8")
    steps = set(re.findall(r'_set\(\s*"[a-z_]+",\s*"([a-z_]+)"', src)) | set(GM._ACTION_STEP.values())
    steps |= set(re.findall(r'return "[a-z_]+", "([a-z_]+)"', src))  # selftest_outcome
    assert {"precheck", "confirm", "download", "start", "verify", "done"} <= steps
    assert steps <= set(GM.STEPS), steps - set(GM.STEPS)


def test_selftest_outcome_maps_results():
    assert GM.selftest_outcome({"ok": True, "gpu": "g", "speed_x": 3.0})[0] == "installed"
    assert GM.selftest_outcome({"ok": True}, exit_code=1)[0] == "failed"
    assert GM.selftest_outcome("nope")[2]["code"] == "selftest_failed"
