"""The default folders, in the places each system's users expect.

    Windows   work data  %LOCALAPPDATA%\\OpenAudioFix\\data
              models     %LOCALAPPDATA%\\OpenAudioFix\\models
              exports    Documents\\OpenAudioFix\\Exports
    macOS     work data  ~/Library/Application Support/OpenAudioFix
              models     ~/.cache/openaudiofix/models
              exports    ~/Documents/OpenAudioFix/Exports
    Linux     work data  $XDG_DATA_HOME/openaudiofix (~/.local/share/openaudiofix)
              models     $XDG_CACHE_HOME/openaudiofix/models (~/.cache/openaudiofix/models)
              exports    ~/Documents/OpenAudioFix/Exports
"""
import os
import sys
from pathlib import Path

_CSIDL = {"documents": 5, "music": 13, "desktop": 16}


def user_folder(name):
    """The user's documents, music or desktop folder (Windows asks the system: they can be moved, e.g. to OneDrive)."""
    if sys.platform == "win32":
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, _CSIDL[name], None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    return Path.home() / name.capitalize()


def _local_app_data():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "OpenAudioFix"


def default_data_dir():
    if sys.platform == "win32":
        return _local_app_data() / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenAudioFix"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "openaudiofix"


def _cache_models():
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "openaudiofix" / "models"


def preferred_models_dir():
    """Where models belong on this system."""
    return _local_app_data() / "models" if sys.platform == "win32" else _cache_models()


def default_models_dir():
    """Where models are looked for: the preferred folder, unless they were downloaded before it was the default."""
    new, old = preferred_models_dir(), _cache_models()
    if new != old and not new.exists() and old.is_dir() and any(old.iterdir()):
        return old
    return new


def default_export_dir():
    return user_folder("documents") / "OpenAudioFix" / "Exports"
