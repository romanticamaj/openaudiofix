import numpy as np
import pytest

from openaudiofix.modules.base import Fix
from server import storage as S


def test_layout_stage_roundtrip_and_safe_paths(tmp_path):
    st = S.Storage(tmp_path)
    pid = st.create()
    a = np.random.default_rng(0).standard_normal((100, 2))
    st.save_stage(pid, 0, "glitch", a, [Fix(1, 5, -1, "seam")], {"n_seams": 1})
    assert np.array_equal(st.load(st.stage_path(pid, 0, "glitch")), a)
    fixes, notes = st.load_fixes(pid, 0, "glitch")
    assert fixes == [Fix(1, 5, -1, "seam")] and notes == {"n_seams": 1}
    assert st.safe_file(pid, "stages/0_glitch.npy") is not None
    assert st.safe_file(pid, "../../afix.db") is None and st.safe_file(pid, "nope.wav") is None


def test_clean_intermediates_keeps_original_final_and_chain_diff(tmp_path):
    st = S.Storage(tmp_path)
    pid = st.create()
    for k, m in enumerate(["glitch", "declip"]):
        st.save_stage(pid, k, m, np.zeros((10, 1)), [], {})
    for sid in ("original", "s0", "s1", "d_chain", "d0", "d1"):
        st.preview_path(pid, sid).write_bytes(b"x")
        st.peaks_path(pid, sid).write_text("[]")
    st.save_source(pid, np.zeros((10, 1)))
    st.save_stage(pid, 1, "declip", np.zeros((10, 1)), [], {})
    (st.pdir(pid) / "exports" / "out.wav").write_bytes(b"x")
    (st.pdir(pid) / "cache" / "c.bin").write_bytes(b"x")
    st.clean_intermediates(pid, ["glitch", "declip"])
    assert not st.stage_path(pid, 0, "glitch").exists() and st.stage_path(pid, 1, "declip").exists()
    left = sorted(p.stem for p in (st.pdir(pid) / "preview").iterdir())
    assert left == ["d_chain", "original", "s1"]
    assert sorted(p.stem for p in (st.pdir(pid) / "peaks").iterdir()) == ["d_chain", "original", "s1"]
    assert st.source_path(pid).exists() and (st.pdir(pid) / "fixes" / "0_glitch.json").exists()
    assert (st.pdir(pid) / "exports" / "out.wav").exists()
    assert list((st.pdir(pid) / "cache").iterdir()) == []


def test_estimate_bytes_counts_float64_stages():
    assert S.estimate_bytes(10.0, 48000, 2, 2) == int(10 * 48000 * 2 * 8 * 3 * 1.1)


@pytest.mark.parametrize("rel", [
    "../x", "../../afix.db", "stages/../../afix.db", "stages/../stages/0_glitch.npy", "./stages/0_glitch.npy",
    "/etc/passwd", "//host/share/x", "C:\\x", "C:x", "C:/x", "c:\\windows\\win.ini",
    "stages\\0_glitch.npy", "..\\..\\afix.db", "\\stages\\0_glitch.npy",
    "%2e%2e%2fafix.db", "%2e%2e/afix.db", "stages%2f0_glitch.npy", "stages/0_glitch.npy%00",
    "stages/0_glitch.npy\x00", "stages//0_glitch.npy", "", "stages/",
])
def test_safe_file_rejects_unsafe_paths(tmp_path, rel):
    st = S.Storage(tmp_path)
    pid = st.create()
    st.save_stage(pid, 0, "glitch", np.zeros((4, 1)), [], {})
    (tmp_path / "afix.db").write_bytes(b"secret")
    assert st.safe_file(pid, rel) is None


def test_safe_file_rejects_sibling_project_and_symlink_escape(tmp_path):
    st = S.Storage(tmp_path)
    a, b = st.create(), st.create()
    (st.pdir(b) / "cache" / "f.txt").write_text("x")
    assert st.safe_file(a, f"../{b}/cache/f.txt") is None
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    try:
        (st.pdir(a) / "cache" / "link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert st.safe_file(a, "cache/link.txt") is None


@pytest.mark.parametrize("pid", ["..", "", ".", "a/b", "ABCDEF012345", "0123456789abc", "0123456789a", "../x"])
def test_bad_project_ids_are_refused(tmp_path, pid):
    st = S.Storage(tmp_path)
    good = st.create()
    with pytest.raises(ValueError):
        st.pdir(pid)
    assert st.safe_file(pid, "afix.db") is None
    (tmp_path / "afix.db").write_bytes(b"db")
    with pytest.raises(ValueError):
        st.delete(pid)
    assert (tmp_path / "afix.db").exists() and st.pdir(good).exists()


def test_original_path_extension_is_sanitized(tmp_path):
    st = S.Storage(tmp_path)
    pid = st.create()
    assert st.original_path(pid, "a.MP3").name == "original.mp3"
    assert st.original_path(pid, "a.mp3:x").name == "original.mp3x"
    assert st.original_path(pid, "noext").name == "original.bin"


def test_upload_tmp_path_is_unique_and_under_data_tmp(tmp_path):
    st = S.Storage(tmp_path)
    a, b = st.upload_tmp_path("訪談 第1集.MP3"), st.upload_tmp_path("x/../../evil")
    assert a.parent == tmp_path / "tmp" and a.suffix == ".mp3" and a.name.startswith("upload-")
    assert b.parent == tmp_path / "tmp" and b.suffix == ".bin" and a != st.upload_tmp_path("a.mp3")
