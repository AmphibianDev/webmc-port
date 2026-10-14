#!/usr/bin/env python3
"""Read what the last snapshot build did, from git alone.

    python3 tools/snapshot_status.py                 # the last attempt, summarised
    python3 tools/snapshot_status.py --log 200       # and the last 200 lines of its build log
    python3 tools/snapshot_status.py --remote origin # a different remote

Why this exists: a snapshot build takes about an hour, and reading it should not require an account, a
token, or a browser. The build job writes `report.json` (what it prepared, what it skipped, the
interesting lines of the log) and `last.log` (the tail) to a `build-log` branch, so one git fetch is
enough to answer "did it build, and if not, at what". This prints exactly that.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
BRANCH = "build-log"


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT)
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def fetch_status(remote: str) -> dict:
    git("fetch", "-q", remote, BRANCH)
    status = git("show", f"FETCH_HEAD:{BRANCH}/status.txt", check=False).strip()
    report = git("show", f"FETCH_HEAD:{BRANCH}/report.json", check=False)
    document = json.loads(report) if report.strip().startswith("{") else {}
    document["status_file"] = status or None
    return document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--log", type=int, default=0, help="also print this many lines of the build log")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = fetch_status(args.remote)
    except SystemExit:
        print(f"nothing on the {BRANCH} branch yet: no snapshot build has finished or failed since it "
              f"was added", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=1))
        return 0

    print(f"last attempt: {report.get('status_file') or report.get('job_status') or 'unknown'}")
    print(f"  at     : {report.get('at')}")
    print(f"  run    : {report.get('run')}")
    if report.get("version"):
        print(f"  version: {report['version']} - Java {report.get('java')} "
              f"(class file {report.get('classFileVersion')}), protocol {report.get('protocolVersion')}, "
              f"world {report.get('worldVersion')}")
    overrides = report.get("overrides") or {}
    if overrides:
        print(f"  profile: {report.get('profile')}")
        print(f"  overrides copied from {overrides.get('copied_from')}: {overrides.get('files')} file(s)"
              f", {len(overrides.get('mentions_old_version') or [])} of them still name it")
    if report.get("patchers"):
        print(f"  patcher: {report['patchers'].get('will_run')} "
              f"(declared by the profile for {report['patchers'].get('profile')})")
    if report.get("bringup"):
        bringup = report["bringup"]
        print(f"  bring-up list: {bringup.get('path')} "
              f"({bringup.get('entries')} entr(y/ies): {bringup.get('ours', 0)} declared by this "
              f"repository, the rest carried over from {bringup.get('seeded_from') or 'nothing'})")
    review = report.get("review") or []
    if review:
        print(f"  review before trusting it ({len(review)}):")
        for item in review[:10]:
            print(f"    - {item}")
    errors = report.get("errors") or []
    skips = report.get("bringup_skips") or []
    if errors:
        print(f"  what went wrong ({len(errors)} line(s), first few):")
        for line in errors[:6]:
            print(f"    {line.strip()[:160]}")
    if skips:
        print(f"  patches skipped by the bring-up list ({len(skips)}):")
        for line in skips[:8]:
            print(f"    {line.strip()[:160]}")
        if len(skips) != len(set(line.strip() for line in skips[:8])):
            print("    (the list is what the build reports; every skip has a reason there)")
    interesting = report.get("interesting") or []
    if interesting and not errors:
        print(f"  from the log ({len(interesting)} interesting line(s)):")
        for line in interesting[-12:]:
            print(f"    {line}")
    log_bytes = report.get("log_bytes")
    if log_bytes:
        print(f"  log: {log_bytes / 1024:.0f} kB captured"
              f"{' (tail too small to be the whole story)' if log_bytes < 2048 else ''}")

    if args.log:
        text = git("show", f"FETCH_HEAD:{BRANCH}/last.log", check=False)
        if not text.strip():
            print(f"\nnothing was written to {BRANCH}/last.log: the job may have failed before any build "
                  f"step ran", file=sys.stderr)
            return 1
        lines = [line for line in text.splitlines() if line.strip()]
        print(f"\n--- last {min(args.log, len(lines))} of the engine's own log ---")
        for line in lines[-args.log:]:
            line = re.sub(r"\x1b\[[0-9;]*m", "", line)
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
