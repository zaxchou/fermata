"""Acceptance test for the packaged EXE.

Assumes nothing. Verifies the frozen build the way a user would exercise it:

  1. --selftest inside the EXE reports the whole stack healthy
  2. launching it opens a real window (process + WebView2 evidence)
  3. clicking the window's X hides to tray instead of killing the app
     (simulated with a real WM_CLOSE, not a synthetic destroy call)
  4. the autostart entry migrates to the EXE path, so at-logon launch survives
     packaging -- the classic silent breakage
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "Fermata", "Fermata.exe")

# Isolate the app's data directory. The EXE inherits this environment, so it
# reads and writes the throwaway directory instead of the real profile, and the
# test can seed the exact preconditions it needs.
_TMP = tempfile.mkdtemp(prefix="fermata_exetest_")
os.environ["FERMATA_DATA_DIR"] = _TMP

sys.path.insert(0, ROOT)

LOG = os.path.join(_TMP, "logs", "app.log")
TITLE = "Fermata"
WM_CLOSE = 0x0010

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def kill_stale():
    """Stop any running app or CLI instance.

    Deliberately targets names rather than scanning every process for a
    CommandLine match: the broad scan silently failed to kill a running
    instance (a scheduled-task launch), and the leftover process then held the
    single-instance mutex. The next launch was refused, the window spotted by
    the test belonged to the old process, and the run reported three misleading
    failures. Kill by name and verify.
    """
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process -Name Fermata -ErrorAction SilentlyContinue "
                    "| Stop-Process -Force -ErrorAction SilentlyContinue"],
                   capture_output=True)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" "
                    "| Where-Object { $_.CommandLine -like '*keepalive*' } "
                    "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                    "-ErrorAction SilentlyContinue }"],
                   capture_output=True)
    for _ in range(10):
        time.sleep(0.5)
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Fermata.exe",
                            "/FO", "CSV", "/NH"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if not any(l.startswith('"Fermata') for l in r.stdout.splitlines()):
            return
    print("  ! warning: an instance is still running after kill attempts")


def run_reg_query() -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows"
                        "\\CurrentVersion\\Run' -Name Fermata "
                        "-ErrorAction SilentlyContinue).Fermata"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.stdout.strip()


print("EXE:", EXE)
if not os.path.exists(EXE):
    print("!! EXE not built")
    sys.exit(1)
print("size: %.1f MB" % (os.path.getsize(EXE) / 1048576))

kill_stale()

# Establish the preconditions explicitly rather than inheriting whatever the
# real profile happened to hold. Step 4 is only meaningful if autostart is
# wanted and the Run entry starts out absent, so the app has to restore it.
from app.core import autostart, settings as settings_mod  # noqa: E402
_seed = settings_mod.load()
_seed["launch_at_login"] = True
settings_mod.save(_seed)
autostart.disable()
print(f"seeded settings in {settings_mod.config_path()}")
print(f"run entry cleared; autostart wanted = {_seed['launch_at_login']}")

# Read only NEW log lines instead of deleting the file. The app may still hold
# the log open (and on this machine a delete of an open file fails), so
# truncating would be both destructive and unreliable. Remember the current
# size and read from there.
LOG_OFFSET = os.path.getsize(LOG) if os.path.exists(LOG) else 0


def log_since_marker() -> str:
    if not os.path.exists(LOG):
        return ""
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        fh.seek(LOG_OFFSET)
        return fh.read()


# --- 1. selftest inside the frozen EXE -------------------------------------
print()
print("=== 1. EXE --selftest ===")
r = subprocess.run([EXE, "--selftest"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=180)
out = (r.stdout or "") + (r.stderr or "")
check("EXE selftest exits 0", r.returncode == 0, f"rc={r.returncode}")
check("selftest found the UI asset", "ui asset present" in out)
check("selftest streamed audio", "[PASS] audio flowing" in out)
if r.returncode != 0:
    print("---- selftest output ----")
    print(out[-2000:])

# --- 2. GUI launch ---------------------------------------------------------
print()
print("=== 2. GUI launch ===")
p = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
time.sleep(16)

alive = p.poll() is None
check("process alive (window open)", alive, f"pid={p.pid}")

r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq msedgewebview2.exe",
                    "/FO", "CSV", "/NH"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
wv = sum(1 for line in r.stdout.splitlines() if line.startswith('"msedge'))
check("WebView2 host present (UI rendered)", wv > 0, f"{wv} processes")

hwnd = user32.FindWindowW(None, TITLE)
check("window found by title", bool(hwnd), f"hwnd={hwnd}")
if hwnd:
    check("window is visible", bool(user32.IsWindowVisible(hwnd)))

text = log_since_marker()
check("log file written this run", bool(text), f"{len(text)} chars")
check("log shows window created", "window created" in text)
check("log shows engine streaming", "engine: streaming" in text)
check("log shows no stream failure",
      "stream ended" not in text and "Traceback" not in text)

# --- 3. close to tray ------------------------------------------------------
print()
print("=== 3. clicking the X hides to tray ===")
if hwnd:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(4)
    still = p.poll() is None
    vis = bool(user32.IsWindowVisible(hwnd)) if hwnd else True
    check("process survives the close (not killed)", still)
    check("window is hidden", not vis, f"visible={vis}")
else:
    check("close-to-tray test", False, "no window handle")

# --- 4. autostart migrates to the EXE -------------------------------------
print()
print("=== 4. autostart entry ===")
value = run_reg_query()
check("Run entry points at the EXE",
      value.lower().endswith("fermata.exe\"") or "dist\\Fermata" in value
      or "dist/Fermata" in value,
      value)
check("Run entry carries --minimized", "--minimized" in value, value)

kill_stale()

print()
print("=" * 60)
bad = [n for n, ok in results if not ok]
print(f"{len(results) - len(bad)}/{len(results)} checks passed")
print("FAILED: " + ", ".join(bad) if bad else "ALL EXE CHECKS PASSED")
print("=" * 60)
