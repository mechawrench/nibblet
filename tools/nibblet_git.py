#!/usr/bin/env python3
"""Git status scanner for the Nibblet bridge.

Watches a configurable list of repos and classifies their state into a
mood the firmware can react to:

  - panic    → at least one merge conflict
  - nervous  → uncommitted changes for longer than NIBBLET_GIT_DIRTY_SECS
  - clean    → no uncommitted changes anywhere
  - None     → no opinion (no repos configured, or dirty under threshold)

Configuration:

  NIBBLET_GIT_REPOS         comma-separated paths. ~ is expanded. Empty
                            disables the feature.
  NIBBLET_GIT_DIRTY_SECS    seconds a repo can stay dirty before it
                            counts as "nervous" (default 3600 = 1h).

The "first dirty" timestamp lives in process memory, not on disk: a
bridge restart resets the clock for every dirty repo. That is fine —
the threshold is on the order of an hour, the bridge restarts on a
fresh launch, and persisting a tiny "dirty since" file would just be
extra failure surface.
"""
import os
import subprocess
import time
from pathlib import Path
from typing import Any


# Per git docs, --porcelain=v1 reports unmerged paths with one of these
# two-character codes. Anything else is a regular dirty file.
_CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}

# Process-local: when did each repo first transition from clean → dirty?
# {absolute_repo_path: timestamp_seconds}
_dirty_since: dict[str, float] = {}


def _resolve_repos() -> list[Path]:
    raw = os.environ.get("NIBBLET_GIT_REPOS", "").strip()
    if not raw:
        return []
    out: list[Path] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        path = Path(os.path.expanduser(chunk))
        try:
            path = path.resolve()
        except OSError:
            continue
        if path.is_dir():
            out.append(path)
    return out


def _dirty_threshold_secs() -> int:
    raw = os.environ.get("NIBBLET_GIT_DIRTY_SECS", "3600")
    try:
        return max(1, int(float(raw)))
    except ValueError:
        return 3600


def _scan_one(repo: Path) -> dict[str, Any] | None:
    """Run `git status --porcelain` and summarize. Returns None on error."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None

    conflicts = 0
    dirty_files = 0
    for line in result.stdout.splitlines():
        if len(line) < 2:
            continue
        code = line[:2]
        if code in _CONFLICT_CODES:
            conflicts += 1
        if code != "  ":
            dirty_files += 1

    return {
        "repo": repo.name,
        "path": str(repo),
        "dirty": dirty_files > 0,
        "files": dirty_files,
        "conflicts": conflicts,
    }


def _classify(info: dict[str, Any], dirty_secs: float, threshold: int) -> str | None:
    if info["conflicts"] > 0:
        return "panic"
    if not info["dirty"]:
        return "clean"
    if dirty_secs >= threshold:
        return "nervous"
    return None  # dirty but under the threshold — no opinion yet


def scan(now: float | None = None) -> dict[str, Any]:
    """Scan every configured repo and aggregate to a single mood.

    The aggregate "worst" mood is `panic` > `nervous` > `clean`. `clean`
    only wins if every configured repo is clean — a mix of clean and
    "dirty under threshold" reports `None`, since at least one repo
    might be on the way to nervous and we shouldn't celebrate yet.
    """
    if now is None:
        now = time.time()

    repos = _resolve_repos()
    if not repos:
        return {
            "mood": None,
            "repos": [],
            "dirty_secs": 0,
            "conflicts": 0,
            "repo": "",
        }

    threshold = _dirty_threshold_secs()
    per_repo: list[dict[str, Any]] = []
    worst_dirty_secs = 0
    total_conflicts = 0

    for repo in repos:
        info = _scan_one(repo)
        if info is None:
            continue

        path_key = info["path"]
        if info["dirty"]:
            first = _dirty_since.get(path_key)
            if first is None:
                first = now
                _dirty_since[path_key] = first
            dirty_secs = max(0.0, now - first)
        else:
            _dirty_since.pop(path_key, None)
            dirty_secs = 0.0

        info["mood"] = _classify(info, dirty_secs, threshold)
        info["dirty_secs"] = int(dirty_secs)
        per_repo.append(info)

        if int(dirty_secs) > worst_dirty_secs:
            worst_dirty_secs = int(dirty_secs)
        total_conflicts += info["conflicts"]

    moods = [r["mood"] for r in per_repo]
    if "panic" in moods:
        aggregate = "panic"
    elif "nervous" in moods:
        aggregate = "nervous"
    elif moods and all(m == "clean" for m in moods):
        aggregate = "clean"
    else:
        aggregate = None

    # Pick the repo whose status drove the aggregate so the firmware
    # has something to name. Falls back to the first repo if everything
    # is None or clean.
    feature_repo = ""
    if per_repo:
        if aggregate in ("panic", "nervous"):
            for r in per_repo:
                if r["mood"] == aggregate:
                    feature_repo = r["repo"]
                    break
        else:
            feature_repo = per_repo[0]["repo"]

    return {
        "mood": aggregate,
        "repos": per_repo,
        "dirty_secs": worst_dirty_secs,
        "conflicts": total_conflicts,
        "repo": feature_repo,
    }


def reset_state() -> None:
    """Test hook: forget all 'first dirty' timestamps."""
    _dirty_since.clear()


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(scan(), indent=2))
