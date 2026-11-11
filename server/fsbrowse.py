"""The folder browser behind "choose a folder" in the UI, and copying exports into the chosen folder.

Only folders are listed, never files. The browser is offered to a browser on the same computer as the server
(see app.can_browse). In a container the listing is confined to AFIX_BROWSE_ROOTS (default: the work-data
folder), the folders mounted from the host.
"""
import os
import shutil
import string
import sys
from pathlib import Path

from openaudiofix import paths


def roots(data_dir, in_container):
    """Starting points: the home folder and the drives (Windows) or / , or the allowed roots in a container."""
    if in_container:
        extra = [p for p in os.environ.get("AFIX_BROWSE_ROOTS", "").split(os.pathsep) if p.strip()]
        return [str(Path(p).resolve()) for p in (extra or [str(data_dir)])]
    out = [str(Path.home())]
    for name in ("documents", "music", "desktop"):
        p = str(paths.user_folder(name))
        if os.path.isdir(p) and p not in out:
            out.append(p)
    if sys.platform == "win32":
        out += [f"{d}:\\" for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]
    else:
        out.append("/")
    return out


def allowed(path, allowed_roots):
    """True when `path` is one of the roots or inside one (None means anything goes)."""
    if allowed_roots is None:
        return True
    p = Path(path).resolve()
    return any(p == Path(r) or Path(r) in p.parents for r in allowed_roots)


def nearest_folder(path):
    """`path`, or its closest parent that exists (a folder not made yet, a mistyped tail); None if there is none."""
    p = Path(path).expanduser()
    while not p.is_dir():
        if p.parent == p:
            return None
        p = p.parent
    return p


def list_dirs(path):
    """{path, parent, dirs: [{name, path}]} for an existing folder; hidden and unreadable entries are left out."""
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise FileNotFoundError(str(p))
    dirs = []
    try:
        with os.scandir(p) as it:
            for e in it:
                if e.name.startswith((".", "$")):
                    continue
                try:
                    if e.is_dir():
                        dirs.append({"name": e.name, "path": str(Path(e.path))})
                except OSError:
                    continue
    except PermissionError:
        pass
    dirs.sort(key=lambda d: d["name"].casefold())
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "dirs": dirs, "writable": os.access(p, os.W_OK)}


def unique_name(folder, name):
    """`name`, or "stem (2).ext", "stem (3).ext"... when the folder already has a file of that name."""
    target = Path(folder) / name
    if not target.exists():
        return target
    stem, ext = os.path.splitext(name)
    n = 2
    while (Path(folder) / f"{stem} ({n}){ext}").exists():
        n += 1
    return Path(folder) / f"{stem} ({n}){ext}"


def copy_into(files, folder):
    """Copy each file into `folder` without overwriting anything there; returns the paths written."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for f in files:
        dst = unique_name(folder, Path(f).name)
        tmp = dst.with_name(dst.name + ".part")
        try:
            shutil.copyfile(f, tmp)
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)
        out.append(str(dst))
    return out
