#!/usr/bin/env python3
"""Tests for reading a snapshot build's outcome out of git.

    python3 tests/test_snapshot_status.py

Offline and self-contained: the "remote" is a temporary git repository with a `build-log` branch in it,
which is exactly what the build job pushes. That makes this a real test of the channel rather than of a
mock: if the tool can read the outcome from a repository, it can read it from GitHub.

The point of the tool is that a 1-hour build whose log needs a token to open is a wasted hour, so the
test checks the parts that a person needs in the first ten seconds: did it build, which version, what
was skipped, and the last few real lines of the engine's log.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import snapshot_status  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


REPORT = {
    "status_file": "failure",
    "job_status": "failure",
    "at": "2026-10-14T09:55:00Z",
    "run": "AmphibianDev/webmc-port/actions/runs/123",
    "version": "26.4-snapshot-4",
    "java": 25,
    "classFileVersion": 69,
    "protocolVersion": 779,
    "worldVersion": 5037,
    "profile": "port/versions/26.4-snapshot-4.json",
    "overrides": {"copied_from": "26.3", "files": 6,
                  "mentions_old_version": ["port/overrides/.../Font.java"]},
    "bringup": {"path": "port/tools/bringup/26.4-snapshot-4.txt", "entries": 2, "seeded_from": "26.3.txt"},
    "patchers": {"will_run": "Minecraft263BrowserPatcher", "profile": "26.4-snapshot-4"},
    "review": ["port/overrides/.../Font.java", "Minecraft263BrowserPatcher: a version-gated patcher ..."],
    "interesting": ["BRINGUP_SKIP RenderPatches263.menuBlur", "error: patch anchor not found"],
    "log_bytes": 900000,
}

LOG = "\n".join([
    "Minecraft263BrowserPatcher: profile 26.4-snapshot-4, 412 classes, renderpearl",
    "\x1b[31mRenderPatches263.menuBlur\x1b[0m: BRINGUP_SKIP (list declares it)",
    "InputPatches263.apply: anchor not found in net/minecraft/client/KeyboardHandler",
    "error: patch anchor not found",
    "gradle: BUILD FAILED",
])


def make_remote(tmp: pathlib.Path) -> pathlib.Path:
    """A repository whose `build-log` branch is what the build job pushes."""
    remote = tmp / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    work = tmp / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    branch = work / "build-log"
    branch.mkdir()
    (branch / "report.json").write_text(json.dumps(REPORT, indent=1) + "\n")
    (branch / "status.txt").write_text("failure\n")
    (branch / "last.log").write_text(LOG)
    (work / "README").write_text("logs\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "log"], check=True)
    subprocess.run(["git", "-C", str(work), "branch", "-M", "build-log"], check=True)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(remote)], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "build-log"], check=True)
    return remote


def run_tool(args: list[str]) -> tuple[int, str]:
    import io
    from contextlib import redirect_stderr, redirect_stdout
    buffer = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(buffer):
        code = snapshot_status.main(args)
    return code, buffer.getvalue()


def test_fetch(tmp: pathlib.Path) -> None:
    print("\nreading the last attempt")
    remote = make_remote(tmp)
    code, output = run_tool(["--remote", str(remote)])
    check("it exits zero", code == 0, str(code))
    check("it says whether the build worked", "failure" in output, output[:120])
    check("and which version it was about",
          "26.4-snapshot-4" in output and "Java 25" in output and "779" in output, output[:400])
    check("it names the patcher that ran, and which profile declared it",
          "Minecraft263BrowserPatcher" in output, output[:400])
    check("it says how many overrides came from where",
          "copied from 26.3: 6 file(s)" in output, output[:400])
    check("and how many still name the old version", "1 of them still name it" in output)
    check("it shows the interesting lines of the log",
          "BRINGUP_SKIP" in output and "anchor not found" in output, output[-400:])
    check("with the log size, so a truncated log is obvious", "log:" in output)

    code, output = run_tool(["--remote", str(remote), "--log", "3"])
    check("--log prints the tail of the engine's own log",
          "BUILD FAILED" in output and "anchor not found" in output, output[-300:])
    check("and strips terminal colour codes", "\x1b[" not in output)

    code, output = run_tool(["--remote", str(remote), "--json"])
    check("--json is the raw report, for anything that wants to parse it",
          json.loads(output)["version"] == "26.4-snapshot-4", output[:120])


def test_missing_branch(tmp: pathlib.Path) -> None:
    print("\nwhen there is nothing to read")
    empty = tmp / "empty.git"
    subprocess.run(["git", "init", "--bare", "-q", str(empty)], check=True)
    code, output = run_tool(["--remote", str(empty)])
    check("a repository with no build-log branch is not a crash", code == 2, str(code))
    check("and the message says what that means", "no snapshot build has finished" in output, output[:200])


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        tmp = pathlib.Path(scratch)
        test_fetch(tmp)
        test_missing_branch(tmp)
    print(f"\n{FAILURES} status test(s) FAILED" if FAILURES else "\nstatus tests passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
