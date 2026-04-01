# nibblet

A desk pet that watches your Claude desktop sessions over Bluetooth LE.
The pet sleeps when nothing's happening, wakes when sessions start, gets
visibly impatient when an approval prompt is waiting, and lets you approve
or deny right from the device with a button press.

Ships with **18 hand-authored ASCII species** (capybara, octopus, dragon,
ghost, axolotl, …) — cycle them from the menu, your pick persists across
reboots. Stats persist too: token-count drives leveling, fast approvals
trigger affection.

## Controls

| | Normal | Pet | Info | Approval |
|---|---|---|---|---|
| **A** (front) | next screen | next screen | next screen | **approve** |
| **B** (right) | scroll transcript | next page | next page | **deny** |
| **Hold A** | menu | menu | menu | menu |
| **Power** (left, short) | toggle screen off | | | |
| **Power** (left, ~6s) | hard power off | | | |
| **Shake** | dizzy | | | — |
| **Face-down** | nap (energy refills) | | | |

The screen auto-powers-off after 30s of no interaction (kept on while an
approval prompt is up). Any button press wakes it.

## Flashing

You need an M5StickC Plus, its USB-C cable, and PlatformIO (`brew install
platformio` or the VS Code extension).

```bash
pio run -t upload
```

First build downloads the ESP32 toolchain (~1 min). After that it's ~15s.
A fresh device boots straight into ASCII buddy mode — no filesystem upload
required.

If you're starting from a previously-flashed device, wipe it first:

```bash
pio run -t erase && pio run -t upload
```

Once running, you can also wipe everything from the device itself:
**hold A → settings → reset → factory reset → tap twice**.

## Pairing

The desktop integration lives in the Claude desktop app under
**Prototypes → Nibblet** (nest builds only). Once the app is running and
the stick is on, the bridge auto-discovers and connects over BLE — no
manual pairing button. macOS will prompt for Bluetooth permission on first
connect; grant it.

If discovery isn't finding the stick:
- Make sure it's awake (any button press)
- Restart the desktop app — the bridge starts ~15s after launch
- Check the stick's settings menu → bluetooth is on

## BLE protocol

The stick exposes a Nordic UART Service. The bridge sends newline-delimited
JSON snapshots ~1Hz; the stick replies with command acks and approval
decisions.

**Snapshot** (bridge → stick):

```json
{"total":3,"running":2,"waiting":1,"connected":true,"msg":"approve: Bash","tokens":48000}
```

| field | sets |
|---|---|
| `connected` | sleep ↔ awake |
| `total`, `running`, `waiting` | idle ↔ busy ↔ attention |
| `msg` | bottom line on the home screen |
| `lines` | transcript scroller (array of strings) |
| `tokens` | feeding counter — 50K tokens per level |
| `prompt` | approval overlay — `{id, tool, hint}` |

**Approval reply** (stick → bridge):

```json
{"cmd":"permission","id":"<promptId>","decision":"once"}
```

**Host commands** (bridge → stick, acked):

| | |
|---|---|
| `{"cmd":"status"}` | dump battery, uptime, heap, stats |
| `{"cmd":"name","name":"..."}` | rename the pet |
| `{"cmd":"owner","name":"..."}` | set owner (boot splash) |
| `{"cmd":"char_begin",...}` | start a GIF character upload |

## ASCII species

Eighteen species, each with seven hand-authored animations (sleep, idle,
busy, attention, celebrate, dizzy, heart). Menu → "next pet" cycles them
with a counter. Choice persists to NVS.

The render path is `src/buddy.cpp` + one file per species in `src/buddies/`.
Each species file defines a `Species` struct with seven animation function
pointers. Adding a new one is ~100 lines.

## GIF characters (optional)

If you want a custom GIF character instead of an ASCII buddy: a character is
a folder with `manifest.json` and seven 135px-wide animated GIFs (one per
state). Install via the desktop Pet Manager — it streams the files over BLE,
the stick switches to GIF mode live. **Settings → delete char** reverts to
ASCII mode.

```json
{
  "name": "bluey",
  "colors": { "body": "#4A90D9", "bg": "#000000", "text": "#FFFFFF",
              "textDim": "#808080", "ink": "#000000" },
  "states": {
    "sleep":     "sleep.gif",
    "idle":      "idle.gif",
    "busy":      "busy.gif",
    "attention": "attention.gif",
    "celebrate": "celebrate.gif",
    "dizzy":     "dizzy.gif",
    "heart":     "heart.gif"
  }
}
```

State values can be a single filename or an array. Arrays rotate — each
loop-end advances to the next GIF after a 3s rest, useful for an idle
activity carousel. `gifsicle --lossy=80 -O3 --colors 64` typically cuts
size 40–60%.

## The seven states

| State | Trigger | Feel |
|---|---|---|
| `sleep` | bridge not connected | eyes closed, slow breathing |
| `idle` | connected, nothing urgent | blinking, looking around |
| `busy` | 3+ sessions running | sweating, working |
| `attention` | approval pending | alert, **LED blinks** |
| `celebrate` | level up (every 50K tokens) | confetti, bouncing |
| `dizzy` | you shook the stick | spiral eyes, wobbling |
| `heart` | approved in under 5s | floating hearts |

## Project layout

```
src/
  main.cpp       — loop, state machine, UI screens
  buddy.cpp      — ASCII species dispatch + render helpers
  buddies/       — one file per species, seven anim functions each
  ble_bridge.cpp — Nordic UART service, line-buffered TX/RX
  character.cpp  — GIF decode + text-mode (legacy path)
  data.h         — wire protocol, JSON parse
  xfer.h         — file transfer receiver (GIF install)
  stats.h        — NVS-backed stats, settings, owner, species choice
characters/      — example GIF characters
tools/           — generators and converters
```
