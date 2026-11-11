"""Where the work data and the models live, and the app settings kept with the work data.

The work-data and models folders are chosen in this order: the environment (AFIX_DATA, AFIX_MODELS), then the
choice saved from the Settings page (location.json in the user's config folder, read by the launcher at start),
then the defaults (openaudiofix.paths). A folder set in the environment cannot be changed from the UI.

App settings (the default export folder) are kept in <work data>/settings.json, so they move with the data.
The export folder has a default too, except in a container, where nothing outside the mounts would reach the host;
saving null means "download only".
"""
import json
import os
import sys
from pathlib import Path

from openaudiofix import paths

LEGACY_DATA = "data"  # the default before there were per-system ones


def in_container():
    return Path("/.dockerenv").exists() or os.environ.get("AFIX_IN_CONTAINER") == "1"


def default_data():
    """The default work-data folder; a store already in ./data keeps being used."""
    return LEGACY_DATA if (Path(LEGACY_DATA) / "afix.db").is_file() else str(paths.default_data_dir())


def default_export():
    """The default export folder, or None for download only. AFIX_DEFAULT_EXPORT overrides it ("" = none)."""
    d = os.environ.get("AFIX_DEFAULT_EXPORT")
    if d is not None:
        return d or None
    return None if in_container() else str(paths.default_export_dir())


def config_dir():
    d = os.environ.get("AFIX_CONFIG_DIR")
    if d:
        return Path(d)
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "OpenAudioFix"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "openaudiofix"


def location_file():
    return config_dir() / "location.json"


def read_location():
    """{"data": path?, "models": path?} saved from the Settings page; {} when nothing was saved or it is unreadable."""
    try:
        d = json.loads(location_file().read_text(encoding="utf-8"))
        return {k: str(v) for k, v in d.items() if k in ("data", "models") and isinstance(v, str) and v}
    except (OSError, ValueError, AttributeError):
        return {}


def write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def save_location(**kw):
    """Save data= / models= for the next start; None removes a saved choice."""
    cur = read_location()
    for k, v in kw.items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = str(v)
    write_json_atomic(location_file(), cur)


def apply_location(environ=None):
    """Put the saved folders into the environment unless it already names them. Returns {name: source}, where
    source is "env", "settings" or "default". The launcher calls this once, before starting its children."""
    env = os.environ if environ is None else environ
    saved = read_location()
    src = {}
    for key, var in (("data", "AFIX_DATA"), ("models", "AFIX_MODELS")):
        if env.get(var):
            src[key] = "env"
        elif key in saved:
            env[var] = saved[key]
            src[key] = "settings"
        else:
            src[key] = "default"
    if src["data"] == "default":
        env["AFIX_DATA"] = default_data()
    env["AFIX_LOCATION_SOURCES"] = json.dumps(src)
    return src


def sources():
    """How the running server got its folders (see apply_location); "env" when started without the launcher."""
    try:
        return json.loads(os.environ.get("AFIX_LOCATION_SOURCES", ""))
    except ValueError:
        return {"data": "env", "models": "env"}


# ---------------------------------------------------------------- app settings
def read_settings(data_dir):
    try:
        d = json.loads((Path(data_dir) / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"export_dir": d["export_dir"] if "export_dir" in d else default_export()}


def write_settings(data_dir, **kw):
    cur = read_settings(data_dir)
    cur.update({k: v for k, v in kw.items() if k in cur})
    write_json_atomic(Path(data_dir) / "settings.json", cur)
    return cur
