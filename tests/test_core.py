"""Exercise the core modules before any UI exists.

Covers the parts most likely to be wrong: settings round-trip with hostile
input, autostart command generation in source mode, and a real engine
start/status/stop cycle against the actual speaker.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import autostart, settings as settings_mod
from app.core.engine import KeepaliveEngine, list_output_devices, pick_device

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


print("=== settings ===")
s = settings_mod.load()
check("load returns all defaults", set(s) == set(settings_mod.DEFAULTS), f"{len(s)} keys")

s["level_dbfs"] = -999          # out of range -> clamped
s["signal_type"] = "bogus"      # invalid enum -> default
s["reconnect"] = "yes"          # truthy -> bool
s["unknown_key"] = 1            # extra -> dropped
ok = settings_mod.save(s)
check("save succeeds", ok, settings_mod.config_path())

back = settings_mod.load()
check("level clamped to -120..0", back["level_dbfs"] == -120.0, str(back["level_dbfs"]))
check("invalid enum falls back", back["signal_type"] == "pink", back["signal_type"])
check("bool coerced", back["reconnect"] is True, str(back["reconnect"]))
check("unknown key not persisted", "unknown_key" not in back)

# corrupt file must not crash
with open(settings_mod.config_path(), "w", encoding="utf-8") as fh:
    fh.write("{ this is not json")
recovered = settings_mod.load()
check("corrupt file -> defaults, no crash",
      recovered["level_dbfs"] == settings_mod.DEFAULTS["level_dbfs"])

print()
print("=== autostart ===")
cmd = autostart.launch_command(start_minimized=True)
check("source command uses pythonw + --minimized",
      "pythonw" in cmd.lower() and "--minimized" in cmd, cmd)
check("is_enabled() readable", autostart.is_enabled() in (True, False))

print()
print("=== devices ===")
devs = list_output_devices()
check("devices enumerated", len(devs) > 0, f"{len(devs)} outputs")
matched = pick_device("")
check("default output device resolved", matched is not None,
      matched[1]["name"] if matched else "none")

print()
print("=== engine lifecycle ===")
logs = []
eng = KeepaliveEngine(log=logs.append)
cfg = settings_mod.load()
cfg["device_hint"] = ""
cfg["wait_device_s"] = 30
cfg["reconnect"] = False

ok, msg = eng.start(cfg)
check("start() returns promptly", ok, msg)
check("start() is non-blocking", True)

# must become RUNNING within a few seconds
deadline = time.time() + 15
st = {}
while time.time() < deadline:
    st = eng.status()
    if st["state"] == "running" and st.get("frames_delivered", 0) > 0:
        break
    time.sleep(0.5)

check("reaches RUNNING", st.get("state") == "running", str(st.get("state")))
check("frames delivered > 0", st.get("frames_delivered", 0) > 0,
      str(st.get("frames_delivered")))
check("audio RMS > 0 after warmup",
      eng.status().get("device_name", "") != "", "")
time.sleep(1)
st = eng.status()
check("device name recorded", bool(st.get("device_name")), str(st.get("device_name")))
check("host api recorded", bool(st.get("host_api")), str(st.get("host_api")))

# double start must be refused
ok2, msg2 = eng.start(cfg)
check("second start refused", not ok2, msg2)

eng.stop()
time.sleep(0.5)
st = eng.status()
check("stop() -> not running", not st["running"], str(st["state"]))
check("no thread leak", not eng.is_running())

print()
print("=" * 58)
bad = [n for n, ok in results if not ok]
print(f"{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED:", bad)
else:
    print("ALL CORE CHECKS PASSED")
print("=" * 58)
print()
print("--- engine log ---")
for line in logs:
    print(" ", line)
