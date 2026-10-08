"""Fermata - desktop app entry point.

Wires four things together, each with its own thread, and the threading is the
tricky part:

  main thread      pywebview's event loop (must own the GUI)
  tray thread      pystray's message loop (Windows allows it off the main thread)
  engine thread    PortAudio streaming, one per run
  monitor thread   polls engine status to recolour the tray icon

Keeping the GUI on the main thread and the tray off it is deliberate: pywebview
insists on the main thread, and pystray is the one that tolerates being moved.
Reversing that deadlocks on startup.

Usage:
    python main.py              open the settings window
    python main.py --minimized  start hidden, tray only (used by launch-at-login)
    python main.py --selftest   headless check of the whole stack, then exit
"""
from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes

# Allow `python app/main.py` as well as `python -m app.main`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pystray  # noqa: E402
import webview  # noqa: E402

from app.core import autostart, i18n, icon, settings as settings_mod  # noqa: E402
from app.core.engine import KeepaliveEngine, list_output_devices  # noqa: E402
from app.core.paths import app_data_dir, log_path, ui_file  # noqa: E402

APP_TITLE = "Fermata"
SINGLETON_MUTEX = "Global\\FermataApp"
ERROR_ALREADY_EXISTS = 183

# Settings that change what comes out of the speaker. Everything else (language,
# tray behaviour, autostart) can be applied without interrupting the stream.
AUDIO_KEYS = ("device_hint", "signal_type", "level_dbfs", "freq_hz",
              "sample_rate")

log = logging.getLogger("fermata")


def setup_logging(verbose: bool = False) -> None:
    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.FileHandler(log_path(), encoding="utf-8"))
    except OSError:
        pass
    # Under pythonw.exe there is no console; logging to stdout would raise.
    if sys.stdout is not None and getattr(sys.stdout, "write", None):
        try:
            sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
            handlers.append(logging.StreamHandler(sys.stdout))
        except Exception:
            pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=handlers or [logging.NullHandler()],
        force=True,
    )


def acquire_app_lock() -> tuple[bool, str]:
    """Single-instance guard for the GUI process."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                 wintypes.LPCWSTR]
    k32.CreateMutexW.restype = wintypes.HANDLE
    handle = k32.CreateMutexW(None, True, SINGLETON_MUTEX)
    if not handle:
        return True, "mutex unavailable"
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return False, "already running"
    globals()["_APP_LOCK"] = handle  # keep alive for process lifetime
    return True, "ok"


# --------------------------------------------------------------------------
# JS <-> Python bridge
# --------------------------------------------------------------------------

class Api:
    """Methods here are callable from JavaScript as pywebview.api.<name>()."""

    def __init__(self, app: "Application"):
        self._app = app

    def get_state(self) -> dict:
        return {
            "settings": self._app.settings,
            "status": self._app.engine.status(),
            "devices": list_output_devices(unique=True),
            "autostart_enabled": autostart.is_enabled(),
            "autostart_command": autostart.current_value(),
            "config_path": settings_mod.config_path(),
            "version": self._app.version,
            # The resolved language, not the raw preference: the window needs to
            # know what "auto" actually landed on.
            "language": self._app.language,
        }

    def status(self) -> dict:
        return self._app.engine.status()

    def list_devices(self) -> list:
        # Collapsed: one row per physical device, tagged with the host API the
        # engine will actually pick. See list_output_devices(unique=True).
        return list_output_devices(unique=True)

    def save_settings(self, incoming: dict) -> dict:
        return self._app.apply_settings(incoming or {})

    def set_autostart(self, enabled: bool) -> dict:
        return self._app.set_autostart(bool(enabled))

    def start_engine(self) -> dict:
        return self._app.start_engine()

    def stop_engine(self) -> dict:
        return self._app.stop_engine()

    def open_data_folder(self) -> bool:
        try:
            os.startfile(app_data_dir())  # noqa: S606
            return True
        except Exception as exc:
            log.warning("open folder failed: %s", exc)
            return False


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

class Application:
    version = "1.0.2"

    def __init__(self, start_minimized: bool = False):
        self.settings = settings_mod.load()
        self.engine = KeepaliveEngine(log=lambda m: log.info("engine: %s", m))
        self.start_minimized = start_minimized
        self.window: webview.Window | None = None
        self.tray: pystray.Icon | None = None
        self._quitting = threading.Event()
        self._last_icon_state = ""

        # Resolve "auto" once, up front: the tray menu needs a language before
        # the window has even loaded.
        self.language = i18n.set_language(self.settings.get("language", "auto"))
        log.info("language resolved to %s", self.language)

        self._sync_autostart()

    # -- autostart repair ------------------------------------------------

    def _sync_autostart(self) -> None:
        """Bring the Run key in line with the stored preference at every start.

        Three cases, and the direction of each matters:

          wanted, missing or wrong   create, restore or repoint it. Without this,
                                     anything that clears the entry behind our
                                     back (cleanup utilities, a profile reset)
                                     silently turns off launch-at-login while the
                                     checkbox still reads "on"; and an entry left
                                     over from a source run keeps pointing at a
                                     tree that may have moved.
          present and unwanted       remove it -- but only if the entry is ours.
                                     Running from source must never delete an
                                     installed build's entry.
        """
        wanted = bool(self.settings.get("launch_at_login"))
        minimized = bool(self.settings.get("start_minimized", True))
        present = autostart.is_enabled()

        if wanted and (not present or autostart.should_refresh(minimized)):
            ok, val = autostart.enable(minimized)
            log.info("autostart %s -> %s (%s)",
                     "restored" if not present else "repointed", val, ok)
            return

        # `is_ours` rather than a whole-command comparison, so an entry written
        # without --minimized is still recognised as ours and can be removed.
        if not wanted and present and autostart.is_ours():
            ok, _ = autostart.disable()
            log.info("autostart disabled (the entry was ours) -> %s", ok)

    # -- engine ---------------------------------------------------------

    def start_engine(self) -> dict:
        ok, msg = self.engine.start(self.settings)
        if not ok:
            log.warning("start_engine: %s", msg)
        return {"ok": ok, "message": msg}

    def stop_engine(self) -> dict:
        self.engine.stop()
        return {"ok": True, "message": "stopped"}

    # -- settings -------------------------------------------------------

    def apply_settings(self, incoming: dict) -> dict:
        before = dict(self.settings)
        merged = dict(self.settings)
        merged.update(incoming)

        was_running = self.engine.is_running()
        ok = settings_mod.save(merged)
        if not ok:
            return {"ok": False, "message": "could not write settings file"}

        self.settings = settings_mod.load()

        # Reuse the same guarded sync used at startup. Doing it inline here was
        # how a source run could delete an installed build's Run entry.
        self._sync_autostart()

        # Follow a language change in the tray immediately.
        new_language = i18n.set_language(self.settings.get("language", "auto"))
        if new_language != self.language:
            self.language = new_language
            log.info("language changed to %s", new_language)
        self._sync_tray(force=True)

        # Only an audio setting justifies restarting the stream. Restarting on
        # every save meant that picking a language cut the signal briefly -- and
        # with it the very thing the app exists to provide.
        changed = [k for k in AUDIO_KEYS if self.settings.get(k) != before.get(k)]
        if was_running and changed:
            log.info("audio settings changed (%s); restarting the stream",
                     ", ".join(sorted(changed)))
            self.engine.stop()
            self.start_engine()
        log.info("settings saved (engine was running: %s, audio changed: %s)",
                 was_running, bool(changed))
        return {"ok": True, "message": "saved"}

    def set_autostart(self, enabled: bool) -> dict:
        if enabled:
            ok, msg = autostart.enable(bool(self.settings.get("start_minimized", True)))
            if ok:
                self.settings["launch_at_login"] = True
                settings_mod.save(self.settings)
        else:
            ok, msg = autostart.disable()
            if ok:
                self.settings["launch_at_login"] = False
                settings_mod.save(self.settings)
        log.info("autostart set to %s -> %s", enabled, msg)
        return {"ok": ok, "message": msg,
                "command": autostart.current_value()}

    # -- window ---------------------------------------------------------

    def hide_window(self) -> bool:
        if self.window:
            try:
                self.window.hide()
                return True
            except Exception as exc:
                log.warning("hide failed: %s", exc)
        return False

    def show_window(self) -> bool:
        if self.window:
            try:
                self.window.show()
                self.window.restore()
                return True
            except Exception as exc:
                log.warning("show failed: %s", exc)
        return False

    def request_quit(self) -> None:
        if self._quitting.is_set():
            return
        self._quitting.set()
        log.info("quitting")
        self.engine.stop()
        if self.tray:
            try:
                self.tray.visible = False
                self.tray.stop()
            except Exception:
                pass
        for win in list(webview.windows):
            try:
                win.destroy()
            except Exception:
                pass

    # -- tray -----------------------------------------------------------

    def _tray_menu(self) -> pystray.Menu:
        # Every label is a callable so the menu follows a language change
        # without rebuilding the icon.
        return pystray.Menu(
            pystray.MenuItem(lambda _i: i18n.t("tray.show"),
                             lambda i, it: self.show_window(), default=True),
            pystray.MenuItem(
                lambda _i: i18n.t("tray.stop") if self.engine.is_running()
                else i18n.t("tray.start"),
                self._tray_toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _i: i18n.t("tray.autostart"),
                             self._tray_autostart,
                             checked=lambda item: autostart.is_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _i: i18n.t("tray.quit"),
                             lambda i, it: self.request_quit()),
        )

    def _tray_toggle(self, _icon, _item) -> None:
        if self.engine.is_running():
            self.stop_engine()
        else:
            self.start_engine()
        self._sync_tray(force=True)

    def _tray_autostart(self, _icon, _item) -> None:
        self.set_autostart(not autostart.is_enabled())
        self._sync_tray(force=True)

    def _build_tray(self) -> pystray.Icon:
        return pystray.Icon("fermata",
                            icon.make_image(64, icon.TRAY_GREY),
                            APP_TITLE, self._tray_menu())

    def _tray_thread(self) -> None:
        assert self.tray is not None
        try:
            self.tray.run()
        except Exception as exc:
            log.error("tray thread died: %s", exc)

    def _sync_tray(self, force: bool = False) -> None:
        """Recolour the tray icon and refresh its tooltip from engine state."""
        if not self.tray:
            return
        st = self.engine.status()
        state = st.get("state", "stopped")
        if not force and state == self._last_icon_state:
            return
        self._last_icon_state = state

        colour = icon.STATE_COLORS.get(state, icon.TRAY_GREY)
        try:
            self.tray.icon = icon.make_image(64, colour)
        except Exception as exc:
            log.debug("tray icon update failed: %s", exc)

        label = i18n.t(f"state.{state}") if state in (
            "running", "waiting", "reconnecting", "error", "stopped") else state
        dev = st.get("device_name") or ""
        self.tray.title = f"{APP_TITLE} - {label}" + (f"\n{dev}" if dev else "")

    def _monitor_thread(self) -> None:
        while not self._quitting.is_set():
            try:
                self._sync_tray()
            except Exception as exc:
                log.debug("monitor: %s", exc)
            time.sleep(2.0)

    # -- run ------------------------------------------------------------

    def run(self) -> int:
        self.tray = self._build_tray()
        threading.Thread(target=self._tray_thread, daemon=True,
                         name="tray").start()
        threading.Thread(target=self._monitor_thread, daemon=True,
                         name="monitor").start()

        api = Api(self)
        self.window = webview.create_window(
            APP_TITLE,
            url=ui_file("index.html"),
            js_api=api,
            width=880, height=620,
            min_size=(720, 520),
            hidden=self.start_minimized,
        )

        def on_closing():
            # Returning False cancels the close so the app keeps running in the
            # tray. Without this the window X would silently kill the keepalive.
            if self._quitting.is_set():
                return True
            if self.settings.get("close_to_tray", True):
                self.hide_window()
                return False
            self.request_quit()
            return True

        try:
            self.window.events.closing += on_closing
        except Exception as exc:
            log.warning("could not attach closing handler: %s", exc)

        if self.settings.get("autostart_engine", True):
            self.start_engine()

        log.info("window created (hidden=%s)", self.start_minimized)
        try:
            webview.start()
        except Exception as exc:
            log.error("webview failed: %s", exc)
            return 2
        finally:
            self.request_quit()
        return 0


# --------------------------------------------------------------------------
# selftest (used to verify the packaged EXE without a human looking at it)
# --------------------------------------------------------------------------

def selftest() -> int:
    """Headless end-to-end check. Writes a report and returns an exit code."""
    lines: list[str] = []
    ok = True

    def step(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        lines.append(f"[{'PASS' if passed else 'FAIL'}] {name}"
                     + (f"  ({detail})" if detail else ""))

    step("ui asset present", os.path.exists(ui_file("index.html")),
         ui_file("index.html"))
    step("settings load", isinstance(settings_mod.load(), dict))
    devs = list_output_devices()
    step("devices enumerated", len(devs) > 0, f"{len(devs)} outputs")
    step("autostart command buildable", bool(autostart.launch_command()),
         autostart.launch_command())
    step("icon renders", icon.make_image(32).size == (32, 32))

    cfg = settings_mod.load()
    # The stored device filter is used as-is; empty means the system default.
    cfg["wait_device_s"] = 30
    cfg["reconnect"] = False
    eng = KeepaliveEngine(log=lambda m: lines.append(f"    engine: {m}"))
    started, msg = eng.start(cfg)
    step("engine starts", started, msg)

    deadline = time.time() + 20
    st = {}
    while time.time() < deadline:
        st = eng.status()
        if st.get("state") == "running" and st.get("frames_delivered", 0) > 0:
            break
        time.sleep(0.5)
    step("engine reaches running", st.get("state") == "running",
         str(st.get("state")))
    step("audio flowing", st.get("frames_delivered", 0) > 0,
         f"frames={st.get('frames_delivered')}")
    eng.stop()
    step("engine stops", not eng.is_running())

    report = os.path.join(app_data_dir(), "selftest.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + f"\n\nRESULT: {'ALL PASSED' if ok else 'FAILURES'}\n")
    for line in lines:
        log.info(line)
    print("\n".join(lines))
    print("report:", report)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--minimized", action="store_true",
                        help="start hidden in the tray")
    parser.add_argument("--selftest", action="store_true",
                        help="headless stack check, then exit")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.selftest:
        return selftest()

    ok, why = acquire_app_lock()
    if not ok:
        log.warning("another instance is already running (%s)", why)
        return 4

    app = Application(start_minimized=args.minimized)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
