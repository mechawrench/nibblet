#!/usr/bin/env python3
import argparse
import json
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .nibblet_state import (
        append_entry,
        file_lock,
        read_json,
        remove_session,
        resolve_state_dir,
        session_path,
        set_msg,
        take_decision,
        truncate,
        update_session,
    )
except ImportError:
    from nibblet_state import (
        append_entry,
        file_lock,
        read_json,
        remove_session,
        resolve_state_dir,
        session_path,
        set_msg,
        take_decision,
        truncate,
        update_session,
    )


DEFAULT_PERMISSION_TIMEOUT_MS = 10 * 60 * 1000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Claude Code hook for Nibblet.")
    parser.add_argument("--state-dir", help="Override the Nibblet state directory.")
    parser.add_argument(
        "--permission-timeout-ms",
        type=int,
        default=int(os.environ.get("NIBBLET_PERMISSION_TIMEOUT_MS", DEFAULT_PERMISSION_TIMEOUT_MS)),
        help="How long to wait for an allow/deny decision from the device.",
    )
    return parser.parse_args()


def load_hook_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def summarize_tool(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Bash":
        return truncate(tool_input.get("description") or tool_input.get("command"), 44)
    if tool_name in {"Read", "Write", "Edit"}:
        return truncate(tool_input.get("file_path"), 44)
    if tool_name == "Glob":
        return truncate(tool_input.get("pattern"), 44)
    if tool_name == "Grep":
        return truncate(tool_input.get("pattern"), 44)
    if tool_name == "WebFetch":
        return truncate(tool_input.get("url"), 44)
    if tool_name == "WebSearch":
        return truncate(tool_input.get("query"), 44)
    if tool_name == "Agent":
        return truncate(tool_input.get("description") or tool_input.get("prompt"), 44)
    return truncate(json.dumps(tool_input, ensure_ascii=True), 44)


def format_prompt_hint(tool_name: str, tool_input: dict[str, Any]) -> str:
    summary = summarize_tool(tool_name, tool_input)
    if summary:
        return f"{tool_name}: {summary}"
    return tool_name


def clear_prompt(session: dict[str, Any]) -> None:
    session["waiting"] = False
    session["prompt"] = None


def on_session_start(state_dir: Path, ctx: dict[str, Any]) -> None:
    def mutate(session: dict[str, Any]) -> None:
        session["active"] = True
        session["running"] = False
        clear_prompt(session)
        set_msg(session, "Claude connected")
        append_entry(session, "Session ready")

    update_session(state_dir, ctx["session_id"], ctx["cwd"], mutate)


def on_user_prompt(state_dir: Path, ctx: dict[str, Any]) -> None:
    prompt = truncate(ctx.get("prompt"), 72)

    def mutate(session: dict[str, Any]) -> None:
        session["active"] = True
        session["running"] = True
        clear_prompt(session)
        set_msg(session, "Thinking...")
        append_entry(session, f"You: {prompt}")

    update_session(state_dir, ctx["session_id"], ctx["cwd"], mutate)


def on_pre_tool_use(state_dir: Path, ctx: dict[str, Any]) -> None:
    tool_name = ctx.get("tool_name", "Tool")
    tool_input = ctx.get("tool_input") or {}
    summary = summarize_tool(tool_name, tool_input)

    def mutate(session: dict[str, Any]) -> None:
        session["active"] = True
        session["running"] = True
        set_msg(session, f"{tool_name}...")
        append_entry(session, f"{tool_name}: {summary or 'running'}")

    update_session(state_dir, ctx["session_id"], ctx["cwd"], mutate)


def on_post_tool_use(state_dir: Path, ctx: dict[str, Any]) -> None:
    tool_name = ctx.get("tool_name", "Tool")
    tool_input = ctx.get("tool_input") or {}
    summary = summarize_tool(tool_name, tool_input)

    def mutate(session: dict[str, Any]) -> None:
        session["active"] = True
        session["running"] = True
        set_msg(session, f"{tool_name} ok")
        append_entry(session, f"{tool_name} ok: {summary or 'done'}")

    update_session(state_dir, ctx["session_id"], ctx["cwd"], mutate)


def on_post_tool_failure(state_dir: Path, ctx: dict[str, Any]) -> None:
    tool_name = ctx.get("tool_name", "Tool")
    error = truncate(ctx.get("error"), 60)

    def mutate(session: dict[str, Any]) -> None:
        session["active"] = True
        session["running"] = True
        set_msg(session, f"{tool_name} failed")
        append_entry(session, f"{tool_name} failed: {error}")

    update_session(state_dir, ctx["session_id"], ctx["cwd"], mutate)


def on_stop(state_dir: Path, ctx: dict[str, Any]) -> None:
    message = truncate(ctx.get("last_assistant_message"), 72)

    def mutate(session: dict[str, Any]) -> None:
        session["active"] = True
        session["running"] = False
        clear_prompt(session)
        set_msg(session, "Idle")
        append_entry(session, f"Claude: {message or 'done'}")

    update_session(state_dir, ctx["session_id"], ctx["cwd"], mutate)


def on_stop_failure(state_dir: Path, ctx: dict[str, Any]) -> None:
    error = truncate(ctx.get("last_assistant_message") or ctx.get("error"), 72)

    def mutate(session: dict[str, Any]) -> None:
        session["active"] = True
        session["running"] = False
        clear_prompt(session)
        set_msg(session, "Error")
        append_entry(session, f"Error: {error}")

    update_session(state_dir, ctx["session_id"], ctx["cwd"], mutate)


def on_notification(state_dir: Path, ctx: dict[str, Any]) -> None:
    note_type = ctx.get("notification_type")
    message = truncate(ctx.get("message"), 72)

    def mutate(session: dict[str, Any]) -> None:
        if note_type == "idle_prompt":
            session["running"] = False
            set_msg(session, "Idle")
        elif note_type == "permission_prompt":
            set_msg(session, "Approve?")
        append_entry(session, f"{note_type}: {message}")

    update_session(state_dir, ctx["session_id"], ctx["cwd"], mutate)


def on_session_end(state_dir: Path, ctx: dict[str, Any]) -> None:
    remove_session(state_dir, ctx["session_id"])


def emit_permission_response(behavior: str, message: str | None = None) -> None:
    payload: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": behavior},
        }
    }
    if behavior == "deny" and message:
        payload["hookSpecificOutput"]["decision"]["message"] = message
    print(json.dumps(payload, ensure_ascii=True))


def _read_session_snapshot(state_dir: Path, session_id: str) -> tuple[str | None, int]:
    """Read the session's current prompt id and entries length atomically.

    Returns `(prompt_id, entries_len)` — both used by `on_permission_request`'s
    polling loop for orphan detection. Reading both in one locked load
    avoids races where the session is updated between two separate reads.
    """
    with file_lock(state_dir):
        payload = read_json(session_path(state_dir, session_id))
    if not isinstance(payload, dict):
        return (None, 0)
    prompt = payload.get("prompt")
    pid: str | None = None
    if isinstance(prompt, dict):
        raw = prompt.get("id")
        if isinstance(raw, str):
            pid = raw
    entries = payload.get("entries")
    entries_len = len(entries) if isinstance(entries, list) else 0
    return (pid, entries_len)


def on_permission_request(state_dir: Path, ctx: dict[str, Any], timeout_ms: int) -> None:
    tool_name = ctx.get("tool_name", "Tool")
    tool_input = ctx.get("tool_input") or {}
    prompt_id = uuid.uuid4().hex
    prompt_hint = format_prompt_hint(tool_name, tool_input)
    short_hint = truncate(prompt_hint, 43)
    session_id = ctx["session_id"]
    cwd = ctx["cwd"]

    def mutate(session: dict[str, Any]) -> None:
        session["active"] = True
        session["running"] = True
        session["waiting"] = True
        session["prompt"] = {"id": prompt_id, "tool": truncate(tool_name, 19), "hint": short_hint}
        set_msg(session, f"Approve: {tool_name}")
        append_entry(session, f"Awaiting approval: {short_hint}")

    initial = update_session(state_dir, session_id, cwd, mutate)
    # Baseline for orphan detection — if session.entries grows past this
    # while we're polling, *some other hook event* has fired for the same
    # session, which means Claude Code moved past our PermissionRequest
    # (typically because the user approved/denied via the native CLI UI).
    # Hooks fire serially per turn, so the only thing that can append
    # entries between now and our resolution is downstream processing
    # of our own tool call (PostToolUse, PostToolUseFailure, Stop) or a
    # brand-new user turn (UserPromptSubmit) — all of which mean Claude
    # Code is no longer waiting on us.
    baseline_entries = len(initial.get("entries", []))

    # Compare-and-swap helper: only clear the prompt slot if it still
    # contains *our* prompt_id. Without this, a stale hook timing out
    # (or catching a signal) would wipe out a newer hook's prompt and
    # leave the bridge unable to surface it. Bug repros under rapid
    # back-to-back tool calls because session state has only one
    # `prompt` slot per session_id.
    def _cas_clear(suffix_msg: str, log_entry: str, drop_running: bool = False):
        def _mutate(session: dict[str, Any]) -> None:
            current = session.get("prompt")
            if isinstance(current, dict) and current.get("id") == prompt_id:
                clear_prompt(session)
                set_msg(session, suffix_msg)
                append_entry(session, log_entry)
                if drop_running:
                    session["running"] = False
        return _mutate

    # If Claude Code is interrupted (Ctrl+C, parent kill, session aborted)
    # while we're polling, the hook process dies and the prompt would
    # otherwise stay stuck in session state forever — bridge keeps sending
    # it, stick keeps alerting. Catch SIGINT/SIGTERM, clear the prompt
    # so the next snapshot tells the stick to dismiss its alert, then
    # exit. The firmware sees `responseSent == false` and plays the
    # external-cancel chime. Drop running too — Stop hook won't fire
    # when CC is killed mid-prompt, so without this the bridge would
    # still report the session as "running" until it ages out and the
    # stick would never go to the clock face.
    def cleanup_and_exit(signum, _frame):
        try:
            update_session(state_dir, session_id, cwd, _cas_clear(
                "Cancelled", f"Cancelled (signal): {short_hint}", drop_running=True
            ))
        except Exception:
            pass
        # 130 = SIGINT (Ctrl+C), 143 = SIGTERM convention
        sys.exit(130 if signum == signal.SIGINT else 143)

    prev_int  = signal.signal(signal.SIGINT,  cleanup_and_exit)
    prev_term = signal.signal(signal.SIGTERM, cleanup_and_exit)

    try:
        deadline = time.time() + max(timeout_ms, 0) / 1000.0
        while time.time() < deadline:
            decision = take_decision(state_dir, prompt_id)
            if decision:
                choice = decision.get("decision")
                if choice in {"once", "allow"}:
                    update_session(state_dir, session_id, cwd, _cas_clear(
                        f"{tool_name} allowed", f"Approved: {short_hint}"
                    ))
                    emit_permission_response("allow")
                    return
                if choice == "deny":
                    update_session(state_dir, session_id, cwd, _cas_clear(
                        f"{tool_name} denied", f"Denied: {short_hint}"
                    ))
                    emit_permission_response("deny", "Denied from Nibblet")
                    return
                if choice == "skip":
                    # User wants to handle this approval inside Claude Code's
                    # own UI instead of on the stick. Clear the pending prompt
                    # so the next snapshot tells the stick to dismiss its
                    # alert, then return without emitting a permission decision
                    # — Claude Code will fall back to its native dialog.
                    update_session(state_dir, session_id, cwd, _cas_clear(
                        "Handling locally", f"Skipped to local: {short_hint}"
                    ))
                    return

            # Orphan checks. Two ways the parent can move on without
            # writing a Nibblet decision file:
            #
            #   (a) A newer PermissionRequest hook for this same session
            #       has overwritten our prompt slot. The newer hook owns
            #       the slot now; exit silently and let it run.
            #
            #   (b) Some other hook event has fired for this session
            #       (PostToolUse / PostToolUseFailure / Stop / a new
            #       UserPromptSubmit). Hooks are serial per turn, so
            #       *any* downstream event firing means Claude Code's
            #       native UI accepted the decision and moved past us.
            #       Detect via entries-length growth past our baseline.
            #
            # Either way: clear our prompt slot via _cas_clear (CAS
            # protects against trampling a newer hook in case (a)) and
            # return without emitting a permission decision — Claude
            # Code is no longer waiting on us.
            current_pid, current_entries_len = _read_session_snapshot(state_dir, session_id)
            if current_pid is not None and current_pid != prompt_id:
                return
            if current_entries_len > baseline_entries:
                update_session(state_dir, session_id, cwd, _cas_clear(
                    f"{tool_name} (CLI)", f"Handled in CLI: {short_hint}"
                ))
                return

            time.sleep(0.2)

        update_session(state_dir, session_id, cwd, _cas_clear(
            "Waiting locally", f"Approval timed out: {short_hint}"
        ))
    finally:
        signal.signal(signal.SIGINT,  prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def main() -> int:
    args = parse_args()
    ctx = load_hook_payload()
    if not ctx:
        return 0

    state_dir = resolve_state_dir(args.state_dir, ctx.get("cwd"))
    event = ctx.get("hook_event_name")

    if event == "SessionStart":
        on_session_start(state_dir, ctx)
    elif event == "UserPromptSubmit":
        on_user_prompt(state_dir, ctx)
    elif event == "PreToolUse":
        on_pre_tool_use(state_dir, ctx)
    elif event == "PostToolUse":
        on_post_tool_use(state_dir, ctx)
    elif event == "PostToolUseFailure":
        on_post_tool_failure(state_dir, ctx)
    elif event == "Notification":
        on_notification(state_dir, ctx)
    elif event == "Stop":
        on_stop(state_dir, ctx)
    elif event == "StopFailure":
        on_stop_failure(state_dir, ctx)
    elif event == "SessionEnd":
        on_session_end(state_dir, ctx)
    elif event == "PermissionRequest":
        on_permission_request(state_dir, ctx, args.permission_timeout_ms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
