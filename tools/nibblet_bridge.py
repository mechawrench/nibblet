#!/usr/bin/env python3
"""BLE bridge between Claude Code hooks and the Nibblet stick.

Pairing
-------
The stick uses Secure Connections + MITM passkey pairing. On first
connect, macOS pops up a system dialog asking for a 6-digit passkey;
the stick prints that passkey on its USB serial console at boot. The
user reads it from there (or wherever the stick's serial monitor is
attached) and enters it in the dialog. Once the bond is stored in
macOS Keychain + the stick's NVS, every subsequent reconnect is
silent. CoreBluetooth handles the dialog itself — bleak has no API
to inject the passkey, so this script just waits out the connect.
"""
import argparse
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .nibblet_state import list_sessions, resolve_state_dir, truncate, write_decision
    from .nibblet_usage import compute_usage, last_activity_secs, DEFAULT_WINDOW_SECS
    from .nibblet_git import scan as git_scan
except ImportError:
    from nibblet_state import list_sessions, resolve_state_dir, truncate, write_decision
    from nibblet_usage import compute_usage, last_activity_secs, DEFAULT_WINDOW_SECS
    from nibblet_git import scan as git_scan


NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BLE bridge between Claude Code hooks and Nibblet.")
    parser.add_argument("--state-dir", help="Override the state directory.")
    parser.add_argument("--device", default=os.environ.get("NIBBLET_DEVICE", ""), help="Exact BLE device name.")
    parser.add_argument(
        "--device-prefix",
        default=os.environ.get("NIBBLET_DEVICE_PREFIX", "Nibblet"),
        help="Scan for the first device whose name starts with this prefix.",
    )
    parser.add_argument(
        "--snapshot-interval",
        type=float,
        default=float(os.environ.get("NIBBLET_SNAPSHOT_INTERVAL", "1.0")),
        help="Seconds between state snapshots.",
    )
    parser.add_argument(
        "--time-sync-interval",
        type=float,
        default=float(os.environ.get("NIBBLET_TIME_SYNC_INTERVAL", "60.0")),
        help="Seconds between clock sync packets.",
    )
    return parser.parse_args()


# Usage scan touches every transcript JSONL Claude Code has written in
# the rolling window — too expensive to run on every 1s snapshot. Cache
# it for USAGE_CACHE_SECS and refresh in the background.
USAGE_CACHE_SECS = 30.0
_usage_cache: dict[str, Any] = {"value": None, "fetched_at": 0.0}

# Visual progress bar cap on the device. Claude Code does not expose the
# user's actual subscription limit, so this is a manual budget. Override
# with NIBBLET_USAGE_CAP_USD=75 (etc) before launching the bridge.
def usage_cap_cents() -> int:
    raw = os.environ.get("NIBBLET_USAGE_CAP_USD", "50")
    try:
        return max(1, int(round(float(raw) * 100)))
    except ValueError:
        return 5000


def cached_usage(window_secs: int = DEFAULT_WINDOW_SECS) -> dict[str, Any]:
    now = time.time()
    if (
        _usage_cache["value"] is not None
        and now - _usage_cache["fetched_at"] < USAGE_CACHE_SECS
        and _usage_cache["value"].get("window_secs") == window_secs
    ):
        return _usage_cache["value"]
    try:
        value = compute_usage(window_secs)
    except Exception as exc:
        print(f"[nibblet] usage scan failed: {exc}", file=sys.stderr, flush=True)
        value = _usage_cache["value"] or {
            "window_secs": window_secs, "cost_cents": 0, "resets_in_secs": 0,
            "messages": 0, "tokens_total": 0,
        }
    _usage_cache["value"] = value
    _usage_cache["fetched_at"] = now
    return value


# Git scan is one `git status` per configured repo (~50ms each on a warm
# index). Cheap, but the user-facing thresholds are in the order of an
# hour — refreshing more than every 30s is pointless.
GIT_CACHE_SECS = 30.0
_git_cache: dict[str, Any] = {"value": None, "fetched_at": 0.0}
_git_empty: dict[str, Any] = {
    "mood": None, "repos": [], "dirty_secs": 0, "conflicts": 0, "repo": "",
}


def cached_git() -> dict[str, Any]:
    now = time.time()
    if _git_cache["value"] is not None and now - _git_cache["fetched_at"] < GIT_CACHE_SECS:
        return _git_cache["value"]
    try:
        value = git_scan(now)
    except Exception as exc:
        print(f"[nibblet] git scan failed: {exc}", file=sys.stderr, flush=True)
        value = _git_cache["value"] or _git_empty
    _git_cache["value"] = value
    _git_cache["fetched_at"] = now
    return value


def _format_git_msg(git: dict[str, Any]) -> str | None:
    """Compose a ≤23-char status line for the home screen, or None."""
    mood = git.get("mood")
    if mood is None:
        return None
    repo = str(git.get("repo", ""))[:10]
    if mood == "panic":
        n = int(git.get("conflicts", 0))
        plural = "s" if n != 1 else ""
        return truncate(f"git: {n} conflict{plural}", 23)
    if mood == "nervous":
        secs = int(git.get("dirty_secs", 0))
        h, rem = divmod(secs, 3600)
        m = rem // 60
        when = f"{h}h{m:02d}m" if h else f"{m}m"
        prefix = f"{repo}: " if repo else "git: "
        return truncate(f"{prefix}dirty {when}", 23)
    if mood == "clean":
        prefix = f"{repo}: " if repo else "git: "
        return truncate(f"{prefix}all clean", 23)
    return None


def aggregate_snapshot(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    now = time.time()
    active = []
    for session in sessions:
        if not session.get("active", True):
            continue
        if now - float(session.get("updated_at", 0) or 0) > 15 * 60:
            continue
        active.append(session)

    active.sort(key=lambda item: float(item.get("updated_at", 0) or 0), reverse=True)
    latest = active[0] if active else None
    # A session is "running" only if its `running` flag is set AND it has
    # been touched by a hook in the last RUNNING_FRESHNESS_SECS. If a
    # PreToolUse fired but the matching Stop never did (Ctrl+C, crash,
    # parent terminal closed), the session ends up frozen with running=
    # True forever — without this check the firmware would think Claude
    # is still working and the clock face would never appear.
    RUNNING_FRESHNESS_SECS = 90
    def is_actively_running(s: dict[str, Any]) -> bool:
        if not s.get("running"):
            return False
        return (now - float(s.get("updated_at", 0) or 0)) < RUNNING_FRESHNESS_SECS

    waiting = [session for session in active if session.get("waiting") and session.get("prompt")]
    waiting.sort(key=lambda item: float(item.get("updated_at", 0) or 0), reverse=True)
    prompt = waiting[0]["prompt"] if waiting else None
    entries = []
    if latest:
        entries = [truncate(entry, 91) for entry in latest.get("entries", [])][-8:]
    msg = latest.get("msg", "No Claude connected") if latest else "No Claude connected"
    usage = cached_usage()
    idle = last_activity_secs()
    git = cached_git()
    # Surface the git status as the home-screen message only when no live
    # Claude session has anything to say. Claude messages always win — the
    # pet's persona state still reflects git mood (see firmware derive()).
    if not latest and git.get("mood") is not None:
        git_msg = _format_git_msg(git)
        if git_msg:
            msg = git_msg
    return {
        "total": len(active),
        "running": sum(1 for session in active if is_actively_running(session)),
        "waiting": len(waiting),
        "connected": bool(active),
        "msg": truncate(msg, 23),
        "entries": entries,
        "tokens": int(sum(int(session.get("tokens", 0) or 0) for session in active)),
        "tokens_today": 0,
        "prompt": prompt,
        # Real per-message usage from Claude Code's local transcripts.
        # Compact ints so the firmware can render without floats: cost in
        # cents, seconds until the oldest message in the rolling window
        # ages out, the window length, and the visual cap (a manual
        # budget — Anthropic does not expose the actual subscription
        # limit) so the firmware can fill a progress bar.
        "usage": {
            "cents":  int(usage.get("cost_cents", 0) or 0),
            "resets": int(usage.get("resets_in_secs", 0) or 0),
            "window": int(usage.get("window_secs", DEFAULT_WINDOW_SECS) or DEFAULT_WINDOW_SECS),
            "cap":    usage_cap_cents(),
        },
        # Seconds since Claude Code was last active (any transcript write).
        # -1 = never used / no transcripts found, distinct from "0s ago".
        "idle_secs": int(idle) if idle is not None else -1,
        # Git mood — null when NIBBLET_GIT_REPOS is unset, otherwise one of
        # clean/nervous/panic. The firmware blends this into the persona
        # state only when Claude has nothing more urgent to say.
        "git": {
            "mood":       git.get("mood"),
            "dirty_secs": int(git.get("dirty_secs", 0) or 0),
            "conflicts":  int(git.get("conflicts", 0) or 0),
            "repo":       str(git.get("repo", "") or "")[:16],
        },
    }


class Bridge:
    def __init__(
        self,
        state_dir: Path,
        device_name: str,
        device_prefix: str,
        snapshot_interval: float,
        time_sync_interval: float,
        on_status: "Callable[[dict[str, Any]], None] | None" = None,
    ):
        self.state_dir = state_dir
        self.device_name = device_name
        self.device_prefix = device_prefix
        self.snapshot_interval = snapshot_interval
        self.time_sync_interval = time_sync_interval
        self.on_status = on_status
        self.client = None
        self.stop_event = asyncio.Event()
        self.rx_buf = bytearray()

    def _emit_status(self, state: str, device: str | None, error: str | None) -> None:
        if self.on_status is None:
            return
        try:
            self.on_status({"state": state, "device": device, "error": error})
        except Exception:
            pass

    async def run(self) -> None:
        from bleak import BleakClient, BleakScanner

        self._emit_status("scanning", None, None)
        while not self.stop_event.is_set():
            device = await self.find_device(BleakScanner)
            if device is None:
                self._emit_status("scanning", None, None)
                await asyncio.sleep(2.0)
                continue

            # On first connect against an unbonded stick, BleakClient's
            # context manager blocks here while CoreBluetooth shows the
            # macOS pairing dialog. Surface a distinct state so the menu
            # bar can tell the user to look for the passkey prompt
            # instead of just spinning on "scanning".
            name = device.name or device.address
            self._emit_status("connecting", name, None)
            try:
                async with BleakClient(device) as client:
                    self.client = client
                    await client.start_notify(NUS_TX_UUID, self.on_notify)
                    print(f"[nibblet] connected to {name}", flush=True)
                    self._emit_status("connected", name, None)
                    await self.sync_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                msg = str(exc)
                # macOS surfaces a failed/cancelled pairing as a generic
                # connect error. Detect the common shapes and rewrite the
                # message so the user knows what to do next.
                lowered = msg.lower()
                if any(token in lowered for token in (
                    "encryption", "authentication", "insufficient", "pairing",
                    "not permitted", "0xe00002c5",  # CoreBluetooth pair denied
                )):
                    msg = (
                        f"{msg} — pairing failed. Check the Nibblet stick's "
                        "serial console for the 6-digit passkey, then retry; "
                        "if macOS still won't pair, Forget the device in "
                        "System Settings → Bluetooth and let it reprompt."
                    )
                print(f"[nibblet] reconnecting after error: {msg}", file=sys.stderr, flush=True)
                self._emit_status("error", None, msg)
                await asyncio.sleep(2.0)
            finally:
                self.client = None
                if not self.stop_event.is_set():
                    self._emit_status("scanning", None, None)

    async def find_device(self, scanner_cls) -> Any | None:
        devices = await scanner_cls.discover(timeout=4.0)
        exact = self.device_name.strip()
        if exact:
            for device in devices:
                if (device.name or "") == exact:
                    return device
            print(f"[nibblet] waiting for BLE device named '{exact}'", flush=True)
            return None

        prefix = self.device_prefix.strip()
        for device in devices:
            name = device.name or ""
            if prefix and name.startswith(prefix):
                return device

        print(f"[nibblet] waiting for BLE device with prefix '{prefix}'", flush=True)
        return None

    async def sync_loop(self) -> None:
        # Two cadences: a fast inner poll for prompt-state changes (so
        # the stick stops alerting within ~200ms of a desktop
        # approve/deny instead of waiting up to a full snapshot_interval)
        # and the regular heartbeat snapshot at snapshot_interval. We
        # only push a BLE write when something meaningful changed or
        # when the heartbeat is due, so the BLE channel isn't spammed.
        last_time_sync = 0.0
        last_send = 0.0
        last_prompt_id: str | None = None
        PROMPT_POLL_SECS = 0.2
        while self.client and self.client.is_connected and not self.stop_event.is_set():
            snapshot = aggregate_snapshot(list_sessions(self.state_dir))
            prompt = snapshot.get("prompt")
            current_prompt_id = prompt.get("id") if isinstance(prompt, dict) else None

            now = time.time()
            prompt_changed = current_prompt_id != last_prompt_id
            heartbeat_due = (now - last_send) >= self.snapshot_interval

            if prompt_changed or heartbeat_due:
                await self.send_json(snapshot)
                last_send = now
                last_prompt_id = current_prompt_id

                if now - last_time_sync >= self.time_sync_interval:
                    await self.send_time_sync(now)
                    last_time_sync = now

            await asyncio.sleep(PROMPT_POLL_SECS)

    async def send_time_sync(self, now: float) -> None:
        local = time.localtime(now)
        gm = time.gmtime(now)
        offset = int(time.mktime(local) - time.mktime(gm))
        await self.send_json({"time": [int(now), offset]})

    async def send_json(self, payload: dict[str, Any]) -> None:
        if not self.client or not self.client.is_connected:
            return
        data = (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
        for offset in range(0, len(data), 180):
            chunk = data[offset : offset + 180]
            await self.client.write_gatt_char(NUS_RX_UUID, chunk, response=False)
            await asyncio.sleep(0.01)

    def on_notify(self, _handle: Any, data: bytearray) -> None:
        self.rx_buf.extend(data)
        while True:
            try:
                idx = self.rx_buf.index(10)
            except ValueError:
                return
            line = bytes(self.rx_buf[:idx]).strip()
            del self.rx_buf[: idx + 1]
            if not line:
                continue
            self.handle_line(line.decode("utf-8", errors="replace"))

    def handle_line(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            print(f"[nibblet] {line}", flush=True)
            return

        if payload.get("cmd") == "permission" and payload.get("id"):
            write_decision(
                self.state_dir,
                str(payload["id"]),
                {
                    "decision": payload.get("decision"),
                    "received_at": time.time(),
                },
            )
            print(f"[nibblet] decision {payload.get('decision')} for {payload.get('id')}", flush=True)
            return

        print(f"[nibblet] device {json.dumps(payload, ensure_ascii=True)}", flush=True)

    def stop(self) -> None:
        self.stop_event.set()


async def async_main() -> int:
    args = parse_args()
    state_dir = resolve_state_dir(args.state_dir)
    bridge = Bridge(state_dir, args.device, args.device_prefix, args.snapshot_interval, args.time_sync_interval)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bridge.stop)
        except NotImplementedError:
            pass

    await bridge.run()
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
