"""Launch-at-login, via the per-user Run key.

Two modes, and the difference matters:

  frozen (packaged EXE)  the Run entry points at the EXE itself.
  source (running .py)   the Run entry points at pythonw.exe + the entry script,
                         because there is no EXE to point at yet.

Getting this wrong is invisible in testing and only shows up as "autostart
silently stopped working after I packaged it", so the two cases are computed
explicitly rather than assumed.
"""
from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Fermata"


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def launch_command(start_minimized: bool = True) -> str:
    """The exact command line to store in the Run key."""
    args = " --minimized" if start_minimized else ""
    if _is_frozen():
        return f'"{sys.executable}"{args}'

    # Source mode: prefer pythonw.exe so no console window flashes at logon.
    exe_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(exe_dir, "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    entry = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "main.py")
    return f'"{pythonw}" "{entry}"{args}'


def is_enabled() -> bool:
    """True if our Run entry exists and matches what we would write now."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def current_value() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except OSError:
        return ""


def enable(start_minimized: bool = True) -> tuple[bool, str]:
    """Create/update the Run entry. Returns (ok, message)."""
    command = launch_command(start_minimized)
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        return True, command
    except OSError as exc:
        return False, str(exc)


def disable() -> tuple[bool, str]:
    """Remove the Run entry. Succeeds even if it was already absent."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
        return True, "removed"
    except OSError as exc:
        return False, str(exc)


def should_refresh() -> bool:
    """True when the stored entry should be repointed at where we are now.

    The direction matters, and getting it wrong breaks an installed product:

      source -> packaged   correct upgrade; repoint at the EXE. This is the
                           case the refresh exists for.
      packaged -> source   NOT an upgrade. Running the source tree once (for
                           development or testing) must never steal autostart
                           from an installed build and point the login entry at
                           a source directory that may move or be deleted.
    """
    if not is_enabled():
        return False

    current = current_value().strip()
    if current == launch_command().strip():
        return False

    if not _is_frozen():
        # We are running from source. Leave any installed EXE alone.
        lowered = current.lower()
        if ".exe" in lowered and "pythonw" not in lowered:
            return False
    return True
