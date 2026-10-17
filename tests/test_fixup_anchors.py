#!/usr/bin/env python3
"""Every fixup in tools/aot_fixups.json, checked against the engine files it will be applied to.

A fixup is a search-and-replace on somebody else's script. When their script changes, the search text stops
matching, and the failure mode is the worst kind: the build keeps going and comes out with a patch that was never
applied - which is how a build can be green and wrong at the same time.

The engine's files are cached in `tools/engine_files/` for exactly this check (they are MIT, and
tools/refresh_engine_files.py replaces them with the current ones). This test proves three things per fixup:
the anchor is really there, the replacement is what the fixup says, and the result still looks like the file it
claims to produce - so a drifted anchor is a red test on a laptop instead of a wasted hour on a runner.

The cache is allowed to be old; it is not allowed to be *unnoticed*. A daily job refreshes it, and the age is part
of the report this test prints.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "tools" / "engine_files"
FIXUPS = ROOT / "tools" / "aot_fixups.json"

FAILURES = 0
CHECKS = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES, CHECKS
    CHECKS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


def main() -> int:
    print("the fixups against the engine files they search")
    doc = json.loads(FIXUPS.read_text())
    fixups = doc.get("fixups") or []
    check("there is a fixup file with entries", bool(fixups), str(FIXUPS))

    manifest_path = CACHE / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    check("the engine files are cached, with their provenance written down",
          bool(manifest.get("files")) and bool(manifest.get("repo")) and bool(manifest.get("commit")),
          str(manifest)[:200])
    print(f"  (cached from {manifest.get('repo', '?')} at {manifest.get('commit', '?')[:12]}, "
          f"{manifest.get('fetched', '?')})")

    for entry in fixups:
        name = entry["file"].split("/")[-1]
        cached = CACHE / entry["file"]
        if not cached.is_file():
            check(f"{name}: its file is in the cache", False, f"missing {cached}")
            continue
        text = cached.read_text(errors="replace")
        occurrences = text.count(entry["find"])
        check(f"{name}: the anchor is in the file exactly once",
              occurrences == 1, f"found {occurrences} time(s); the engine changed and the fixup is dead")
        if occurrences != 1:
            continue
        patched = text.replace(entry["find"], entry["replace"], 1)
        check(f"{name}: applying it changes the file", patched != text)
        # Two shapes are legitimate: a fixup that *replaces* a block (the anchor is gone afterwards) and one that
        # *extends* it - the chain-hook fixup re-emits the loop it anchors on and appends a guarded block, so its
        # anchor is still there on purpose. The promise in both cases is the same: the file now says what the
        # fixup says, and it said something else before.
        extended = entry["find"] in entry["replace"]
        promise = entry["replace"] in patched and patched != text
        check(f"{name}: the result is what the fixup promises",
              promise and (extended or entry["find"] not in patched),
              f"extended={extended} changed={patched != text}")
        # A fixup must not delete something the engine needs later in the same file: the two things every one of
        # these touches (the patcher runner and the profile check) have to survive.
        for needed in ("run_client_patcher", "$client_patch_classes") if name == "build-overlays.sh" else ():
            check(f"{name}: {needed} is still in the file afterwards", needed in patched)
        check(f"{name}: it says why it exists",
              isinstance(entry.get("why"), str) and len(entry["why"]) > 20, str(entry.get("why"))[:60])

    print(f"\n{CHECKS - FAILURES}/{CHECKS} fixup checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
