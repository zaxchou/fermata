"""Persisted settings.

Kept deliberately dumb: a flat dict of primitives, validated on load. A settings
file is the kind of thing users hand-edit and that survives upgrades, so every
read must tolerate missing, extra, or malformed keys instead of crashing the app
at startup.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

from .paths import config_path

DEFAULTS: dict[str, Any] = {
    # Which output device to feed. Case-insensitive substring match.
    # Empty means "any device", which resolves to the system default output --
    # the right default for a tool that should work on any machine.
    "device_hint": "",
    # pink | white | sine
    "signal_type": "pink",
    # dBFS; -60 is inaudible but well above the amp's noise floor
    "level_dbfs": -60.0,
    # sine only
    "freq_hz": 19000.0,
    # 0 = use the device default
    "sample_rate": 0,
    # seconds to wait for the speaker to appear (0 = forever)
    "wait_device_s": 300.0,
    # keep retrying after the stream drops
    "reconnect": True,
    "reconnect_delay_s": 10.0,
    # start feeding as soon as the app launches
    "autostart_engine": True,
    # launch the app itself at logon
    "launch_at_login": False,
    # start hidden to the tray
    "start_minimized": True,
    # closing the window hides it instead of quitting
    "close_to_tray": True,
    # auto = follow the Windows UI language; or force "en" / "zh"
    "language": "auto",
}

_NUMERIC_BOUNDS: dict[str, tuple[float, float]] = {
    "level_dbfs": (-120.0, 0.0),
    "freq_hz": (20.0, 20000.0),
    "sample_rate": (0, 384000),
    "wait_device_s": (0.0, 86400.0),
    "reconnect_delay_s": (1.0, 3600.0),
}

_SIGNAL_TYPES = ("pink", "white", "sine")
_LANGUAGES = ("auto", "en", "zh")


def _coerce(key: str, value: Any) -> Any:
    """Return a safe value for `key`, falling back to the default."""
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        try:
            num = float(value)
        except (TypeError, ValueError):
            return default
        lo, hi = _NUMERIC_BOUNDS.get(key, (float("-inf"), float("inf")))
        num = max(lo, min(hi, num))
        return int(num) if isinstance(default, int) else num
    if isinstance(default, str):
        text = str(value)
        if key == "signal_type" and text not in _SIGNAL_TYPES:
            return default
        if key == "language" and text not in _LANGUAGES:
            return default
        if key == "device_hint":
            text = text.strip() or default
        return text
    return default


def load() -> dict[str, Any]:
    """Read settings, tolerating anything short of an unreadable file."""
    settings = copy.deepcopy(DEFAULTS)
    path = config_path()
    if not os.path.exists(path):
        return settings
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        # A corrupt settings file must not brick the app; defaults win and the
        # bad file is left alone for inspection.
        return settings
    if not isinstance(raw, dict):
        return settings
    for key, value in raw.items():
        if key in DEFAULTS:
            settings[key] = _coerce(key, value)
    return settings


def save(settings: dict[str, Any]) -> bool:
    """Write settings atomically. Returns True on success."""
    clean = {k: _coerce(k, v) for k, v in settings.items() if k in DEFAULTS}
    merged = copy.deepcopy(DEFAULTS)
    merged.update(clean)

    path = config_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
