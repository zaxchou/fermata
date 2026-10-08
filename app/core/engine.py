"""The audio engine, wrapped so a GUI can drive it.

The proven signal logic is unchanged from the CLI version (broadband pink noise
at -60 dBFS, WASAPI preferred over MME). What is new is the shape: a
`KeepaliveEngine` object that runs the stream on a worker thread and exposes
start/stop/status, so the UI thread never blocks on audio.

Threading contract, which is the whole point of this class:

  * `start()` and `stop()` are called from the UI thread and return promptly.
  * All audio work happens on one worker thread, created per run.
  * `status()` is safe to call from anywhere at any time.
  * The worker owns the PortAudio stream for its entire life and is the only
    code that closes it. Closing a stream from a different thread than the one
    driving it is a classic source of hangs on Windows.
"""
from __future__ import annotations

import ctypes
import re
import threading
import time
from typing import Any, Callable

import numpy as np
import sounddevice as sd

FADE_S = 1.5
BLOCK_FRAMES = 4096
HEARTBEAT_POLL_S = 0.5

# A device that cannot be opened immediately is retried on this cadence.
DEVICE_POLL_S = 3.0

# COM apartment for the audio thread. WASAPI is a COM API and PortAudio requires
# the thread that opens a stream to have COM initialized; the multithreaded
# apartment is the documented choice for audio worker threads.
COINIT_MULTITHREADED = 0x0
_RPC_E_CHANGED_MODE = 0x80010106


def _com_initialize() -> bool:
    """Initialize COM on the calling thread.

    Returns True when this call owns a reference and the thread must call
    CoUninitialize afterwards.

    Why this is not optional: PortAudio's WASAPI backend fails with
    "Unanticipated host error (WdmSyncIoctl, DeviceIoControl)" when the stream
    is opened from a thread with no COM apartment. A PyInstaller build hit this
    every time while the identical source run happened to work, which is the
    worst kind of bug -- it appears only after packaging. Initializing COM
    explicitly fixes it in both modes, verified on a frozen probe.
    """
    try:
        ole32 = ctypes.WinDLL("ole32")
        ole32.CoInitializeEx.restype = ctypes.c_long
        hr = ole32.CoInitializeEx(None, COINIT_MULTITHREADED) & 0xFFFFFFFF
    except Exception:
        return False
    # S_OK (0) / S_FALSE (1): we hold a reference -> uninitialize later.
    # RPC_E_CHANGED_MODE: already initialized as STA. Streaming works there too,
    # but the reference is not ours, so we must not balance it.
    return hr in (0, 1)


def _com_uninitialize() -> None:
    try:
        ctypes.WinDLL("ole32").CoUninitialize()
    except Exception:
        pass


# --------------------------------------------------------------------------
# signal generation (unchanged from the CLI implementation)
# --------------------------------------------------------------------------

class NoiseGenerator:
    """Streaming per-sample generator; one value is produced per frame."""

    def __init__(self, kind: str, sample_rate: int, freq: float = 19000.0):
        self.kind = kind
        self.sample_rate = sample_rate
        self.freq = freq
        self.phase = 0.0
        # Paul Kellet economy pink filter: -3 dB/octave, negligible CPU.
        self._pink = np.zeros(7)

    def _pink_sample(self) -> float:
        white = float(np.random.uniform(-1.0, 1.0))
        b0, b1, b2, b3, b4, b5, b6 = self._pink
        b0 = 0.99886 * b0 + white * 0.0555179
        b1 = 0.99332 * b1 + white * 0.0750759
        b2 = 0.96900 * b2 + white * 0.1538520
        b3 = 0.86650 * b3 + white * 0.3104856
        b4 = 0.55000 * b4 + white * 0.5329522
        b5 = -0.7616 * b5 - white * 0.0168980
        b6 = white * 0.115926
        self._pink = (b0, b1, b2, b3, b4, b5, b6)
        return b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362

    def generate(self, frames: int, amplitude: float) -> np.ndarray:
        if self.kind == "pink":
            out = np.fromiter((self._pink_sample() for _ in range(frames)),
                              dtype=np.float32, count=frames)
            return (out / 3.0 * amplitude).astype(np.float32)
        if self.kind == "white":
            return (np.random.uniform(-1.0, 1.0, frames).astype(np.float32)
                    * amplitude)
        step = 2.0 * np.pi * self.freq / self.sample_rate
        idx = self.phase + step * np.arange(frames, dtype=np.float64)
        self.phase = float(idx[-1] % (2.0 * np.pi))
        return (np.sin(idx) * amplitude).astype(np.float32)


def dbfs_to_amplitude(dbfs: float) -> float:
    return float(10.0 ** (dbfs / 20.0))


# --------------------------------------------------------------------------
# device discovery
# --------------------------------------------------------------------------

def list_output_devices(unique: bool = False) -> list[dict[str, Any]]:
    """Every output-capable device, with the host API and a default marker.

    unique=True collapses the per-host-API duplicates. One physical device shows
    up once per host API (MME, DirectSound, WASAPI) with the same name, and
    listing all of them in a picker is noise -- worse, it invites picking the
    MME entry, which is not what the engine will use, because pick_device ranks
    WASAPI first. Collapsing keeps the view honest: one row per device, naming
    the host API that will actually be selected.
    """
    devices: list[dict[str, Any]] = []
    try:
        default_idx = sd.default.device[1]
    except Exception:
        default_idx = -1
    try:
        hostapis = sd.query_hostapis()
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_output_channels", 0) < 1:
                continue
            api = hostapis[dev["hostapi"]]["name"]
            devices.append({
                "index": idx,
                "name": dev["name"],
                "host_api": api,
                "channels": int(dev["max_output_channels"]),
                "sample_rate": int(dev["default_samplerate"] or 0),
                "is_default": idx == default_idx,
            })
    except Exception:
        return []

    if not unique:
        return devices

    # Best (lowest) rank wins for a given name; the rank order must match
    # pick_device, or the picker would advertise a host API the engine does not use.
    order = {"Windows WASAPI": 0, "Windows DirectSound": 1, "MME": 2}

    def rank(d: dict[str, Any]) -> tuple[int, int]:
        for name, tier in order.items():
            if d["host_api"].startswith(name):
                return tier, d["index"]
        return 9, d["index"]

    best: dict[str, dict[str, Any]] = {}
    for d in devices:
        cur = best.get(d["name"])
        if cur is None:
            best[d["name"]] = d
            continue
        if rank(d) < rank(cur):
            d["is_default"] = d["is_default"] or cur["is_default"]
            best[d["name"]] = d
        else:
            cur["is_default"] = cur["is_default"] or d["is_default"]
    return sorted(best.values(), key=lambda d: d["index"])


def _api_name(index: int) -> str:
    dev = sd.query_devices(index)
    return sd.query_hostapis()[dev["hostapi"]]["name"]


def pick_device(hint: str) -> tuple[int, dict] | None:
    """Best host-API view of the endpoint matching `hint`.

    A Bluetooth speaker appears once per host API and they behave differently:
    WASAPI (shared) mixes cleanly with other players and starts without a
    glitch; MME is last resort because it collides with other players and
    underflows on the first block. Rank WASAPI > DirectSound > MME, then
    prefer the endpoint Windows actually routes audio to.
    """
    hint = (hint or "").strip()
    matches: list[tuple[int, dict]] = []
    try:
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_output_channels", 0) < 1:
                continue
            if not hint or re.search(hint, dev["name"], re.IGNORECASE):
                matches.append((idx, dev))
    except Exception:
        return None
    if not matches:
        return None

    def rank(idx: int) -> tuple[int, int]:
        try:
            api = _api_name(idx)
        except Exception:
            return 9, idx
        for tier, name in enumerate(("Windows WASAPI", "Windows DirectSound",
                                     "MME")):
            if api.startswith(name):
                return tier, idx
        return 9, idx

    best = min(rank(i)[0] for i, _ in matches)
    pool = [(i, d) for i, d in matches if rank(i)[0] == best]
    if len(pool) == 1:
        return pool[0]

    try:
        default_idx = sd.default.device[1]
        for i, d in pool:
            if i == default_idx:
                return i, d
        default_name = sd.query_devices(kind="output")["name"]
        for i, d in pool:
            if d["name"] == default_name:
                return i, d
    except Exception:
        pass
    return pool[0]


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------

STATE_STOPPED = "stopped"
STATE_WAITING = "waiting"
STATE_RUNNING = "running"
STATE_RECONNECTING = "reconnecting"
STATE_ERROR = "error"


class KeepaliveEngine:
    """Runs the keepalive stream on a worker thread until told to stop."""

    def __init__(self, log: Callable[[str], None] | None = None):
        self._log = log or (lambda _msg: None)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = STATE_STOPPED
        self._detail = ""
        self._stats: dict[str, Any] = {}
        self._started_mono = 0.0

    # -- public API --------------------------------------------------------

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, settings: dict[str, Any]) -> tuple[bool, str]:
        """Begin feeding the speaker. Returns (ok, message)."""
        if self.is_running():
            return False, "already running"

        self._stop_event.clear()
        self._started_mono = time.monotonic()
        self._set_state(STATE_WAITING, "starting")
        self._stats = {}

        thread = threading.Thread(target=self._worker, args=(dict(settings),),
                                  name="keepalive-worker", daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()
        return True, "started"

    def stop(self, timeout: float = 6.0) -> None:
        """Ask the worker to finish and wait briefly for it."""
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._thread = None
        self._set_state(STATE_STOPPED, "stopped")

    def status(self) -> dict[str, Any]:
        """Snapshot for the UI. Safe from any thread."""
        with self._lock:
            state = self._state
            detail = self._detail
            stats = dict(self._stats)
            running = self._thread is not None and self._thread.is_alive()
        uptime = (time.monotonic() - self._started_mono) if running else 0.0
        return {
            "running": running,
            "state": state,
            "detail": detail,
            "uptime_s": round(uptime, 1),
            **stats,
        }

    # -- internals ---------------------------------------------------------

    def _set_state(self, state: str, detail: str = "") -> None:
        with self._lock:
            self._state = state
            self._detail = detail

    def _set_stats(self, **kwargs: Any) -> None:
        with self._lock:
            self._stats.update(kwargs)

    def _worker(self, settings: dict[str, Any]) -> None:
        """Owns the stream lifecycle: wait for device, stream, maybe retry.

        COM is initialized here, on this thread, before any stream is opened --
        see _com_initialize for why that is mandatory rather than cosmetic.
        """
        own_com = _com_initialize()
        try:
            self._worker_body(settings)
        finally:
            if own_com:
                _com_uninitialize()

    def _worker_body(self, settings: dict[str, Any]) -> None:
        hint = settings.get("device_hint", "")
        wait_s = float(settings.get("wait_device_s", 300.0))
        reconnect = bool(settings.get("reconnect", True))
        backoff = max(1.0, float(settings.get("reconnect_delay_s", 10.0)))
        attempt = 0

        while not self._stop_event.is_set():
            picked = self._await_device(hint, wait_s)
            if picked is None:
                if not reconnect:
                    self._set_state(STATE_ERROR, f"no device matching '{hint}'")
                    self._log(f"give up: no device matching '{hint}'")
                    break
                attempt += 1
                delay = min(backoff * attempt, 60.0)
                self._set_state(STATE_WAITING,
                                f"no device yet, retry in {delay:.0f}s")
                if self._stop_event.wait(delay):
                    break
                continue

            index, info = picked
            self._set_stats(device_name=info.get("name", ""))
            result = self._run_stream(index, info, settings)
            if result == "stopped":
                break
            if not reconnect:
                self._set_state(STATE_ERROR, result)
                self._log(f"stream ended: {result}")
                break

            attempt += 1
            delay = min(backoff * attempt, 60.0)
            self._set_state(STATE_RECONNECTING,
                            f"{result}; retry in {delay:.0f}s")
            self._log(f"stream lost ({result}); retry in {delay:.0f}s")
            if self._stop_event.wait(delay):
                break

        with self._lock:
            if self._state not in (STATE_ERROR,):
                self._state = STATE_STOPPED

    def _await_device(self, hint: str,
                      timeout_s: float) -> tuple[int, dict] | None:
        """Poll until the endpoint exists or the timeout expires (0 = forever)."""
        deadline = None if timeout_s <= 0 else time.monotonic() + timeout_s
        announced = False
        while not self._stop_event.is_set():
            try:
                picked = pick_device(hint)
            except Exception:
                picked = None
            if picked is not None:
                if announced:
                    self._log(f"device appeared: {picked[1].get('name')}")
                return picked
            if deadline is not None and time.monotonic() >= deadline:
                return None
            if not announced:
                self._set_state(STATE_WAITING, f"waiting for '{hint}'")
                announced = True
            if self._stop_event.wait(DEVICE_POLL_S):
                return None
        return None

    def _run_stream(self, device_index: int, info: dict,
                    settings: dict[str, Any]) -> str:
        """Stream until stopped or broken. Returns 'stopped' or a reason."""
        try:
            default_sr = int(info.get("default_samplerate") or 0) or 44100
        except Exception:
            default_sr = 44100
        sample_rate = int(settings.get("sample_rate") or 0) or default_sr
        channels = min(int(info.get("max_output_channels", 2)), 2) or 1
        amplitude = dbfs_to_amplitude(float(settings.get("level_dbfs", -60.0)))
        gen = NoiseGenerator(str(settings.get("signal_type", "pink")),
                             sample_rate, float(settings.get("freq_hz", 19000.0)))

        fade_in = np.linspace(0.0, 1.0, int(sample_rate * FADE_S),
                              dtype=np.float32)
        fade_out = fade_in[::-1].copy()
        st: dict[str, Any] = {"pos": 0, "fading": False, "frames": 0,
                              "rms": 0.0, "errors": 0}

        def callback(outdata, frames, time_info, status):  # noqa: ARG001
            if status:
                st["errors"] += 1
            buf = gen.generate(frames, amplitude)
            if not st["fading"]:
                n = min(len(fade_in) - st["pos"], frames)
                if n > 0:
                    buf[:n] *= fade_in[st["pos"]:st["pos"] + n]
                    st["pos"] += n
                if st["pos"] >= len(fade_in):
                    st["fading"] = True
            elif self._stop_event.is_set():
                # Fade out only when actually shutting down, so the stop is
                # click-free but normal operation is never attenuated.
                n = min(len(fade_out), frames)
                if n > 0:
                    buf[:n] *= fade_out[:n]
                if n < frames:
                    buf[n:] = 0.0
                    raise sd.CallbackStop

            if channels == 2:
                outdata[:] = np.repeat(buf[:, np.newaxis], 2, axis=1)
            else:
                outdata[:] = buf.reshape(-1, 1)

            st["rms"] = float(np.sqrt(np.mean(np.square(buf, dtype=np.float64))))
            st["frames"] += frames
            if self._stop_event.is_set():
                raise sd.CallbackStop

        try:
            host = _api_name(device_index)
        except Exception:
            host = "?"
        self._log(f"streaming on #{device_index} '{info.get('name')}' "
                  f"[{host}] {sample_rate}Hz {channels}ch "
                  f"{settings.get('signal_type')} @ "
                  f"{settings.get('level_dbfs')} dBFS")

        try:
            with sd.OutputStream(device=device_index, samplerate=sample_rate,
                                 channels=channels, dtype="float32",
                                 blocksize=BLOCK_FRAMES, callback=callback):
                self._set_state(STATE_RUNNING, "feeding the speaker")
                self._set_stats(device_index=device_index,
                                device_name=info.get("name", ""),
                                host_api=host,
                                sample_rate=sample_rate,
                                channels=channels,
                                signal_type=settings.get("signal_type"),
                                level_dbfs=settings.get("level_dbfs"),
                                audio_rms=0.0,
                                expected_rms=amplitude / 2.0,
                                frames_delivered=0)
                while not self._stop_event.is_set():
                    if self._stop_event.wait(HEARTBEAT_POLL_S):
                        break
                    self._set_stats(frames_delivered=st["frames"],
                                    audio_rms=st["rms"],
                                    stream_errors=st["errors"])
            return "stopped"
        except sd.CallbackStop:
            return "stopped"
        except Exception as exc:
            # Log the traceback, not just the message: this exact class of
            # failure (stream open refused) is almost always environmental and
            # the message alone does not say which call gave up.
            import traceback
            self._log("stream failure traceback:\n" + traceback.format_exc())
            return f"{type(exc).__name__}: {exc}"
