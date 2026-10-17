#!/usr/bin/env python3
"""Refresh the cached engine files that tools/aot_fixups.json patches.

Our fixups are search-and-replace operations on somebody else's scripts. Whether they still match is decidable
without a build, but only if the files are on disk - so this copies the handful of engine files that any fixup
touches, and writes down where they came from. `tests/test_fixup_anchors.py` then runs on a laptop, in a second,
and says whether a build would have applied its patches or silently skipped them.

Run it when a build fails on a fixup, or when the daily job says the cache is old:

    python3 tools/refresh_engine_files.py                     # from a clone next to this repository
    python3 tools/refresh_engine_files.py --engine /tmp/gaius # or from anywhere on disk
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "tools" / "engine_files"
FIXUPS = ROOT / "tools" / "aot_fixups.json"


def engine_commit(engine: pathlib.Path) -> str:
    """The commit the engine is on, so a cached file can name the revision it came from."""
    try:
        result = subprocess.run(["git", "-C", str(engine), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=30)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def repo_of(engine: pathlib.Path) -> str:
    try:
        result = subprocess.run(["git", "-C", str(engine), "config", "--get", "remote.origin.url"],
                                capture_output=True, text=True, timeout=30)
        remote = result.stdout.strip() if result.returncode == 0 else ""
        return remote.removesuffix(".git").replace("git@github.com:", "https://github.com/")
    except (OSError, subprocess.SubprocessError):
        return ""


def refresh(engine: pathlib.Path, log=print) -> dict:
    fixups = json.loads(FIXUPS.read_text()).get("fixups") or []
    wanted = sorted({entry["file"] for entry in fixups})
    files = {}
    for relative in wanted:
        source = engine / relative
        if not source.is_file():
            log(f"  ! {relative} is not in {engine}: the fixup for it cannot be checked")
            continue
        target = CACHE / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        files[relative] = {"bytes": source.stat().st_size}
        log(f"  {relative} ({source.stat().st_size} bytes)")

    manifest = {
        "note": ("Cached engine files, copied so tools/aot_fixups.json can be checked without a build. They are "
                 "MIT-licensed (see CREDITS.md): the files are here to prove our patches still match them, not to "
                 "build anything from this repository."),
        "repo": repo_of(engine),
        "commit": engine_commit(engine),
        "fetched": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }
    (CACHE / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default=str(ROOT.parent / "engine"),
                        help="the engine checkout the files are copied from")
    args = parser.parse_args(argv)
    engine = pathlib.Path(args.engine)
    if not engine.is_dir():
        print(f"no engine checkout at {engine}: pass --engine", file=sys.stderr)
        return 2
    manifest = refresh(engine)
    print(f"cached {len(manifest['files'])} file(s) from {manifest['repo']} at {manifest['commit'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
