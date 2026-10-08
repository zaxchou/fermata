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


def launch_target() -> str:
    """The program to launch, without the --minimized flag."""
    if _is_frozen():
        return f'"{sys.executable}"'

    # Source mode: prefer pythonw.exe so no console window flashes at logon.
    exe_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(exe_dir, "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    entry = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "main.py")
    return f'"{pythonw}" "{entry}"'


def launch_command(start_minimized: bool = True) -> str:
    """The exact command line to store in the Run key."""
    args = " --minimized" if start_minimized else ""
    return launch_target() + args


def _without_flags(command: str) -> str:
    return command.strip().removesuffix("--minimized").strip()


def is_ours() -> bool:
    """True when the Run entry launches *this* program, ignoring its flags.

    Comparing whole command lines instead is what made the "start minimized"
    checkbox unable to reach the registry: the stored entry only carries
    --minimized when the setting was on, so an entry written the other way
    looked like somebody else's and was left alone.
    """
    if not is_enabled():
        return False
    return _without_flags(current_value()) == launch_target()


def is_enabled() -> bool:
    """True if our Run entry exists.

    Note "exists", not "is correct": a stale entry pointing at a moved install
    still counts as enabled, which is what the UI checkbox should reflect.
    should_refresh() answers the other question.
    """
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


def should_refresh(start_minimized: bool = True) -> bool:
    """True when the stored entry should be rewritten: wrong target or wrong flags.

    The direction matters, and getting it wrong breaks an installed product:

      source -> packaged   correct upgrade; repoint at the EXE. This is the
                           case the refresh exists for.
      packaged -> source   NOT an upgrade. Running the source tree once (for
                           development or testing) must never steal autostart
                           from an installed build and point the login entry at
                           a source directory that may move or be deleted.

    Flags count too: turning "start minimized" off has to reach the registry,
    otherwise the checkbox and the login behaviour disagree.
    """
    if not is_enabled():
        return False

    current = current_value().strip()
    if current == launch_command(start_minimized).strip():
        return False

    if not _is_frozen():
        # We are running from source. Leave any installed EXE alone.
        lowered = current.lower()
        if ".exe" in lowered and "pythonw" not in lowered:
            return False
    return True
