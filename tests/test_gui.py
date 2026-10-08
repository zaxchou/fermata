"""Launch the real GUI app and verify it comes up.

Checks the things a human would look for: the process stays alive (window open),
a WebView2 host appears (the UI actually rendered), the tray icon thread started,
and the log shows the window was created. Also verifies the single-instance
guard rejects a second launch.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
PYW = os.path.join(os.path.dirname(PY), "pythonw.exe")
sys.path.insert(0, ROOT)

from app.core import settings as settings_mod  # noqa: E402
from app.core.paths import app_data_dir  # noqa: E402

LOG = os.path.join(app_data_dir(), "logs", "app.log")

print("resetting settings to defaults…")
settings_mod.save(settings_mod.DEFAULTS)
cfg_defaults = settings_mod.load()
print("  level_dbfs =", cfg_defaults["level_dbfs"])

if os.path.exists(LOG):
    os.remove(LOG)

print()
print("launching GUI (pythonw, no console)…")
CREATE_NO_WINDOW = 0x08000000
p = subprocess.Popen([PYW, os.path.join(ROOT, "app", "main.py")],
                     creationflags=CREATE_NO_WINDOW, cwd=ROOT)
print("  pid", p.pid)
time.sleep(14)

alive = p.poll() is None
print("  process alive:", alive)


def count(image_name: str) -> int:
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image_name}",
                        "/FO", "CSV", "/NH"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return sum(1 for line in r.stdout.splitlines()
               if line.startswith(f'"{image_name.split(".")[0]}'))


print("  msedgewebview2 processes:", count("msedgewebview2.exe"))

print()
print("--- app log ---")
if os.path.exists(LOG):
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        for line in fh.read().splitlines()[-14:]:
            print(" ", line)
else:
    print("  (no log file)")

print()
print("--- single instance guard ---")
r = subprocess.run([PY, os.path.join(ROOT, "app", "main.py")],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=60)
print("  second launch exit code:", r.returncode, "(4 = correctly refused)")

print()
print("stopping…")
subprocess.run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" "
                "| Where-Object { $_.CommandLine -like '*app*main.py*' } "
                "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
               capture_output=True)
print("done")
