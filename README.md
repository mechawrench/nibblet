# nibblet

> A desk pet that watches your Claude sessions over Bluetooth LE.

Works with both the Claude desktop app (nest builds) and Claude Code via the [standalone bridge](docs/claude-code.md). The pet sleeps when nothing's happening, wakes when sessions start, gets visibly impatient when an approval prompt is waiting, and lets you approve or deny right from the device with a button press.

Ships with **18 hand-authored ASCII species** (capybara, octopus, dragon, ghost, axolotl, …) — cycle them from the menu, your pick persists across reboots. Stats persist too: cumulative output tokens drive levels, your median response time drives mood, naps refill energy. See [Stats](#stats) for what each meter means.

---

## Table of contents

- [What this fork adds vs upstream](#what-this-fork-adds-vs-upstream)
- [Controls](#controls)
- [Flashing](#flashing)
- [Pairing](#pairing)
- [Building the bridge app](#building-the-bridge-app)
- [Remote approve / deny](#remote-approve--deny)
- [Git-aware moods](#git-aware-moods)
- [BLE security](#ble-security)
- [BLE protocol](#ble-protocol)
- [ASCII species](#ascii-species)
- [GIF characters (optional)](#gif-characters-optional)
- [The seven states](#the-seven-states)
- [Stats](#stats)
- [Settings menu](#settings-menu)
- [Charging clock & idle clock](#charging-clock--idle-clock)
- [Project layout](#project-layout)

---

## What this fork adds vs upstream

This is a fork of [felixrieseberg/nibblet](https://github.com/felixrieseberg/nibblet). The upstream firmware only talks to a nest-only Claude Desktop "Prototypes → Nibblet" integration, exposes a minimal BLE snapshot, and ships no host-side tooling beyond a couple of GIF helpers. This fork rewires it to be useful outside that path and adds a pile of device features. Skim this section if you came from upstream and want the diff at a glance — every entry below is then explained in detail in its own section.

### Works without Claude Desktop nest builds
- **Standalone Claude Code bridge.** `tools/nibblet_bridge.py` keeps a BLE link to the stick and feeds it from Claude Code hooks (`tools/nibblet_hook.py`). End-to-end setup is in [docs/claude-code.md](docs/claude-code.md). Upstream had no host bridge at all — only the closed nest desktop integration.
- **macOS menu-bar app.** `Nibblet.app` (built with `py2app` from `tools/setup_app.py`, wrapped by `tools/nibblet_app.py` using `rumps`) runs the bridge in the background and shows connection state from the menu bar. See [Building the bridge app](#building-the-bridge-app).
- **Remote approve / deny / skip CLI.** `tools/nibblet_approve.py`, `nibblet_deny.py`, and `nibblet_skip.py` drop a decision file the hook picks up within ~200ms. The bridge pushes an out-of-cycle snapshot and the stick's chime stops within ~400ms — the same way it would if you pressed A/B on the device. See [Remote approve / deny](#remote-approve--deny).
- **Stale orphan-hook detection.** When Claude Code's own CLI permission UI (or an auto-allow rule) handles a prompt instead of the stick, the hook now self-clears within 200ms by watching for a slot overwrite or for downstream session activity, so the stick doesn't alarm forever. Detection logic lives in `tools/nibblet_hook.py:296-352`.

### New device features
- **Charging clock.** When the stick is plugged in, idle, and the bridge has sent an RTC sync, the home screen takes over and becomes a wall clock with the pet sleeping underneath. Auto / portrait / landscape orientation (`clock rot`), IMU-driven auto-rotate. See [Charging clock & idle clock](#charging-clock--idle-clock).
- **Idle clock face.** Alternate face that shows `HH:MM:SS` since the last Claude Code transcript touch (capped at `99:59:59`). Toggled via **settings → clock face**. Fed by the new `idle_secs` field in the BLE protocol.
- **Idle clock visible on battery.** The idle face renders even unplugged — useful as an at-a-glance "how long has Claude been quiet" widget. The wall-clock face still requires USB + an RTC sync because it needs real time-of-day. Auto-screen-off still applies on battery so it doesn't drain.
- **Usage cost meter.** Rolling 5h cost meter on the clock screen. `tools/nibblet_usage.py` parses Claude Code transcripts for `input_tokens` / `output_tokens` / cache-tier counts and prices them against Anthropic's published list rates. Visual cap is configurable via `NIBBLET_USAGE_CAP_USD` (default `$50`).
- **Git-aware moods.** Point the bridge at one or more repos with `NIBBLET_GIT_REPOS` and the pet reflects their state when no Claude session is active: `panic` on merge conflicts, `nervous` on long-dirty trees (`NIBBLET_GIT_DIRTY_SECS`), one-shot `heart` when everything goes clean. Standalone scanner: `tools/nibblet_git.py`. Full details in [Git-aware moods](#git-aware-moods).
- **Energy meter + nap accounting.** Five-bar energy that drains 1 bar per 2h awake and tops up on a face-down nap (the stick detects "face-down" via the IMU). Lifetime nap time is persisted and shown on the stats screen.
- **Mood meter.** 0–4 hearts based on the median seconds-to-respond over the last 8 approvals, dragged down a tier by a heavy denial ratio (>33%). Persists across reboots.
- **`tokens_today`.** Output tokens since local midnight, computed by the bridge from Claude Code transcripts and rendered on the stats screen.
- **Two-note "ack" chime** when a remote allow/deny lands — audible feedback that the decision actually reached the stick from across the room.

### Hardened BLE pairing
- **Secure Connections + MITM passkey pairing.** At boot the stick generates a fresh random 6-digit passkey and shows it **right on the device screen** in a "pair me" overlay whenever no bond exists yet. macOS prompts for the same passkey on first pair, then bonds silently. The overlay disappears forever after the first successful pair. (Same passkey is also printed to USB serial as a fallback.) All NUS reads/writes/notifies are AES-CCM encrypted, GATT characteristics carry `ESP_GATT_PERM_*_ENCRYPTED`, and the LE Secure Connections ECDH (P-256) handshake means a passive sniffer can't crack the key the way `crackle` could break legacy pairing. Upstream README documented "no manual pairing button"; this fork replaces that with explicit MITM-resistant pairing. Caveats and remaining gaps documented in [BLE security](#ble-security).

### BLE protocol additions
On top of the upstream snapshot fields (`total / running / waiting / connected / msg / tokens / lines / prompt`):

| New field        | Purpose                                                                                |
| ---------------- | -------------------------------------------------------------------------------------- |
| `entries`        | transcript scroller (replaces upstream's `lines`, ≤8 × 91 chars)                       |
| `tokens_today`   | "today" line on the stats screen                                                       |
| `usage`          | rolling 5h cost meter — `{cents, cap, resets, window}`                                 |
| `idle_secs`      | seconds since the last Claude Code transcript touch (-1 = unknown)                     |
| `time`           | one-shot RTC sync — `[epoch_secs, tz_offset_secs]`                                     |
| `git`            | git mood — `{mood, dirty_secs, conflicts, repo}`                                       |

Plus an **out-of-cycle prompt-state push**: the bridge sends an extra snapshot within ~200ms whenever the prompt changes, on top of its 1Hz heartbeat, so the stick alarms (and stops alarming) at button-press latency instead of once a second.

The host command set also grew. Upstream documented `status`, `name`, `owner`, and a `char_begin` stub. This fork ships the full GIF-streaming protocol on top:

| Added command                                      | Effect                                                                |
| -------------------------------------------------- | --------------------------------------------------------------------- |
| `{"cmd":"species","idx":N}`                        | switch ASCII species; `0xFF` reverts to a loaded GIF                  |
| `{"cmd":"file","path":"...","size":N}`             | open the next file in a GIF bundle upload                             |
| `{"cmd":"chunk","d":"<base64>"}`                   | append ≤300 decoded bytes per chunk, individually acked               |
| `{"cmd":"file_end"}`                               | close current file, verify size                                       |
| `{"cmd":"char_end"}`                               | finalize the upload and switch the stick to GIF mode                  |

### New environment variables
None of these existed upstream:
`NIBBLET_DEVICE`, `NIBBLET_DEVICE_PREFIX`, `NIBBLET_STATE_DIR`, `NIBBLET_SNAPSHOT_INTERVAL`, `NIBBLET_TIME_SYNC_INTERVAL`, `NIBBLET_USAGE_CAP_USD`, `NIBBLET_PERMISSION_TIMEOUT_MS`, `NIBBLET_GIT_REPOS`, `NIBBLET_GIT_DIRTY_SECS`. Full table in [Building the bridge app](#building-the-bridge-app).

### New tooling
Upstream `tools/` shipped four files: `install_character.py`, `make_blob.mjs`, `test_serial.py`, `test_xfer.py`. This fork adds:

- `nibblet_bridge.py`, `nibblet_hook.py`, `nibblet_state.py` — the standalone Claude Code stack
- `nibblet_app.py`, `setup_app.py`, `run_nibblet_bridge.sh`, `run_nibblet_hook.sh` — menu-bar app + venv-aware launchers
- `nibblet_approve.py`, `nibblet_deny.py`, `nibblet_skip.py` — remote prompt resolution
- `nibblet_usage.py` — Claude Code transcript cost scanner
- `nibblet_git.py` — git mood scanner
- `requirements-nibblet.txt` — `bleak` (BLE) + `rumps` (menu-bar app)

### New settings
`wifi` (reserved), `clock rot` (auto/port/land), `clock face` (time/idle). Upstream had `brightness`, `sound`, `bluetooth`, `led`, `transcript`, `ascii pet`, and `reset`.

### New state triggers
- `dizzy` also triggers when the [git mood](#git-aware-moods) is `nervous` or `panic`.
- `heart` also triggers when the git tree just transitioned to clean.

### New docs
- `docs/claude-code.md` — standalone Claude Code bridge setup, hook wiring, and protocol notes. Upstream has no `docs/` directory.

---

## Controls

| Button                  | Normal              | Pet           | Info          | Approval    |
| ----------------------- | ------------------- | ------------- | ------------- | ----------- |
| **A** (front)           | next screen         | next screen   | next screen   | **approve** |
| **B** (right)           | scroll transcript   | next page     | next page     | **deny**    |
| **Hold A**              | menu                | menu          | menu          | menu        |
| **Power** (left, short) | toggle screen off   |               |               |             |
| **Power** (left, ~6s)   | hard power off      |               |               |             |
| **Shake**               | dizzy               |               |               | —           |
| **Face-down**           | nap (energy refills)|               |               |             |

> [!NOTE]
> The screen auto-powers-off after 30s of no interaction (kept on while an approval prompt is up). Any button press wakes it.

---

## Flashing

**You'll need:**

- An M5StickC Plus
- Its USB-C cable
- PlatformIO (`brew install platformio` or the VS Code extension)

**Flash it:**

```bash
pio run -t upload
```

> [!TIP]
> First build downloads the ESP32 toolchain (~1 min). After that it's ~15s. A fresh device boots straight into ASCII buddy mode — no filesystem upload required.

If you're starting from a previously-flashed device, wipe it first:

```bash
pio run -t erase && pio run -t upload
```

You can also wipe everything from the device itself:
**hold A → settings → reset → factory reset → tap twice**.

---

## Pairing

The desktop integration lives in the Claude desktop app under **Prototypes → Nibblet** (nest builds only). Once the app is running and the stick is on, the bridge auto-discovers and connects over BLE.

### First-time pairing

The stick uses **Secure Connections + MITM passkey pairing**. At boot it generates a fresh random 6-digit passkey. As long as the device has zero bonds in NVS (i.e., it's never been paired, or you just ran `pio run -t erase` / factory reset), the passkey is shown **on the device screen** in a centered "pair me" overlay — so you don't need a USB cable or serial terminal to see it. On the very first connect, macOS pops up a dialog asking for that passkey; type in the number on the screen.

As soon as the bond completes, the on-screen overlay disappears forever (until you wipe bonds again). The same passkey is also printed to the USB serial console (`pio device monitor`) as a fallback if you happen to be tethered.

The bond is then stored on both sides; every subsequent reconnect is silent.

> [!IMPORTANT]
> To re-pair (after a "Forget" on macOS or a `pio run -t erase` on the stick), reboot the stick to surface a fresh passkey, then reconnect. After erase, the on-screen overlay reappears immediately on the next boot.

macOS will also prompt for Bluetooth permission the first time the desktop app runs — grant it.

### No nest build?

Use the standalone Claude Code bridge in [docs/claude-code.md](docs/claude-code.md). It talks to the same BLE service and uses Claude Code hooks instead of the desktop prototype.

### Discovery troubleshooting

If discovery isn't finding the stick:

- [ ] Make sure it's awake (any button press)
- [ ] Restart the desktop app — the bridge starts ~15s after launch
- [ ] Check the stick's settings menu → bluetooth is on

---

## Building the bridge app

The Claude Code bridge ships as a macOS menu bar app (`Nibblet.app`) built with `py2app` from `tools/setup_app.py`. Build it once, drop it into `/Applications`, and it auto-runs in the background.

> [!WARNING]
> **Rebuild after every change to anything under `tools/`** — `py2app` freezes a snapshot of the Python files into the bundle, so editing `nibblet_bridge.py` / `nibblet_usage.py` / etc. has no effect on the running app until the bundle is rebuilt.

### Build steps

```bash
cd /path/to/nibblet-main

# 1. Set up the venv (first time only)
python3 -m venv .venv
source .venv/bin/activate
pip install -r tools/requirements-nibblet.txt
pip install py2app

# 2. Quit the currently-running Nibblet.app — py2app cannot overwrite a
#    bundle that's executing. Click the menu bar icon → Quit, or:
pkill -f 'Nibblet.app/Contents/MacOS/Nibblet' 2>/dev/null || true

# 3. Wipe previous artifacts so py2app does a clean rebuild
rm -rf build dist/Nibblet.app

# 4. Build the bundle. First build pulls ~50MB of py2app deps (~1–3 min);
#    subsequent rebuilds are ~15s.
python3 tools/setup_app.py py2app

# 5. Smoke-test from dist/, then drop into /Applications
open dist/Nibblet.app
mv dist/Nibblet.app /Applications/
```

### Environment variables

> [!CAUTION]
> `Nibblet.app` is launched by Finder / launchd, which **doesn't inherit your shell environment** — anything you set in `~/.zshrc` is invisible to the bundle.

The full list of knobs the bridge and hook honor:

| Variable                       | Default                                                                            | What it does                                                                                                                                                  | Read by                |
| ------------------------------ | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `NIBBLET_STATE_DIR`            | `~/Library/Application Support/Nibblet` (macOS) / `~/.nibblet` (Linux)             | Where the hook drops session files and the bridge reads them. **Both halves must agree** — see [Troubleshooting](#troubleshooting).                           | hook, bridge, remote scripts |
| `NIBBLET_DEVICE`               | _(unset)_                                                                          | Exact BLE device name to connect to. Use when multiple sticks are in range.                                                                                   | bridge, app            |
| `NIBBLET_DEVICE_PREFIX`        | `Nibblet`                                                                          | Substring prefix for scan-and-connect when `NIBBLET_DEVICE` is unset.                                                                                         | bridge, app            |
| `NIBBLET_SNAPSHOT_INTERVAL`    | `1.0`                                                                              | Seconds between heartbeat snapshots over BLE. The 200ms prompt-state poll runs independently.                                                                 | bridge, app            |
| `NIBBLET_TIME_SYNC_INTERVAL`   | `60.0`                                                                             | Seconds between RTC sync packets. The stick's RTC drifts and the charging clock won't render until at least one sync has landed.                              | bridge, app            |
| `NIBBLET_USAGE_CAP_USD`        | `50`                                                                               | Visual progress-bar cap on the device's usage meter. This is a manual budget — Anthropic doesn't expose your real subscription limit.                         | bridge                 |
| `NIBBLET_PERMISSION_TIMEOUT_MS`| `600000` (10 min)                                                                  | How long `nibblet_hook.py` waits for an allow/deny decision before giving up and letting Claude Code's native dialog take over. Also `--permission-timeout-ms`.| hook                   |
| `NIBBLET_GIT_REPOS`            | _(unset)_                                                                          | Comma-separated repo paths the bridge should watch for git mood. Empty = feature off. See [Git-aware moods](#git-aware-moods).                                | bridge                 |
| `NIBBLET_GIT_DIRTY_SECS`       | `3600` (1h)                                                                        | How long a repo can stay dirty before the pet gets nervous about it.                                                                                          | bridge                 |

If you want non-defaults to take effect inside `Nibblet.app`, either hard-code them at the top of `tools/nibblet_bridge.py` before rebuilding, or run the bridge from source instead of the bundle:

```bash
source .venv/bin/activate
NIBBLET_USAGE_CAP_USD=75 python3 tools/nibblet_bridge.py
```

> [!NOTE]
> The first launch after a rebuild may re-prompt for Bluetooth permission (the bundle's code signature changed). Grant it.

---

## Remote approve / deny

You can resolve a pending prompt without touching the stick — useful when the stick is across the room or you're already in a terminal:

```bash
python3 tools/nibblet_approve.py   # allow every pending prompt
python3 tools/nibblet_deny.py      # deny every pending prompt
python3 tools/nibblet_skip.py      # let Claude Code's own dialog handle it
```

These drop a decision file the hook picks up within ~200ms. The bridge notices the prompt clear immediately (it polls state at 200ms specifically for prompt-state changes, separate from its 1Hz heartbeat snapshot) and pushes a snapshot to the stick out-of-cycle, so the alert chime stops within ~400ms — the same way it would if you pressed A/B on the device itself. The stick plays a brief two-note "ack" so you can hear that the remote decision landed.

Pressing A or B on the stick *also* immediately silences the in-flight chime instead of letting it tick to the end of `PROMPT_PATTERN`.

> [!WARNING]
> **Caveat: Claude Code path only.** These scripts write a file at `<state>/decisions/<promptId>.json` that `nibblet_hook.py` consumes from its polling loop. They do nothing for the nest Claude Desktop "Prototypes → Nibblet" integration — the desktop app handles approvals through its own internal pipeline and never spawns the hook. If you're on the desktop path, the only remote-approve options are pressing A/B on the stick.

### Troubleshooting

If `nibblet_approve.py` reports `no pending prompts to approve` or the stick keeps alerting after a successful run, work through these in order:

#### 1. State-dir mismatch

Both halves must agree on `NIBBLET_STATE_DIR`. The remote scripts inherit it from your shell; the bundled `Nibblet.app` does not (Finder/launchd doesn't run your `~/.zshrc`). Verify with:

```bash
python3 -c "from tools.nibblet_state import resolve_state_dir; print(resolve_state_dir(None))"
ls "$HOME/Library/Application Support/Nibblet/sessions/"
```

If the script's resolved dir is empty but the bundled app's default has session files, drop `NIBBLET_STATE_DIR` from your shell rc, or bake the same value into `tools/setup_app.py` before rebuilding the bundle.

#### 2. Hook not configured / not running

Without `nibblet_hook.py` wired into `.claude/settings.local.json` for `PermissionRequest`, no session ever gets `waiting=true`, so the script always reports zero pending prompts. Check that `ps aux | grep nibblet_hook` shows a process while you're staring at an unresolved prompt.

#### 3. Stale orphan hooks

_(the alarm keeps going after a CLI approve)_

If you approve a tool call via Claude Code's own CLI permission UI (or via auto-allow rules), that approval path doesn't write a Nibblet decision file. Claude Code does **not** signal or terminate the in-flight `nibblet_hook.py` — it just lets the tool proceed and leaves the hook polling.

The current hook detects this in two ways on its 200ms poll tick (`tools/nibblet_hook.py:296-352`):

- **Slot overwritten.** A newer `PermissionRequest` hook for the same session replaced our prompt — exit silently and let the new one run.
- **Session activity past our baseline.** `session.entries` grew past the snapshot we recorded right after we set the prompt. Hooks fire serially per turn, so any downstream event (`PostToolUse`, `PostToolUseFailure`, `Stop`, a new `UserPromptSubmit`) means Claude Code's native UI accepted the decision and moved past us. Hook clears the prompt via compare-and-swap and exits within ~200ms — the stick stops alarming on the next snapshot.

> [!IMPORTANT]
> Both detections rely on `PostToolUse` / `Stop` hooks being configured in `.claude/settings.local.json` — without them, no downstream event fires, no entries are appended, and the hook has nothing to detect on. The reference config in [docs/claude-code.md](docs/claude-code.md) wires all of them up.

If you have legacy zombies from before this fix (or want to nuke everything by hand):

```bash
pkill -f nibblet_hook.py    # safe — the parent tool calls already returned
```

---

## Git-aware moods

The bridge can watch one or more git repos and use them to color the pet's idle mood. Claude work always wins — the pet only reacts to git when no Claude session needs you.

### Enable

Set the env var(s) before launching the bridge:

```bash
NIBBLET_GIT_REPOS="$HOME/code/nibblet,$HOME/code/work-repo" \
NIBBLET_GIT_DIRTY_SECS=3600 \
python3 tools/nibblet_bridge.py
```

> [!IMPORTANT]
> `Nibblet.app` (the bundled menu-bar app) doesn't see your shell env. To enable git moods inside the bundle, hard-code the values at the top of `tools/nibblet_bridge.py` before rebuilding, or run the bridge from source as shown above.

### What the moods mean

| Mood        | Trigger                                                                              | Pet reaction                                                              |
| ----------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| **panic**   | one or more files have a merge conflict (`UU`/`AA`/`DD`/`AU`/`UA`/`DU`/`UD`)         | `dizzy` state, home msg shows `git: N conflicts`                          |
| **nervous** | the working tree has been dirty for longer than `NIBBLET_GIT_DIRTY_SECS`             | `dizzy` state, home msg shows `<repo>: dirty 2h15m` (only when no Claude) |
| **clean**   | every configured repo has a clean working tree                                       | one-shot `heart` when transitioning out of nervous/panic                  |
| _none_      | no repos configured, or every repo is dirty but under the threshold                  | normal idle behavior                                                      |

When multiple repos are configured, the **worst** mood wins: `panic` > `nervous` > `clean`. `clean` only fires when *every* repo is clean — a mix of clean and "dirty under threshold" stays neutral so the pet doesn't celebrate prematurely.

### How it works

- The bridge runs `git status --porcelain` per repo and caches the result for 30s — typical scan is ~50ms per repo on a warm index, so this is cheap.
- "First dirty" timestamps live in process memory only. Restarting the bridge resets the clock; if your tree was already dirty for 5h, the pet won't get nervous until `NIBBLET_GIT_DIRTY_SECS` after the bridge came up. This is intentional — persisting a tiny dirty-since file would just be extra failure surface for an already-loose hint.
- The clean → heart transition is one-shot and only fires after the pet was nervous/panic; a fresh bridge launch on a clean tree won't pop a heart.

### Smoke-test the scanner

You can run the scanner standalone to see what the bridge will report:

```bash
NIBBLET_GIT_REPOS=/path/to/repo python3 tools/nibblet_git.py
```

It prints a JSON summary including each repo's mood, dirty file count, and any conflicts found.

---

## BLE security

The BLE link uses Secure Connections + MITM passkey pairing (see [Pairing](#pairing) for the user-visible flow). What that buys you and what it doesn't:

### Protected against

- **Passive eavesdropping of session traffic.** All NUS reads/writes/notifies are AES-CCM encrypted with session keys derived from the LTK; the GATT characteristics carry `ESP_GATT_PERM_*_ENCRYPTED` so the stack rejects unencrypted access.
- **Passive sniffing of the pairing handshake.** LE Secure Connections uses ECDH (P-256) for key agreement, so a sniffer that records the entire pair exchange still can't derive the LTK. (This is the long-standing difference vs. Legacy Pairing, which `crackle` could break.)
- **Active MITM during first pair.** This is what the passkey adds. A nearby attacker advertising as "Nibblet" can't complete pairing because they don't know the random 6-digit passkey the real stick prints on its serial console at boot.

### Still observable / not yet hardened

- **Discovery and tracking.** The stick advertises a fixed MAC and device name with `setScanResponse(true)`, so any scanner can see "a Nibblet exists, here's its MAC" and correlate sightings. Resolvable Private Addresses + advertising-only-when-disconnected would close this; not shipped yet.
- **Traffic-analysis side channels.** Packet timing and sizes are visible even when payloads aren't.
- **Bond hygiene.** The ESP32 silently overwrites bonds at ~8 entries; we don't currently cap the bond list or expose a "forget all bonds" button-hold path.

---

## BLE protocol

The stick exposes a Nordic UART Service. The bridge sends newline-delimited JSON snapshots ~1Hz; the stick replies with command acks and approval decisions.

### Snapshot _(bridge → stick)_

```json
{"total":3,"running":2,"waiting":1,"connected":true,
 "msg":"approve: Bash","entries":["edit src/main.cpp","run pio run"],
 "tokens":48000,"tokens_today":12000,
 "prompt":{"id":"p_42","tool":"Bash","hint":"pio run -t upload"},
 "usage":{"cents":340,"cap":2000,"resets":7200,"window":18000},
 "idle_secs":12,"time":[1712592000,-14400],
 "git":{"mood":"nervous","dirty_secs":7200,"conflicts":0,"repo":"nibblet"}}
```

| Field                          | Sets                                                                          |
| ------------------------------ | ----------------------------------------------------------------------------- |
| `connected`                    | sleep ↔ awake (also gated by recent traffic)                                  |
| `total`, `running`, `waiting`  | idle ↔ busy ↔ attention                                                       |
| `msg`                          | bottom line on the home screen (≤23 chars)                                    |
| `entries`                      | transcript scroller (≤8 lines, ≤91 chars each)                                |
| `tokens`                       | cumulative output tokens — feeds the pet, drives levels                       |
| `tokens_today`                 | "today" line on the stats screen                                              |
| `prompt`                       | approval overlay — `{id, tool, hint}`, omit/null to clear                     |
| `usage`                        | rolling 5h cost meter — `{cents, cap, resets, window}`                        |
| `idle_secs`                    | seconds since Claude Code last touched a transcript (-1 = unknown)            |
| `time`                         | one-shot RTC sync — `[epoch_secs, tz_offset_secs]`                            |
| `git`                          | git mood — `{mood, dirty_secs, conflicts, repo}`. `mood` is `clean`/`nervous`/`panic`/`null`. See [Git-aware moods](#git-aware-moods). |

> [!NOTE]
> The bridge sends a full snapshot at 1Hz and an extra out-of-cycle snapshot within ~200ms whenever the prompt state changes.

### Approval reply _(stick → bridge)_

```json
{"cmd":"permission","id":"<promptId>","decision":"once"}
```

### Host commands _(bridge → stick)_

Acked with `{"ack":"<cmd>","ok":...}`.

| Command                                            | Effect                                                                                                                                                                |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `{"cmd":"status"}`                                 | dump battery, uptime, heap, FS free, and stats — replies with the same shape as the `INFO → DEVICE` page                                                              |
| `{"cmd":"name","name":"..."}`                      | rename the pet (NVS-persisted)                                                                                                                                        |
| `{"cmd":"owner","name":"..."}`                     | set owner shown on the boot splash and Pet header                                                                                                                     |
| `{"cmd":"species","idx":N}`                        | switch ASCII species; `0xFF` reverts to a loaded GIF if present                                                                                                       |
| `{"cmd":"char_begin","name":"...","total":N}`      | start a GIF upload; pre-flights free space and wipes `/characters/` if it'll fit                                                                                      |
| `{"cmd":"file","path":"...","size":N}`             | open the next file in the bundle                                                                                                                                      |
| `{"cmd":"chunk","d":"<base64>"}`                   | append ≤300 decoded bytes per chunk; acked individually because the UART RX buffer is only ~256B and LittleFS writes can stall on flash erase, so the sender must wait per ack |
| `{"cmd":"file_end"}`                               | close the current file, verify size matches                                                                                                                           |
| `{"cmd":"char_end"}`                               | finalize the upload and switch the stick to GIF mode                                                                                                                  |

---

## ASCII species

Eighteen species, each with seven hand-authored animations (sleep, idle, busy, attention, celebrate, dizzy, heart). Menu → "next pet" cycles them with a counter. Choice persists to NVS.

The render path is `src/buddy.cpp` + one file per species in `src/buddies/`. Each species file defines a `Species` struct with seven animation function pointers. **Adding a new one is ~100 lines.**

---

## GIF characters (optional)

If you want a custom GIF character instead of an ASCII buddy: a character is a folder with `manifest.json` and seven 135px-wide animated GIFs (one per state). Two ways to install one:

### Option 1 — Bake into the firmware image

Use the prep helper to normalize and crop the GIFs into `data/characters/<name>/`, then flash the LittleFS partition:

```bash
python3 tools/install_character.py characters/bufo
pio run -t uploadfs
```

### Option 2 — Live BLE install

The nest Claude desktop app exposes this as **Pet Manager** — it streams the GIFs over the `char_begin` → `file`/`chunk`/`file_end` → `char_end` sequence (see [BLE protocol](#ble-protocol)) and the stick switches to GIF mode without a reflash.

> [!TIP]
> **Settings → delete char** reverts to ASCII mode.

### Manifest format

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

State values can be a single filename or an array. Arrays rotate — each loop-end advances to the next GIF after a 3s rest, useful for an idle activity carousel.

> [!TIP]
> `gifsicle --lossy=80 -O3 --colors 64` typically cuts size 40–60%.

---

## The seven states

| State        | Trigger                                                    | Feel                          |
| ------------ | ---------------------------------------------------------- | ----------------------------- |
| `sleep`      | bridge not connected                                       | eyes closed, slow breathing   |
| `idle`       | connected, nothing urgent                                  | blinking, looking around      |
| `busy`       | 3+ sessions running                                        | sweating, working             |
| `attention`  | approval pending                                           | alert, **LED blinks**         |
| `celebrate`  | level up (every 50K tokens)                                | confetti, bouncing            |
| `dizzy`      | you shook the stick, or [git mood](#git-aware-moods) is nervous/panic | spiral eyes, wobbling |
| `heart`      | approved in under 5s, or git tree just went clean          | floating hearts               |

---

## Stats

The **Pet** screen (tap A to cycle screens) shows three meters and a counter block. Tap B to flip between the stats page and a one-screen how-to.

> [!NOTE]
> Everything here is local to the stick — the bridge never sees it and a `factory reset` zeros it.

### Meters

| Meter                     | What it tracks                          | Why it moves                                                                                                                                                                                      |
| ------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **mood** (0–4 hearts)     | how snappy you are at approvals         | median seconds-to-respond over the last 8 approvals: `<15s` = 4 hearts, `15–30s` = 3, `30–60s` = 2, `60–120s` = 1, slower = 0. A heavy denial ratio (>33% of the last decisions) drags it down a tier. |
| **fed** (10 pips)         | progress toward the next level          | one pip per 5K cumulative output tokens. Fills 0 → 10 across 50K tokens, then resets and the level number ticks up (and the pet plays `celebrate`).                                               |
| **energy** (0–5 bars)     | rest state                              | tops up to full when a nap ends (face-down → wake), drains 1 bar per 2 hours awake. Boots at 3/5.                                                                                                 |

### Counter block

| Line                       | Meaning                                                                                       |
| -------------------------- | --------------------------------------------------------------------------------------------- |
| **Lv N**                   | total levels reached — one per 50K cumulative output tokens, persists across reboots          |
| **approved** / **denied**  | lifetime approval and denial counts from the device or remote tools                           |
| **napped**                 | total face-down nap time (`Hh MMm`)                                                           |
| **tokens**                 | lifetime cumulative output tokens the bridge has reported                                     |
| **today**                  | output tokens since local midnight (computed by the bridge from Claude Code transcripts)      |

### How tokens get counted

The bridge sums output tokens across active Claude sessions and sends a running total in each snapshot. The stick tracks deltas, so a bridge restart resyncs without re-crediting the session, and a device reboot latches the first packet to avoid double-counting.

> [!WARNING]
> Tokens accumulate in RAM and only persist to NVS on a level-up — worst case on a hard power-off is losing up to 50K tokens of in-flight progress. (NVS sectors wear out around 100K writes, so we don't flush every heartbeat.)

### Why stats are persisted to NVS

Mood, level, and approval/denial counters survive reboots so the pet feels continuous across days. Stats only write on meaningful events (approval, denial, nap end, level-up), never on a timer.

---

## Settings menu

**Hold A → settings.** Toggles persist to NVS. The full list:

| Setting        | What it does                                                                                                                                                              |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `brightness`   | 0/4 – 4/4, mapped to `ScreenBreath` 20–100                                                                                                                                |
| `sound`        | the chime sequencer (boot, prompt nag, ack, level-up)                                                                                                                     |
| `bluetooth`    | BLE advertising on/off — turns off the radio entirely                                                                                                                     |
| `wifi`         | reserved; pref persists but no WiFi stack is linked yet                                                                                                                   |
| `led`          | the red LED that blinks during `attention`                                                                                                                                |
| `transcript`   | the bottom-of-screen HUD that scrolls Claude messages                                                                                                                     |
| `clock rot`    | charging-clock orientation: `auto` / `port` / `land`                                                                                                                      |
| `clock face`   | charging-clock content: `time` (wall clock) or `idle` (seconds since the last Claude Code transcript touch, fed by `idle_secs`)                                           |
| `ascii pet`    | cycles through the 18 species + the GIF character if loaded                                                                                                               |
| `reset`        | submenu: `delete char` (wipe GIFs only) / `factory reset` (NVS + LittleFS, two-tap arm). BLE bonds live in a separate partition and are untouched.                        |

---

## Charging clock & idle clock

The home screen has two alternate "clock" faces, picked by **settings → clock face**:

| Face   | Shows                                                            | Needs                                                  |
| ------ | ---------------------------------------------------------------- | ------------------------------------------------------ |
| `time` | wall clock + date, pet sleeping underneath                       | USB power **and** an RTC sync from the bridge          |
| `idle` | `HH:MM:SS` since the last Claude Code transcript touch (`since claude`) | `idle_secs` from the bridge — no RTC, no USB required  |

Both faces only take over when nothing else is happening: the stick must be on the home screen, with no running/waiting sessions, no prompt, and no menu open. The clock disappears the instant a session starts.

### Time face (USB only)

When all the requirements above are met *and* the stick is plugged in, the wall-clock face takes over and the pet's mood follows the clock:

- 🌙 sleepy at night
- 💖 occasional `heart` on weekends
- 🎉 `celebrate` on Friday afternoons
- 💫 `dizzy` near midnight

`clock rot` picks portrait vs landscape vs auto-rotate (auto uses the IMU). The face disappears as soon as a session starts or you unplug.

### Idle face (works on battery)

The `idle` face is allowed even when running on battery — it doesn't need wall-clock time, and the bridge's `idle_secs` field is enough to render. Auto-screen-off (30s of no interaction) still applies on battery so it doesn't drain. Tap any button to wake it; it'll come back showing the same counter.

> [!NOTE]
> If the bridge hasn't sent any `idle_secs` yet (fresh boot, bridge offline), the face shows `--:--:--` until the first heartbeat lands.

---

## Project layout

### Firmware _(C++ / Arduino / ESP-IDF)_

```
src/
  main.cpp                 — loop, state machine, UI screens, button input
  buddy.cpp                — ASCII species dispatch + render helpers
  buddies/                 — one file per species, seven anim functions each
  ble_bridge.cpp/h         — Nordic UART service, line-buffered TX/RX, NimBLE pairing
  character.cpp/h          — GIF decode + text-mode (legacy path)
  data.h                   — wire protocol, JSON parse, demo mode
  xfer.h                   — file transfer receiver (char_begin/file/chunk/end)
  stats.h                  — NVS-backed stats, settings, owner, species choice
```

### Host-side Python _(bridge, hook, helpers)_

```
tools/
  nibblet_bridge.py        — BLE bridge: reads sessions, pushes snapshots
  nibblet_hook.py          — Claude Code hook: writes sessions, polls decisions
  nibblet_state.py         — shared state-dir + file-lock primitives
  nibblet_usage.py         — rolling-window cost scan over CC transcripts
  nibblet_app.py           — menu-bar wrapper around the bridge (rumps)
  nibblet_approve.py       — remote allow for any pending prompt
  nibblet_deny.py          — remote deny for any pending prompt
  nibblet_skip.py          — clear the prompt and defer to Claude Code's UI
  install_character.py     — normalize/crop GIFs into data/characters/<name>/
  setup_app.py             — py2app spec for building Nibblet.app
  run_nibblet_bridge.sh    — venv-aware launcher used by the menu-bar app
  run_nibblet_hook.sh      — venv-aware launcher used by .claude/settings hooks
  requirements-nibblet.txt — `bleak` (BLE) + `rumps` (menu-bar app)
```

### Assets & config

```
data/characters/           — GIFs baked into LittleFS via `pio run -t uploadfs`
characters/                — example character source folders (manifest + GIFs)
docs/claude-code.md        — standalone Claude Code bridge setup
assets/                    — app icon (icns/iconset/svg) for the macOS bundle
platformio.ini             — board, partition table, flags, lib deps
```
