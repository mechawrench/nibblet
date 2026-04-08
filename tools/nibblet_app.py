#!/usr/bin/env python3
"""macOS menu bar wrapper around the Nibblet BLE bridge.

Run from a checkout:

    python3 tools/nibblet_app.py

Or build a distributable bundle (Nibblet.app) with py2app:

    python3 tools/setup_app.py py2app

The app sits in the menu bar (no Dock icon), runs the same BLE bridge as
``nibblet_bridge.py`` on a background thread, and surfaces connection
state, the connected stick, and the active session count.
"""
from __future__ import annotations

import asyncio
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import rumps  # type: ignore[import-not-found]

from nibblet_bridge import Bridge
from nibblet_state import list_sessions, resolve_state_dir


DEFAULT_PREFIX = os.environ.get("NIBBLET_DEVICE_PREFIX", "Nibblet")
DEFAULT_DEVICE = os.environ.get("NIBBLET_DEVICE", "")
SNAPSHOT_INTERVAL = float(os.environ.get("NIBBLET_SNAPSHOT_INTERVAL", "1.0"))
TIME_SYNC_INTERVAL = float(os.environ.get("NIBBLET_TIME_SYNC_INTERVAL", "60.0"))
POLL_INTERVAL = 1.0


class BridgeWorker:
    """Runs ``Bridge`` on a private asyncio loop in a daemon thread."""

    def __init__(self, status_queue: "queue.Queue[dict[str, Any]]", state_dir: Path) -> None:
        self.status_queue = status_queue
        self.state_dir = state_dir
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bridge: Bridge | None = None
        self.thread = threading.Thread(target=self._run, name="nibblet-bridge", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        loop = self.loop
        bridge = self.bridge
        if loop is None or bridge is None:
            return
        loop.call_soon_threadsafe(bridge.stop)

    def _publish(self, payload: dict[str, Any]) -> None:
        try:
            self.status_queue.put_nowait(payload)
        except queue.Full:
            pass

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:  # surfaces in the menu bar
            self._publish({"state": "error", "device": None, "error": str(exc)})

    async def _main(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.bridge = Bridge(
            state_dir=self.state_dir,
            device_name=DEFAULT_DEVICE,
            device_prefix=DEFAULT_PREFIX,
            snapshot_interval=SNAPSHOT_INTERVAL,
            time_sync_interval=TIME_SYNC_INTERVAL,
            on_status=self._publish,
        )
        await self.bridge.run()


class NibbletApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Nibblet", quit_button=None)
        self.state_dir = resolve_state_dir()
        self.status: dict[str, Any] = {"state": "starting", "device": None, "error": None}
        self.status_queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=64)

        self.status_item = rumps.MenuItem("Starting...")
        self.device_item = rumps.MenuItem("Device: -")
        self.sessions_item = rumps.MenuItem("Sessions: 0")
        # Static help row — visible all the time so the user knows what
        # to do the first time macOS pops a pairing dialog. The stick
        # generates a fresh 6-digit passkey at boot and prints it on its
        # USB serial console; we cannot read it from here (it's not
        # transmitted over BLE), so the user has to look at the device.
        self.pairing_help_item = rumps.MenuItem(
            "Pairing: enter passkey from stick's serial console"
        )
        self.state_dir_item = rumps.MenuItem(
            f"Reveal state folder", callback=self._reveal_state_dir
        )
        quit_item = rumps.MenuItem("Quit Nibblet", callback=self._on_quit)

        self.menu = [
            self.status_item,
            self.device_item,
            self.sessions_item,
            None,
            self.pairing_help_item,
            self.state_dir_item,
            None,
            quit_item,
        ]

        self.worker = BridgeWorker(self.status_queue, self.state_dir)
        self.worker.start()

        self.poll_timer = rumps.Timer(self._poll, POLL_INTERVAL)
        self.poll_timer.start()

    def _poll(self, _sender: Any) -> None:
        latest: dict[str, Any] | None = None
        while True:
            try:
                latest = self.status_queue.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self.status.update(latest)
            self._render_status()
        self._render_sessions()

    def _render_status(self) -> None:
        state = self.status.get("state", "starting")
        device = self.status.get("device")
        error = self.status.get("error")

        if state == "connected":
            self.status_item.title = "Connected"
            self.device_item.title = f"Device: {device or '-'}"
        elif state == "connecting":
            # First connect against an unbonded stick blocks here while
            # macOS shows the pairing dialog. Nudge the user toward the
            # passkey prompt so they don't think the app is hung.
            self.status_item.title = "Connecting / pairing..."
            self.device_item.title = f"Device: {device or '-'}"
        elif state == "scanning":
            self.status_item.title = "Scanning for stick..."
            self.device_item.title = "Device: -"
        elif state == "error":
            short = (error or "unknown").splitlines()[0][:60]
            self.status_item.title = f"Error: {short}"
            self.device_item.title = "Device: -"
        else:
            self.status_item.title = "Starting..."
            self.device_item.title = "Device: -"

    def _render_sessions(self) -> None:
        try:
            sessions = list_sessions(self.state_dir)
        except Exception:
            sessions = []
        active = [s for s in sessions if s.get("active", True)]
        self.sessions_item.title = f"Sessions: {len(active)}"

    def _reveal_state_dir(self, _sender: Any) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(self.state_dir)])

    def _on_quit(self, _sender: Any) -> None:
        self.worker.stop()
        deadline = time.time() + 1.5
        while self.worker.thread.is_alive() and time.time() < deadline:
            time.sleep(0.05)
        rumps.quit_application()


def main() -> int:
    NibbletApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
