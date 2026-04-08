#!/usr/bin/env python3
"""Cancel any pending Nibblet stick approval and defer to Claude Code's
native permission dialog. Drops a `skip` decision file for every active
prompt id, which the hook polling loop picks up — it clears the prompt,
returns no decision, and Claude Code falls back to its built-in dialog.
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
        description="Skip pending Nibblet approvals and defer to Claude Code."
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
        print("nibblet: no pending prompts to skip")
        return 0
    now = time.time()
    for session in pending:
        prompt_id = session["prompt"]["id"]
        write_decision(state_dir, prompt_id, {"decision": "skip", "received_at": now})
        print(f"nibblet: skipped prompt {prompt_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
