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
from app.core.engine import (NOISE_RMS, _API_ORDER, _is_hands_free,
                             KeepaliveEngine, NoiseGenerator,
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

# Bluetooth hands-free endpoints are 8/16 kHz mono and opening one can force the
# headset out of A2DP into HFP. Windows exposes them through the raw driver
# layer with stripped or driver-qualified names, so they are excluded by name.
HFP = ("耳机 (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0\r\n;"
       ";(WH-1000XM4))")
check("hands-free endpoint recognised (driver-qualified name)",
      _is_hands_free(HFP))
check("hands-free endpoint recognised (plain name)",
      _is_hands_free("Headset (Hands-Free)"))
check("a normal device name is not mistaken for one",
      not _is_hands_free("扬声器 (STANMORE II)")
      and not _is_hands_free("Speakers (Realtek Speaker)")
      and not _is_hands_free("Headphones (High Fidelity Audio)"))
listed = [d["name"] for d in uniq]
check("no hands-free endpoint is offered in the picker",
      not any(_is_hands_free(n) for n in listed),
      f"{len(listed)} devices listed")
check("no hands-free endpoint is reachable by name filter",
      pick_device("WH-1000XM4") is None)
check("WDM-KS ranks below the shared-mode APIs",
      _API_ORDER.index("Windows WDM-KS") > _API_ORDER.index("MME"),
      " -> ".join(_API_ORDER))

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


def read(gen, total, block=4096):
    """Pull `total` samples out of a generator, as a plain array."""
    return np.concatenate([gen.block(block) for _ in range(total // block)]).astype(np.float64)


SR = 44100
pink = NoiseGenerator("pink", SR, amplitude=1.0)
period = pink.period
check("pink builds a loop", bool(period), f"{period} samples = {period/SR:.2f}s")

N = 1 << 18                       # stays inside one period
sig = read(pink, N)
rms = float(np.sqrt(np.mean(sig ** 2)))
slope = spectral_slope(sig, SR)
check("pink output is -3 dB/octave",
      -4.5 < slope < -1.5, f"{slope:+.2f} dB/oct")
check("pink RMS matches uniform white",
      abs(rms - NOISE_RMS) / NOISE_RMS < 0.10, f"{rms:.4f} vs {NOISE_RMS:.4f}")
check("pink is broadband, not silent", np.count_nonzero(sig) > len(sig) * 0.99)

# The loop must be periodic, and the wrap must be indistinguishable from any
# other sample transition. Both fall out of building it circularly; asserting
# them is what stops a future edit from quietly turning it into a cut-and-paste
# loop with a click at the seam.
two = read(NoiseGenerator("pink", SR, amplitude=1.0), period * 2)
check("the loop repeats exactly after one period",
      np.array_equal(two[:period], two[period:]),
      f"period {period}")
loop = two[:period]
steps = np.abs(np.diff(loop))
seam = abs(float(loop[0] - loop[-1]))
check("the wrap step is no worse than the largest natural step",
      seam <= float(steps.max()),
      f"seam {seam:.2e} vs max natural step {steps.max():.2e}")

white = read(NoiseGenerator("white", SR, amplitude=1.0), 1 << 16)
check("white RMS is 1/sqrt(3) at unit amplitude",
      abs(float(np.sqrt(np.mean(white ** 2))) / NOISE_RMS - 1) < 0.05,
      f"{np.sqrt(np.mean(white ** 2)):.4f}")
sine = read(NoiseGenerator("sine", SR, 1000.0, amplitude=1.0), 1 << 16)
check("sine RMS is 1/sqrt(2) at unit amplitude",
      abs(float(np.sqrt(np.mean(sine ** 2))) * np.sqrt(2) - 1) < 0.02,
      f"{np.sqrt(np.mean(sine ** 2)):.4f}")
check("sine has no loop (phase stays continuous)", NoiseGenerator("sine", SR).period is None)

# The level is baked into the loop, so the callback does no arithmetic. Check the
# scaling actually lands.
loud = read(NoiseGenerator("pink", SR, amplitude=1e-2), 1 << 16)
check("the loop carries the requested level",
      abs(float(np.sqrt(np.mean(loud ** 2))) / (NOISE_RMS * 1e-2) - 1) < 0.05,
      f"rms {np.sqrt(np.mean(loud ** 2)):.2e}")

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
