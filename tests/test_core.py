"""Exercise the core modules before any UI exists.

Covers the parts most likely to be wrong: settings round-trip with hostile
input, autostart command generation in source mode, and a real engine
start/status/stop cycle against the actual speaker.
"""
import json
import os
import sys
import tempfile
import time

import numpy as np

# Redirect the app's data directory BEFORE importing anything that reads it.
# One of the checks below writes a deliberately corrupt settings file, and doing
# that to the real profile would destroy the user's configuration -- which is
# exactly what happened before this was isolated.
_TMP = tempfile.mkdtemp(prefix="fermata_test_")
os.environ["FERMATA_DATA_DIR"] = _TMP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import autostart, settings as settings_mod
from app.core.engine import (NOISE_RMS, KeepaliveEngine, NoiseGenerator,
                             dbfs_to_amplitude, list_output_devices, pick_device)

print(f"test data dir: {_TMP}")
print()

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
uniq = list_output_devices(unique=True)
check("duplicates collapsed per host API", len(uniq) <= len(devs),
      f"{len(devs)} -> {len(uniq)}")
matched = pick_device("")
check("default output device resolved", matched is not None,
      matched[1]["name"] if matched else "none")
# Names contain parentheses, which used to be interpreted as regex groups.
paren = next((d["name"] for d in uniq if "(" in d["name"]), None)
if paren:
    check("device name with brackets matches literally",
          pick_device(paren) is not None, paren)
check("unmatched filter returns None", pick_device("no-such-device-zzz") is None)
if paren:
    check("filter is case-insensitive",
          pick_device(paren.upper()) is not None, paren.upper())

print()
print("=== signal ===")


def spectral_slope(x, sample_rate, f_lo=80.0, f_hi=12000.0):
    """Least-squares tilt of the magnitude spectrum, in dB per octave."""
    win = np.hanning(len(x))
    spec = np.abs(np.fft.rfft(x * win))
    freqs = np.fft.rfftfreq(len(x), 1.0 / sample_rate)
    band = (freqs >= f_lo) & (freqs <= f_hi)
    db = 20.0 * np.log10(np.maximum(spec[band], 1e-30))
    return float(np.polyfit(np.log2(freqs[band] / f_lo), db, 1)[0])


SR = 44100
gen = NoiseGenerator("pink", SR)
N = 1 << 19
blocks = [gen.generate(4096, 1.0) for _ in range(N // 4096)]
sig = np.concatenate(blocks).astype(np.float64)
sig = sig[N // 4:]                     # drop the start-up ramp
rms = float(np.sqrt(np.mean(sig ** 2)))
slope = spectral_slope(sig, SR)
check("pink output is -3 dB/octave",
      -4.5 < slope < -1.5, f"{slope:+.2f} dB/oct")
check("pink RMS matches uniform white",
      abs(rms - NOISE_RMS) / NOISE_RMS < 0.10, f"{rms:.4f} vs {NOISE_RMS:.4f}")
check("pink is broadband, not silent", np.count_nonzero(sig) > len(sig) * 0.99)
check("pink does not repeat block for block",
      not np.array_equal(blocks[1], blocks[2]))

white = NoiseGenerator("white", SR).generate(1 << 16, 1.0).astype(np.float64)
check("white RMS is 1/sqrt(3) at unit amplitude",
      abs(float(np.sqrt(np.mean(white ** 2))) / NOISE_RMS - 1) < 0.05,
      f"{np.sqrt(np.mean(white ** 2)):.4f}")
sine = NoiseGenerator("sine", SR, 1000.0).generate(1 << 16, 1.0).astype(np.float64)
check("sine RMS is 1/sqrt(2) at unit amplitude",
      abs(float(np.sqrt(np.mean(sine ** 2))) * np.sqrt(2) - 1) < 0.02,
      f"{np.sqrt(np.mean(sine ** 2)):.4f}")

print()
print("=== engine lifecycle ===")
logs = []
eng = KeepaliveEngine(log=logs.append)
cfg = settings_mod.load()
cfg["device_hint"] = ""
cfg["wait_device_s"] = 30
cfg["reconnect"] = False
cfg["level_dbfs"] = -60.0

t0 = time.monotonic()
ok, msg = eng.start(cfg)
elapsed = time.monotonic() - t0
check("start() returns promptly", ok, msg)
check("start() does not block on the device", elapsed < 1.0, f"{elapsed*1000:.0f} ms")

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
check("device name recorded", bool(st.get("device_name")), str(st.get("device_name")))
check("host api recorded", bool(st.get("host_api")), str(st.get("host_api")))

# Wait out the 1.5 s fade-in before judging the level: the reported RMS is
# measured after the envelope is applied, so during the ramp it is lower.
time.sleep(3.0)
st = eng.status()
expected = dbfs_to_amplitude(cfg["level_dbfs"]) * NOISE_RMS
measured = st.get("audio_rms", 0.0)
check("streamed level matches the dBFS setting",
      abs(measured - expected) / expected < 0.15,
      f"rms {measured:.2e} vs {expected:.2e} expected")

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

# Exit non-zero on failure. Without this the suite reported the same status
# whether it passed or failed, which makes it useless from a script or CI.
sys.exit(1 if bad else 0)
