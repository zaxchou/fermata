"""Where the app keeps its state on disk.

Config and logs live under %APPDATA% rather than next to the EXE: the app may
be installed somewhere read-only (Program Files), and per-user state has no
business being there anyway.
"""
from __future__ import annotations

import os
import sys

APP_NAME = "Fermata"


def app_data_dir() -> str:
    """Directory for settings and logs.

    Overridable with FERMATA_DATA_DIR. That serves two purposes: the tests can
    run against a throwaway directory instead of the real one (one of them
    deliberately writes a corrupt settings file, and doing that to a live
    profile is not acceptable), and it gives a portable mode for free.
    """
    override = os.environ.get("FERMATA_DATA_DIR")
    if override:
        path = override
    else:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(app_data_dir(), "settings.json")


def log_path() -> str:
    logs = os.path.join(app_data_dir(), "logs")
    os.makedirs(logs, exist_ok=True)
    return os.path.join(logs, "app.log")


def bundle_dir() -> str:
    """Directory holding bundled data files (UI assets, icon).

    Under PyInstaller onefile the data is unpacked to sys._MEIPASS; in a normal
    run it sits next to the package. Getting this wrong yields a blank window
    that works in development and fails only after packaging.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ui_file(name: str) -> str:
    return os.path.join(bundle_dir(), "ui", name)
