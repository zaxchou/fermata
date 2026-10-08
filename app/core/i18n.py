"""Localisation for the parts of the app that are not the web window.

The settings window carries its own translation table in JavaScript, so it can
render instantly without waiting for a round-trip. Only the tray menu lives in
Python, and it needs a handful of strings -- keeping that table here avoids
pulling a whole i18n framework in for six labels.

Language resolution lives here too, so both halves agree on what "auto" means.
"""
from __future__ import annotations

import ctypes

DEFAULT_LANGUAGE = "en"
SUPPORTED = ("en", "zh")

# Tray strings. Keys are shared with the JS table by convention, not by code.
_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "tray.show": "Show settings",
        "tray.start": "Start keeping awake",
        "tray.stop": "Stop keeping awake",
        "tray.autostart": "Launch at login",
        "tray.quit": "Quit",
        "state.running": "Active",
        "state.waiting": "Waiting for device",
        "state.reconnecting": "Reconnecting",
        "state.error": "Error",
        "state.stopped": "Stopped",
    },
    "zh": {
        "tray.show": "打开设置",
        "tray.start": "开始保持唤醒",
        "tray.stop": "停止保持唤醒",
        "tray.autostart": "开机自启",
        "tray.quit": "退出",
        "state.running": "运行中",
        "state.waiting": "等待设备",
        "state.reconnecting": "重连中",
        "state.error": "出错",
        "state.stopped": "已停止",
    },
}

_current = DEFAULT_LANGUAGE


def _system_language() -> str:
    """Map the Windows UI language to one we ship."""
    try:
        # GetUserDefaultUILanguage returns a LANGID; the low 10 bits are the
        # primary language, and 0x04 is Chinese (any region: Simplified,
        # Traditional, Hong Kong, Macao all share it).
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        primary = langid & 0x3FF
        if primary == 0x04:
            return "zh"
    except Exception:
        pass
    return DEFAULT_LANGUAGE


def resolve(setting: str) -> str:
    """Turn the stored preference into a concrete language code."""
    value = (setting or "auto").strip().lower()
    if value in SUPPORTED:
        return value
    return _system_language()


def set_language(setting: str) -> str:
    """Apply a preference and return the language actually in use."""
    global _current
    _current = resolve(setting)
    return _current


def current() -> str:
    return _current


def t(key: str) -> str:
    """Look up a string, falling back to English and then to the key itself."""
    table = _STRINGS.get(_current) or _STRINGS[DEFAULT_LANGUAGE]
    if key in table:
        return table[key]
    return _STRINGS[DEFAULT_LANGUAGE].get(key, key)
