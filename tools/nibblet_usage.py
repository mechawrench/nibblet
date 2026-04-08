#!/usr/bin/env python3
"""Compute real Claude Code usage from local transcript JSONL files.

Claude Code writes per-message transcripts to ~/.claude/projects/<encoded
project>/<session-uuid>.jsonl. Each assistant entry includes the API
`usage` block (input_tokens, output_tokens, cache_creation_input_tokens,
cache_read_input_tokens) plus a `model` and ISO `timestamp`. We sum those
for the rolling window the user is interested in (default 5h) and convert
to API-list-price dollars per model.

Caveats:
- The transcript schema is undocumented; field names could change in a
  future Claude Code release. The reader is defensive: missing fields and
  malformed lines are skipped, never raised.
- Message ids repeat across the same transcript (streaming chunk replays),
  so we dedupe by message.id before summing — without this you can be off
  by 30-50%.
- Pricing is API list price as of 2025-10. Subscription plans like Pro
  $100 / Max do not bill at list price; this number is "what you would
  have spent on the API", which is the same metric Claude Code's /cost
  command reports.
- The 5h "rolling window" is computed locally as "all messages whose
  timestamp is within the last 5 hours". This is not synced with
  Anthropic's server-side bucket — there can be small drift but it
  should match closely for ordinary use.
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_WINDOW_SECS = 5 * 3600


# Per 1M tokens, USD. Pulled from Anthropic's published list pricing.
# Update when Anthropic changes pricing.
PRICING_OPUS_4 = {
    "in":   15.00,
    "out":  75.00,
    "cw5":  18.75,   # cache write, 5-minute ephemeral
    "cw1h": 30.00,   # cache write, 1-hour ephemeral
    "cr":    1.50,   # cache read
}
PRICING_SONNET_4 = {"in": 3.00, "out": 15.00, "cw5": 3.75, "cw1h": 6.00, "cr": 0.30}
PRICING_HAIKU_4  = {"in": 1.00, "out":  5.00, "cw5": 1.25, "cw1h": 2.00, "cr": 0.10}
PRICING_HAIKU_3  = {"in": 0.80, "out":  4.00, "cw5": 1.00, "cw1h": 1.60, "cr": 0.08}


def model_pricing(model: str) -> dict[str, float]:
    """Map a model id like `claude-opus-4-6` to its pricing block.
    Falls back to Opus pricing for unknowns so we over-report rather than
    silently under-bill.
    """
    if not model:
        return PRICING_OPUS_4
    m = model.lower()
    if "opus" in m:
        return PRICING_OPUS_4
    if "sonnet" in m:
        return PRICING_SONNET_4
    if "haiku-4" in m or "haiku-5" in m:
        return PRICING_HAIKU_4
    if "haiku" in m:
        return PRICING_HAIKU_3
    return PRICING_OPUS_4


def parse_iso_ts(value: str) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def message_cost(usage: dict[str, Any], pricing: dict[str, float]) -> float:
    in_tok  = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    cw_tok  = int(usage.get("cache_creation_input_tokens") or 0)
    cr_tok  = int(usage.get("cache_read_input_tokens") or 0)

    # If the message provides a 5m / 1h cache write breakdown, use it;
    # otherwise charge the whole bucket as 5m (the default ephemeral).
    cw_5m = cw_1h = 0
    cw_detail = usage.get("cache_creation")
    if isinstance(cw_detail, dict):
        cw_5m = int(cw_detail.get("ephemeral_5m_input_tokens") or 0)
        cw_1h = int(cw_detail.get("ephemeral_1h_input_tokens") or 0)
    if cw_5m + cw_1h == 0 and cw_tok > 0:
        cw_5m = cw_tok

    return (
        in_tok  * pricing["in"]
        + out_tok * pricing["out"]
        + cw_5m   * pricing["cw5"]
        + cw_1h   * pricing["cw1h"]
        + cr_tok  * pricing["cr"]
    ) / 1_000_000.0


def last_activity_secs(projects_dir: Path = CLAUDE_PROJECTS_DIR) -> int | None:
    """Seconds since the most recent transcript JSONL was written.

    Claude Code appends a line to the active session's transcript on every
    user prompt, assistant response, and tool call — so the max mtime
    across all transcripts is exactly "the last time Claude Code was used".
    Returns None if no transcripts exist (Claude Code never run).

    Cheap enough to call per-snapshot: ~1 stat() per file, no reads.
    """
    if not projects_dir.is_dir():
        return None
    newest = 0.0
    try:
        for path in glob.glob(str(projects_dir / "*" / "*.jsonl")):
            try:
                m = os.path.getmtime(path)
            except OSError:
                continue
            if m > newest:
                newest = m
    except Exception:
        return None
    if newest <= 0:
        return None
    delta = int(time.time() - newest)
    return max(0, delta)


def empty_usage(window_secs: int) -> dict[str, Any]:
    return {
        "window_secs": window_secs,
        "messages": 0,
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_cache_write": 0,
        "tokens_cache_read": 0,
        "tokens_total": 0,
        "cost_usd": 0.0,
        "cost_cents": 0,
        "resets_in_secs": 0,
    }


def compute_usage(window_secs: int = DEFAULT_WINDOW_SECS,
                  projects_dir: Path = CLAUDE_PROJECTS_DIR) -> dict[str, Any]:
    """Walk every JSONL transcript Claude Code has written and sum the
    usage of every assistant message whose timestamp falls within the
    rolling window. Returns the same shape on every call (zeros if no
    data) so callers can blindly serialize it.
    """
    now = time.time()
    cutoff = now - window_secs

    if not projects_dir.is_dir():
        return empty_usage(window_secs)

    # mtime pre-filter: a file last modified before the window started
    # cannot contribute. Saves reading hundreds of stale transcripts.
    candidate_files: list[str] = []
    try:
        for path in glob.glob(str(projects_dir / "*" / "*.jsonl")):
            try:
                if os.path.getmtime(path) >= cutoff - 60:
                    candidate_files.append(path)
            except OSError:
                continue
    except Exception:
        return empty_usage(window_secs)

    seen_ids: set[str] = set()
    total_in = total_out = total_cw = total_cr = 0
    cost_usd = 0.0
    oldest_in_window: float | None = None
    n_messages = 0

    for path in candidate_files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line[0] != "{":
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") != "assistant":
                        continue
                    msg = entry.get("message")
                    if not isinstance(msg, dict):
                        continue
                    mid = msg.get("id")
                    if not mid or mid in seen_ids:
                        continue
                    ts = parse_iso_ts(entry.get("timestamp"))
                    if ts is None or ts < cutoff:
                        continue
                    usage = msg.get("usage")
                    if not isinstance(usage, dict):
                        continue

                    seen_ids.add(mid)
                    n_messages += 1
                    pricing = model_pricing(str(msg.get("model") or ""))
                    cost_usd += message_cost(usage, pricing)
                    total_in  += int(usage.get("input_tokens") or 0)
                    total_out += int(usage.get("output_tokens") or 0)
                    total_cw  += int(usage.get("cache_creation_input_tokens") or 0)
                    total_cr  += int(usage.get("cache_read_input_tokens") or 0)
                    if oldest_in_window is None or ts < oldest_in_window:
                        oldest_in_window = ts
        except OSError:
            continue

    resets_in_secs = 0
    if oldest_in_window is not None:
        resets_in_secs = max(0, int(oldest_in_window + window_secs - now))

    return {
        "window_secs": window_secs,
        "messages": n_messages,
        "tokens_input": total_in,
        "tokens_output": total_out,
        "tokens_cache_write": total_cw,
        "tokens_cache_read": total_cr,
        "tokens_total": total_in + total_out + total_cw + total_cr,
        "cost_usd": round(cost_usd, 4),
        "cost_cents": int(round(cost_usd * 100)),
        "resets_in_secs": resets_in_secs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print real Claude Code usage from local transcripts."
    )
    parser.add_argument(
        "--window", type=int, default=DEFAULT_WINDOW_SECS,
        help="Rolling window in seconds (default 18000 = 5h).",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    args = parser.parse_args()
    result = compute_usage(args.window)
    print(json.dumps(result, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
