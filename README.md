# Fermata

Keeps audio devices out of standby with a signal you cannot hear.

**English** · [简体中文](README.zh-CN.md)

Fermata is a small Windows tray application. It feeds your speaker, DAC, or
Bluetooth receiver a continuous inaudible signal, so the device never decides it
has been idle long enough to power down.

> **Why the name?** A *fermata* is the musical symbol that tells a performer to
> hold a note longer than its written value. That is exactly the trick: hold the
> signal, so the device never sees silence.

---

## The problem

Plenty of audio hardware shuts itself off after a stretch of silence: Bluetooth
speakers drop the link, USB DACs sleep and pop when they wake, soundbars switch
inputs. Many of these timers cannot be disabled at all.

The Marshall Stanmore II is one such case: it enters standby after **20 minutes
without input**, and neither the top-panel knobs nor the vendor's app expose a
setting for it. Fermata was written for that speaker, but nothing in it is
specific to it.

## Why noise, and not an ultrasonic tone

The obvious fix -- play a 20 kHz tone nobody can hear -- fails in practice. This
is the part quick scripts tend to get wrong:

| Approach | What actually happens |
|---|---|
| **20 kHz sine** | Sits at the edge of hearing, and Bluetooth codecs (SBC / aptX) fold content near Nyquist back **into the audible band** as a piercing whistle |
| **5-20 Hz sine** | A high-pass filter in the codec strips it before it ever reaches the amplifier |
| **Digital silence** | Some devices judge standby on silence itself, so this accomplishes nothing |
| **Broadband noise** (what Fermata uses) | However much the codec and the device's own crossovers remove, some band always carries energy, so the device always sees signal |

The default is **pink noise at -60 dBFS**: inaudible in any real room, yet far
above the amplifier's own noise floor.

### How the noise is made

Pink noise, by overlap-adding random-phase spectral frames: each frame draws a
random spectrum, shapes it by `1/sqrt(f)`, inverse transforms it, and blends it
in at a 50 % hop. The result is a genuine random process, not a loop, and the
tilt is exact -- a test asserts it lands at -3 dB/octave.

The first implementation ran a per-sample one-pole filter bank (Paul Kellet's) in
a Python loop: 44 100 interpreter iterations a second, measured at **9.4 % of a
core** for a utility whose entire job is to be invisible. The overlap-add version
measures **0.94 %** and lands at -3.00 dB/oct against the old filter's -2.99.

## Cost

Measured on the author's machine (i7-11700K, 16 threads, Windows 11), as process
CPU time over a 30-second window:

| What | Measured |
|---|---|
| CPU, audio engine only (no GUI) | **0.94 %** of one core |
| CPU, whole app, tray only | **1.9 %** of one core = 0.12 % of this 16-thread CPU |
| CPU, whole app, window open | **3.0 %** of one core |
| Memory, app process | ~130 MB working set |
| Memory, including its WebView2 hosts | ~480-530 MB working set |
| Stream | 44.1 kHz stereo, an ordinary shared-mode WASAPI stream |

The honest reading of that table: **the settings GUI costs more than the audio
does.** If you want no interface at all, a command-line C++ tool is a tenth of
this -- see the comparison further down.

---

## Quick start

Download **[the latest release](https://github.com/zaxchou/fermata/releases/latest)**
and extract the zip, or [build it yourself](#building-from-source).

Then run `Fermata.exe`.

First launch opens the settings window and starts feeding the device right away.
After that:

- **Closing the window keeps it running.** It lives in the tray; use *Quit* in
  the tray menu to actually exit.
- The **tray icon colour is the status**: green = feeding, amber = waiting or
  reconnecting, red = error, grey = stopped.
- Tick **Launch at login** and it starts by itself, minimized to the tray.

It is a folder build, so move the whole folder. The EXE needs its `_internal`
sibling; copying the EXE alone will not work.

---

## Settings

| Setting | What it does |
|---|---|
| **Output device** | Which device to keep awake, chosen by name from the list. *Any device (system default)* resolves to whatever the default output is at that moment -- see the note below. |
| **Name filter** | Used instead, when the device is not in the list -- for example before a Bluetooth speaker has been paired. Case-insensitive substring match; plain text, not a pattern. |
| **Signal type** | Pink (default), white, or sine. Pink is the safe choice -- see above. |
| **Frequency** | Only used by the sine signal. |
| **Level** | -90 to -20 dBFS. **If you can hear it, it is too loud.** |
| **Reconnect automatically** | Keep retrying if the stream drops: device switched off, out of range, and so on. |
| **Retry delay** | Base seconds between retries, backing off to a 60 s maximum. A stream that ran healthily for a minute resets it. |
| **Wait for device** | How long to wait at startup for the device to appear. `0` waits forever. Bluetooth usually reconnects a few seconds after logon. |
| **Launch at login** | Writes the per-user Run key, pointing at the EXE. |
| **Start feeding on launch** | Begin as soon as the app starts, without pressing Start. |
| **Start minimized to tray** | Only applies when launched at login. |
| **Closing hides to tray** | Turn this off and the window's X quits the app and stops the stream. |
| **Language** | *Auto (system)* follows the Windows display language; or pin English / 简体中文. Applies immediately, including the tray menu, without a restart. |

On the level setting: it sets the peak amplitude of the generated waveform, so
pink and white come out at the same RMS (they are both normalised to uniform
white noise) and a sine is 1.8 dB louder in RMS terms at the same setting. The
status tab shows the measured RMS, so you can see what is actually going out.

On the device setting: *Any device (system default)* is resolved **when the
stream starts**, and again on each reconnect -- not continuously. Switching the
Windows default output afterwards does not move the stream. That is deliberate:
if it chased the default, plugging in headphones would abandon the speaker this
tool exists to protect. The consequence worth knowing is that the device a
running instance feeds is decided at startup, so **if you want one specific
device protected, select it by name rather than leaving it on "Any device"**.
Device indices shift between launches (a Bluetooth endpoint that was `#10` can be
`#14` an hour later), which is why the selection is stored as a name.

### Files

| Path | |
|---|---|
| `%APPDATA%\Fermata\settings.json` | Settings. Hand-editable; unknown keys are ignored and out-of-range values are clamped. |
| `%APPDATA%\Fermata\logs\app.log` | Log. Append-only. |
| `%APPDATA%\Fermata\selftest.txt` | Report from the last `--selftest`. |

Set `FERMATA_DATA_DIR` to move all of that somewhere else -- useful for a
portable install on a USB stick, and used by the tests to avoid touching a real
profile.

There is also a `sample_rate` setting that has no UI control (default `0` = use
the device's own rate). Edit it by hand if you need to pin one.

---

## Verifying it works

Fermata ships with a headless check that exercises the real stack: bundled
assets, settings, device enumeration, the autostart command, and an actual audio
stream.

```powershell
.\Fermata.exe --selftest
```

It prints a pass/fail list, writes a report to the data folder, and exits `0`
only if everything passed. That is the point of it: you can confirm the product
works on your machine without opening the window and guessing.

To confirm the device really stays awake: run the app, leave the volume at a
normal level, wait past the device's idle timeout, and check its indicator light
or connection state.

---

## How this compares to similar tools

This space is not empty, and some existing tools are more mature than this one.
Being upfront about that:

| Project | Notes |
|---|---|
| [vrubleg/soundkeeper](https://github.com/vrubleg/soundkeeper) | C++, Windows, no GUI, very configurable, including its own noise modes. The most established option if you want no interface at all. |
| [Deimz/SoundKeeper](https://github.com/Deimz/SoundKeeper) | Python, tray-only, configured by editing the source. |
| [Carve/audio-keep-alive](https://github.com/Carve/audio-keep-alive) | Go, cross-platform tray. |
| **Fermata** | Windows, settings GUI plus tray, autostart, reconnect, and a built-in `--selftest`. Its distinguishing feature is that the signal choice and the failure modes above are documented and testable rather than assumed. |

If a command-line-only C++ tool suits you better, `vrubleg/soundkeeper` is
probably what you want.

---

## Building from source

```
pip install -r requirements.txt

pyinstaller --noconfirm --clean Fermata.spec
# -> dist\Fermata\
```

Python 3.11 or newer. On Windows the settings window uses the WebView2 runtime;
it ships with Windows 11 and with current Windows 10 builds, and otherwise
installs from Microsoft as a small standalone package.

Run without packaging:

```
python app\main.py
python app\main.py --minimized     # tray only, as launch-at-login does
python app\main.py --selftest      # headless check
```

### Tests

Three suites, each isolated to a throwaway data directory, and all of them exit
non-zero on failure. Two caveats: the GUI and EXE suites stop any running
Fermata first (the single-instance guard would otherwise refuse the instance
under test), and the EXE suite puts your Run key back the way it found it.

```
python tests\test_core.py    # 31 checks: settings, autostart, devices, signal
                             # spectrum and level, engine lifecycle
python tests\test_gui.py     #  9 checks: window, tray, docs, single-instance
python tests\test_exe.py     # 15 checks: packaged EXE acceptance
```

`test_exe.py` needs a build; it runs the EXE's own `--selftest`, launches the
GUI, sends a real `WM_CLOSE` to the window to prove that clicking the X hides to
the tray instead of killing the app, and confirms the Run key is repointed at the
packaged EXE.

`test_core.py` asserts the signal itself: the pink spectrum is measured and must
land between -1.5 and -4.5 dB/octave, its RMS must match uniform white, and the
level coming out of a live stream must match the dBFS setting.

---

## A packaging bug worth knowing about

If you build a pywebview + sounddevice app with PyInstaller, you may hit this.

The packaged EXE failed to open the audio stream with

```
PortAudioError: Unanticipated host error [PaErrorCode -9999]
WdmSyncIoctl: DeviceIoControl GLE = 0x00000492
```

while the identical source run worked fine. The cause: **PortAudio's WASAPI
backend requires COM to be initialized on the thread that opens the stream.**
Fermata streams from a worker thread, which has no COM apartment of its own. The
frozen build was strict about it; the source run happened to get away with it,
which is the worst possible shape for a bug, because it appears only after
packaging.

`app/core/engine.py` calls `CoInitializeEx(COINIT_MULTITHREADED)` on the worker
thread before touching PortAudio. Without it, the packaged app streams nothing.

---

## Limits

- **Windows only.** The COM initialization and the Run-key autostart are both
  Windows-specific, and the tests assume Windows.
- **Keep the volume at a normal level.** Most amplifiers decide standby before
  the volume control, but this is not universally documented, so a volume knob at
  zero is untested territory.
- **Devices must already be paired.** Fermata waits for an endpoint to appear; it
  does not pair Bluetooth devices for you.
- **Bluetooth reconnection is at the mercy of the OS.** Fermata waits and
  retries, but it cannot force a link the Bluetooth stack refuses to make.

---

## Not affiliated with Marshall

Marshall and Stanmore are trademarks of their respective owners. Fermata is an
independent project, not affiliated with or endorsed by Marshall. The Stanmore II
is named only to describe the device this was originally built for; the tool
works with any audio output device that has an idle standby timer.

## License

MIT -- see [LICENSE](LICENSE).
