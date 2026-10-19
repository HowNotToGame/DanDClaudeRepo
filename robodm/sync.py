"""Carry campaigns across git branches.

Cloud and phone sessions start from the default branch and `/endsession` pushes to the session's own
branch, so without a merge the next session wouldn't remember. `./dm sync` fixes that per campaign: it fetches
origin, finds the branch whose newest commit touching campaigns/<slug>/ is the most recent, and, if that
state isn't already in HEAD, restores the folder from it (staged, so the next `/endsession` commit carries it).
The SessionStart hook runs it automatically in cloud sessions; on desktop it only reports newer states.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime

from robodm import paths
from robodm.util import RoboDMError


def _git(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="echo", GCM_INTERACTIVE="never")
    return subprocess.run(["git", *args], cwd=str(paths.home()), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)


def in_repo() -> bool:
    try:
        r = _git("rev-parse", "--is-inside-work-tree", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def _newest(ref: str, path: str) -> tuple[int, str, str] | None:
    r = _git("log", "-1", "--format=%ct%x09%H%x09%s", ref, "--", path)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    ts, sha, subject = r.stdout.strip().split("\t", 2)
    return int(ts), sha, subject


def _contains_head(sha: str) -> bool:
    return _git("merge-base", "--is-ancestor", sha, "HEAD").returncode == 0


def check(fetch: bool = True, fetch_timeout: int = 30) -> dict:
    """{'fetched': bool, 'error': str, 'newer': [ {slug, ref, sha, ts, subject} ], 'active': {...} | None}"""
    if not in_repo():
        return {"fetched": False, "error": "not a git checkout", "newer": [], "active": None}
    result = {"fetched": False, "error": "", "newer": [], "active": None}
    if fetch:
        try:
            r = _git("fetch", "origin", "--prune", "--quiet", timeout=fetch_timeout)
            result["fetched"] = r.returncode == 0
            if r.returncode != 0:
                result["error"] = (r.stderr.strip().splitlines() or ["git fetch failed"])[-1]
        except subprocess.TimeoutExpired:
            result["error"] = f"git fetch timed out after {fetch_timeout}s"
    refs = [x for x in _git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin").stdout.split()
            if x not in ("origin", "origin/HEAD")]
    slugs: set[str] = set()
    for ref in ["HEAD", *refs]:
        r = _git("ls-tree", "-d", "--name-only", ref, "campaigns/")
        slugs.update(line.split("/", 1)[1] for line in r.stdout.split() if "/" in line)
    best_overall = None
    for slug in sorted(slugs):
        path = f"campaigns/{slug}"
        here = _newest("HEAD", path)
        best = None
        for ref in refs:
            n = _newest(ref, path)
            if n and (best is None or n[0] > best[0]):
                best = (*n, ref)
        if best is None or _contains_head(best[1]):
            continue  # nothing elsewhere, or HEAD already has it
        # the other branch builds on HEAD's state (newer by definition), or the two diverged and it's more recent
        newer = here is None or _git("merge-base", "--is-ancestor", here[1], best[1]).returncode == 0 or best[0] > here[0]
        if not newer:
            continue
        item = {"slug": slug, "ref": best[3], "sha": best[1], "ts": best[0], "subject": best[2]}
        result["newer"].append(item)
        if best_overall is None or item["ts"] > best_overall["ts"]:
            best_overall = item
    result["active"] = best_overall
    return result


def _when(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def describe(item: dict) -> str:
    return f"{item['slug']} on {item['ref']} ({_when(item['ts'])}: {item['subject']})"


def apply(state: dict | None = None, force: bool = False, fetch: bool = True) -> list[str]:
    """Restore each campaign folder that has a newer state on another branch. Returns report lines."""
    state = state or check(fetch=fetch)
    if state.get("error") == "not a git checkout":
        raise RoboDMError("Sync needs a git checkout of the RoboDM repo.")
    lines = []
    if state.get("error"):
        lines.append(f"⚠️ Couldn't fetch origin ({state['error']}); comparing with the branches already fetched.")
    synced = []
    for item in state["newer"]:
        path = f"campaigns/{item['slug']}"
        dirty = _git("status", "--porcelain", "--", path).stdout.strip()
        if dirty and not force:
            lines.append(f"⏸ Skipped {describe(item)}: {path}/ has uncommitted changes here (commit them, or "
                         "`./dm sync --force` to replace them).")
            continue
        r = _git("restore", f"--source={item['sha']}", "--staged", "--worktree", "--", path)
        if r.returncode != 0:
            lines.append(f"⚠️ Couldn't restore {describe(item)}: {r.stderr.strip()}")
            continue
        synced.append(item)
        lines.append(f"⤵ Synced {describe(item)} — staged; the next `/endsession` commit carries it.")
    top = state.get("active")
    if top and top in synced:
        _git("restore", f"--source={top['sha']}", "--staged", "--worktree", "--", "campaigns/.active")
    if not state["newer"]:
        lines.append("✅ Campaigns are up to date with every branch on origin.")
    return lines
