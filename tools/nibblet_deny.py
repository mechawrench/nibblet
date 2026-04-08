#!/usr/bin/env python3
"""Deny any pending Nibblet stick prompt without touching the hardware.

Drops a `deny` decision for every active prompt id. The hook polling
loop picks it up within ~200ms, returns "deny" to Claude Code so the
tool call is refused, and clears the prompt from session state. The
next bridge snapshot tells the stick to dismiss its alert (firmware
plays a short two-note "ack" chime since the user didn't press a
button).

Use when you want to deny from your keyboard / a terminal alias /
another Claude Code window without reaching for the stick.
"""
import argparse
import sys
import time
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .nibblet_state import list_sessions, resolve_state_dir, write_decision
except ImportError:
    from nibblet_state import list_sessions, resolve_state_dir, write_decision


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deny pending Nibblet prompts without using the stick."
    )
    parser.add_argument("--state-dir", help="Override the Nibblet state directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state_dir = resolve_state_dir(args.state_dir)
    sessions = list_sessions(state_dir)
    pending = [
        s for s in sessions
        if s.get("waiting") and isinstance(s.get("prompt"), dict) and s["prompt"].get("id")
    ]
    if not pending:
        print("nibblet: no pending prompts to deny")
        return 1
    now = time.time()
    for session in pending:
        prompt_id = session["prompt"]["id"]
        write_decision(state_dir, prompt_id, {"decision": "deny", "received_at": now})
        print(f"nibblet: denied prompt {prompt_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
