# Claude Code over BLE

The firmware already exposes a Nordic UART Service BLE endpoint. The missing
piece is a host bridge. You do not need the nest-only Claude Desktop
prototype if you run a local bridge process and feed it from Claude Code
hooks.

## What this adds

- `tools/nibblet_bridge.py`
  - keeps a BLE connection open to the stick
  - sends the same newline-delimited JSON snapshots the firmware already
    expects
  - writes approval decisions from the stick back to disk
- `tools/nibblet_hook.py`
  - consumes Claude Code hook JSON on stdin
  - updates project-local state in `.nibblet/`
  - waits for `PermissionRequest` decisions from the stick, then returns
    `allow` or `deny` to Claude Code

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r tools/requirements-nibblet.txt
```

## Run the BLE bridge

```bash
./tools/run_nibblet_bridge.sh
```

If you have more than one stick nearby, point at one explicitly:

```bash
NIBBLET_DEVICE="Nibblet-ABCD" ./tools/run_nibblet_bridge.sh
```

## Run as a macOS desktop app

The same bridge ships as a menu bar app (`tools/nibblet_app.py`) so you do
not have to keep a terminal open. It runs the BLE loop on a background
thread and shows connection state, the connected stick, and the active
session count from the menu bar.

To run it directly from the checkout:

```bash
source .venv/bin/activate
python3 tools/nibblet_app.py
```

The same `NIBBLET_DEVICE` / `NIBBLET_DEVICE_PREFIX` / `NIBBLET_STATE_DIR`
environment variables work here.

To build a standalone `Nibblet.app` you can drag into `/Applications`:

```bash
source .venv/bin/activate
pip install py2app
python3 tools/setup_app.py py2app
open dist/Nibblet.app
```

The bundle declares `LSUIElement` (no Dock icon) and the Bluetooth usage
strings macOS requires, so the first connect prompts for Bluetooth
permission against "Nibblet" rather than your terminal.

The bridge scans for a BLE peripheral whose name starts with `Nibblet` and
connects to the firmware's Nordic UART Service:

- service: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
- write: `6e400002-b5a3-f393-e0a9-e50e24dcca9e`
- notify: `6e400003-b5a3-f393-e0a9-e50e24dcca9e`

## Claude Code hook config

A ready-to-use file is already included at `.claude/settings.local.json`.
It uses `./tools/run_nibblet_hook.sh` so Claude Code will prefer the repo's
`.venv` automatically.

If you want to regenerate it manually, use:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./tools/run_nibblet_hook.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./tools/run_nibblet_hook.sh"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./tools/run_nibblet_hook.sh"
          }
        ]
      }
    ],
    "PermissionRequest": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "./tools/run_nibblet_hook.sh --permission-timeout-ms 600000"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./tools/run_nibblet_hook.sh"
          }
        ]
      }
    ],
    "PostToolUseFailure": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./tools/run_nibblet_hook.sh"
          }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./tools/run_nibblet_hook.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./tools/run_nibblet_hook.sh"
          }
        ]
      }
    ],
    "StopFailure": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./tools/run_nibblet_hook.sh"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./tools/run_nibblet_hook.sh"
          }
        ]
      }
    ]
  }
}
```

## State directory

The hook and the bridge swap session info and approval prompts through a
shared state directory. By default that lives at:

- macOS: `~/Library/Application Support/Nibblet`
- Linux: `~/.nibblet`

Both processes need to agree on this location, otherwise the hook writes
session files where the bridge cannot see them and the device shows
"No Claude connected" even while the bridge is happily connected over BLE.
Override with `NIBBLET_STATE_DIR=/some/path` if you want a different
location — just make sure both the hook command and the bridge/app see the
same value.

## How approval works

1. Claude Code reaches a `PermissionRequest`.
2. `tools/nibblet_hook.py` writes a prompt into the shared state dir and
   waits.
3. `tools/nibblet_bridge.py` sees that prompt in the next snapshot and sends
   it to the device over BLE.
4. Pressing A on the stick sends `{"cmd":"permission","decision":"once"}`
   back over BLE. Pressing B sends `{"cmd":"permission","decision":"deny"}`.
5. The bridge writes that decision to `<state>/decisions/<promptId>.json`.
6. The hook returns the matching `allow` or `deny` result to Claude Code.

If the timeout expires first, the hook clears the pending prompt and returns
no decision. Claude falls back to its normal local permission dialog.

## Limits

- This bridge is for `Claude Code`, not the nest-only `Claude Desktop`
  prototype.
- It currently tracks session state and permission prompts. It does not yet
  extract real token counts from the transcript, so `tokens` stays at `0`.
- BLE support here is implemented with `bleak`, so it is a macOS/Linux host
  dependency rather than something built into Claude Code itself.
- The wrapper scripts expect to live inside the repo. If you move the Python
  files elsewhere, update `.claude/settings.local.json` too.
