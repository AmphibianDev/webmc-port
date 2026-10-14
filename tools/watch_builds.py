#!/usr/bin/env python3
"""Watch the build queue without an account: which builds are running, and what the last one said.

    python3 tools/watch_builds.py                 # the recent build runs, newest first
    python3 tools/watch_builds.py --wait          # poll until nothing is queued or running
    python3 tools/watch_builds.py --wait --timeout 3600

Why this exists: a compiled client is a 45-minute job, so "is it working" is a question about a queue, not
about a program. The GitHub API answers it without a token for a public repository, and the build log
branch answers "why did it fail" - this prints both, so watching a build does not mean clicking through
the web UI. `--wait` is for leaving it running while you do something else.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request

HERE = __file__.rsplit("/", 1)[0]
REPO = "AmphibianDev/webmc-port"
API = f"https://api.github.com/repos/{REPO}"
RUNS = f"{API}/actions/runs?per_page=20"


def fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+watch)",
                                                  "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def build_runs() -> list[dict]:
    runs = fetch(RUNS).get("workflow_runs") or []
    return [run for run in runs if "build" in run["name"].lower() or "snapshot" in run["name"].lower()]


def line(run: dict) -> str:
    state = run.get("conclusion") or run["status"]
    version = run["head_branch"].replace("build-", "") or "?"
    return f"  {version:22s} {state:10s} {run['status']:11s} started {run['created_at'][:16]}  {run['html_url']}"


def queued(runs: list[dict]) -> int:
    return sum(1 for run in runs if run["status"] in ("queued", "in_progress", "requested", "waiting", "pending"))


def report() -> str:
    """The last attempt's own report, from the build-log branch."""
    try:
        result = subprocess.run([sys.executable, f"{HERE}/snapshot_status.py"],
                                capture_output=True, text=True, timeout=120)
        return result.stdout.strip()
    except Exception as error:  # noqa: BLE001 - a watcher must never be the thing that breaks
        return f"(could not read the report: {error})"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", action="store_true", help="poll until nothing is running")
    parser.add_argument("--timeout", type=int, default=7200, help="give up after this many seconds")
    parser.add_argument("--poll", type=int, default=60)
    args = parser.parse_args(argv)

    deadline = time.time() + args.timeout
    while True:
        runs = build_runs()
        print(f"\n{REPO} builds ({time.strftime('%H:%M:%S')})")
        for run in runs[:8]:
            print(line(run))
        busy = queued(runs)
        if not args.wait or busy == 0:
            break
        print(f"  {busy} queued or running; waiting {args.poll}s")
        if time.time() > deadline:
            print("  giving up on waiting (the builds are still going; run this again to look)")
            break
        time.sleep(args.poll)

    print("\nthe last attempt that finished, in its own words")
    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
