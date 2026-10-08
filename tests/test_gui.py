"""Launch the real GUI app and verify it comes up.

Checks the things a human would look for: the process stays alive, a window
titled "Fermata" actually exists, the log shows the window was created and the
engine streaming, and the single-instance guard rejects a second launch.

The data directory is redirected to a throwaway folder *before* importing the
app, exactly as test_core and test_exe do. It used to write DEFAULTS into the
real profile, so running this test reset the developer's own saved settings.
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

_TMP = tempfile.mkdtemp(prefix="fermata_guitest_")
os.environ["FERMATA_DATA_DIR"] = _TMP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
PYW = os.path.join(os.path.dirname(PY), "pythonw.exe")
sys.path.insert(0, ROOT)

from app.core import settings as settings_mod  # noqa: E402
from app.core.paths import app_data_dir  # noqa: E402

LOG = os.path.join(app_data_dir(), "logs", "app.log")
TITLE = "Fermata"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def kill_stale() -> str:
    """Stop any running instance, source or packaged.

    The packaged app has to go too, not just source runs: it holds the
    single-instance mutex, so the app launched here is refused and every window
    check fails for a reason that looks nothing like the cause.
    """
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process -Name Fermata -ErrorAction SilentlyContinue "
                    "| Stop-Process -Force -ErrorAction SilentlyContinue"],
                   capture_output=True)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' "
                    "or Name='python.exe'\" "
                    "| Where-Object { $_.CommandLine -like '*app*main.py*' } "
                    "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                    "-ErrorAction SilentlyContinue }"],
                   capture_output=True)
    time.sleep(2)
    return "stopped any running Fermata instance (source or packaged)"


def wait_window_gone(timeout: float = 10.0) -> bool:
    """Poll instead of checking once.

    Stop-Process returns as soon as the process object is reaped; the window is
    torn down asynchronously a moment later. A single immediate check reported a
    false failure here.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not user32.FindWindowW(None, TITLE):
            return True
        time.sleep(0.5)
    return False


print(f"test data dir: {_TMP}")
print("resetting settings to defaults in that throwaway directory…")
settings_mod.save(settings_mod.DEFAULTS)
cfg = settings_mod.load()
check("settings written to the throwaway directory",
      os.path.dirname(settings_mod.config_path()) == _TMP,
      settings_mod.config_path())

print(kill_stale())
print()
print("launching GUI (pythonw, no console)…")
CREATE_NO_WINDOW = 0x08000000
p = subprocess.Popen([PYW, os.path.join(ROOT, "app", "main.py")],
                     creationflags=CREATE_NO_WINDOW, cwd=ROOT)
print(f"  pid {p.pid}")
time.sleep(14)

check("process alive (window open)", p.poll() is None, f"pid={p.pid}")
hwnd = user32.FindWindowW(None, TITLE)
check("window exists by title", bool(hwnd), f"hwnd={hwnd}")

print()
print("--- app log ---")
text = ""
if os.path.exists(LOG):
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    for line in text.splitlines()[-14:]:
        print(" ", line)
else:
    print("  (no log file)")

check("log written", bool(text))
check("log shows the window was created", "window created" in text)
check("log shows the engine streaming", "engine: streaming" in text)
check("log shows no stream failure",
      "stream ended" not in text and "Traceback" not in text)

print()
print("--- single instance guard ---")
r = subprocess.run([PY, os.path.join(ROOT, "app", "main.py")],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=60)
check("second launch refused with exit code 4", r.returncode == 4,
      f"rc={r.returncode}")

print()
print("stopping…")
kill_stale()
check("window closed after shutdown", wait_window_gone())

print()
print("=" * 58)
bad = [n for n, ok in results if not ok]
print(f"{len(results) - len(bad)}/{len(results)} checks passed")
print("FAILED: " + ", ".join(bad) if bad else "ALL GUI CHECKS PASSED")
print("=" * 58)

sys.exit(1 if bad else 0)
