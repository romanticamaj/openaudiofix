"""The /data layout. One folder per project; afix.db at the root."""
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

import numpy as np

from openaudiofix.io import save_npy, tmp_path_for
from openaudiofix.modules.base import Fix

PID_RE = re.compile(r"[0-9a-f]{12}")
SUBDIRS = ("stages", "fixes", "preview", "peaks", "exports", "cache")


def write_text_atomic(path, text):
    """Write UTF-8 text through a unique temp file + os.replace (spec 4.2)."""
    tmp = tmp_path_for(path)
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def estimate_bytes(duration_s, sr, channels, n_stages):
    return int(duration_s * sr * channels * 8 * (n_stages + 1) * 1.1)


class Storage:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "projects").mkdir(parents=True, exist_ok=True)
        self.tmp_dir = self.root / "tmp"
        self.tmp_dir.mkdir(exist_ok=True)
        self.db_path = self.root / "afix.db"

    def pdir(self, pid):
        if not isinstance(pid, str) or not PID_RE.fullmatch(pid):
            raise ValueError(f"invalid project id: {pid!r}")
        return self.root / "projects" / pid

    def create(self):
        pid = uuid.uuid4().hex[:12]
        for d in SUBDIRS:
            (self.pdir(pid) / d).mkdir(parents=True, exist_ok=True)
        return pid

    @staticmethod
    def safe_ext(filename):
        ext = re.sub(r"[^a-z0-9]", "", os.path.splitext(filename)[1].lower())[:10]
        return "." + ext if ext else ".bin"

    def original_path(self, pid, filename):
        return self.pdir(pid) / f"original{self.safe_ext(filename)}"

    def upload_tmp_path(self, filename):
        """Where an upload is streamed before it is probed (no project folder exists yet)."""
        return self.tmp_dir / f"upload-{uuid.uuid4().hex[:12]}{self.safe_ext(filename)}"

    def source_path(self, pid):
        return self.pdir(pid) / "source.npy"

    def stage_path(self, pid, seq, mid):
        return self.pdir(pid) / "stages" / f"{seq}_{mid}.npy"

    def preview_path(self, pid, src_id):
        return self.pdir(pid) / "preview" / f"{src_id}.webm"

    def peaks_path(self, pid, src_id):
        return self.pdir(pid) / "peaks" / f"{src_id}.json"

    @staticmethod
    def load(path):
        return np.load(str(path), mmap_mode="r")

    def save_source(self, pid, audio):
        save_npy(self.source_path(pid), audio)

    def save_stage(self, pid, seq, mid, audio, fixes, notes):
        save_npy(self.stage_path(pid, seq, mid), audio)
        p = self.pdir(pid) / "fixes" / f"{seq}_{mid}.json"
        write_text_atomic(p, json.dumps({"fixes": [asdict(f) for f in fixes], "notes": notes}, ensure_ascii=False))

    def load_fixes(self, pid, seq, mid):
        d = json.loads((self.pdir(pid) / "fixes" / f"{seq}_{mid}.json").read_text(encoding="utf-8"))
        return [Fix(**f) for f in d["fixes"]], d["notes"]

    def reset_outputs(self, pid):
        for d in SUBDIRS:
            p = self.pdir(pid) / d
            shutil.rmtree(p, ignore_errors=True)
            p.mkdir(parents=True, exist_ok=True)
        for f in ("fixes.csv", "report.json"):
            (self.pdir(pid) / f).unlink(missing_ok=True)

    def clean_intermediates(self, pid, modules):
        last = len(modules) - 1
        for k, m in enumerate(modules[:-1]):
            self.stage_path(pid, k, m).unlink(missing_ok=True)
        keep = {"original", f"s{last}", "d_chain"}
        for sub, ext in (("preview", ".webm"), ("peaks", ".json")):
            for p in (self.pdir(pid) / sub).glob(f"*{ext}"):
                if p.stem not in keep:
                    p.unlink()
        shutil.rmtree(self.pdir(pid) / "cache", ignore_errors=True)
        (self.pdir(pid) / "cache").mkdir(exist_ok=True)

    @staticmethod
    def _tree_size(top):
        total = 0
        for f in top.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except FileNotFoundError:
                pass
        return total

    def size(self, pid):
        return self._tree_size(self.pdir(pid))

    def total_size(self):
        return self._tree_size(self.root)

    def free_bytes(self):
        return shutil.disk_usage(self.root).free

    def delete(self, pid):
        shutil.rmtree(self.pdir(pid), ignore_errors=True)

    def safe_file(self, pid, rel):
        """Resolve a project-relative path; None when it is unsafe, escapes the project folder or is not a file."""
        if not isinstance(rel, str) or not rel or "\\" in rel or "%" in rel or "\0" in rel or ":" in rel:
            return None
        if rel.startswith("/") or any(part in ("", ".", "..") for part in rel.split("/")):
            return None
        try:
            base = self.pdir(pid).resolve()
        except ValueError:
            return None
        p = (base / rel).resolve()
        try:
            p.relative_to(base)
        except ValueError:
            return None
        return p if p.is_file() else None
