# Fermata

Keeps audio devices out of standby with a signal you cannot hear.

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

### Cost

| Metric | Measured |
|---|---|
| Memory | ~8 MB resident |
| CPU | ~0.1% of one core |
| Stream | 44.1 kHz stereo, a normal A2DP/audio stream |

---

## Quick start

Run `Fermata.exe`.

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
| **Output device** | Which device to keep awake. Matched by name, so it survives the index changing when a device reconnects. Empty means the system default output. |
| **Name filter** | Fallback match string when the exact device is not currently listed. |
| **Signal type** | Pink (default), white, or sine. Pink is the safe choice -- see above. |
| **Frequency** | Only used by the sine signal. |
| **Level** | -90 to -20 dBFS. **If you can hear it, it is too loud.** |
| **Reconnect automatically** | Keep retrying if the stream drops: device switched off, out of range, and so on. |
| **Retry delay** | Base seconds between retries, backing off to a 60 s maximum. |
| **Wait for speaker** | How long to wait at startup for the device to appear. `0` waits forever. Bluetooth usually reconnects a few seconds after logon. |
| **Launch at login** | Writes the per-user Run key, pointing at the EXE. |
| **Start feeding on launch** | Begin as soon as the app starts, without pressing Start. |
| **Start minimized to tray** | Only applies when launched at login. |
| **Closing hides to tray** | Turn this off and the window's X quits the app and stops the stream. |

Settings: `%APPDATA%\Fermata\settings.json`

Log: `%APPDATA%\Fermata\logs\app.log`

---

## Verifying it works

Fermata ships with a headless check that exercises the real stack: bundled
assets, settings, device enumeration, the autostart command, and an actual audio
stream.

```powershell
.\Fermata.exe --selftest
```

It prints a pass/fail list, writes a report to `%APPDATA%\Fermata\selftest.txt`,
and exits `0` only if everything passed.

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

```
python tests\test_core.py    # settings, autostart, engine lifecycle
python tests\test_gui.py     # GUI launch, tray, single-instance guard
python tests\test_exe.py     # packaged EXE acceptance (needs a build)
```

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
