#!/usr/bin/env python3
"""Which engine builds a version, and how that engine is built. One answer, for the build job.

    python3 tools/engine_for.py --version 26.2                 # id, repo, kind, one per line
    python3 tools/engine_for.py --version 26.2 --json          # the whole answer
    python3 tools/engine_for.py --version 26.2 --field repo    # one field, for a shell script

The build job used to hardcode its engine for a tag-triggered run, which meant a dispatch of 26.2 built it with
whatever engine that line named rather than with the engine the route says builds it - and the two families are
not interchangeable: a profile engine is told which version to fetch and patch, a workspace *is* the client source
and is built with its own commands. The wrong one does not fail cleanly, it fails somewhere in the middle, which is
a wasted hour and a misleading log.

So the route is the single source of truth, and this is the seam that reads it: routes.json when it is there (the
deploy writes it), and `tools/route.py` when it is not (a fresh clone).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import plan as planner  # noqa: E402
import route as router  # noqa: E402


def answer(version: str) -> dict:
    """The engine for one version: its id, its repository, its kind, and why it is not buildable when it is not.

    The state is built the way tools/route.py builds it - the catalog's memory plus the launcher's list, which
    carries each version's type and release date. Routing a version with no metadata answers "unknown", and a
    build job that acts on "unknown" builds the wrong thing, so the missing pieces are read here rather than
    assumed.
    """
    # Enabled engines only: a disabled engine is a record, not a builder, and the build job must not be handed
    # one that would fail halfway through. (The route tools can be asked the other question with
    # `include_disabled=True`; this tool exists for the build job.)
    engines = planner.load_engines(include_disabled=False)
    state = _state()
    listed = ROOT / "site" / "manifest.json"
    if listed.is_file():
        doc = json.loads(listed.read_text())
        state["manifest"] = {entry["id"]: {"type": entry.get("type"), "released": entry.get("released")}
                             for entry in doc.get("versions") or []}
    row = router.route_for(version, engines, state)
    engine_id = row.get("engine")
    entry = next((engine for engine in engines if engine.get("id") == engine_id), None)
    return {
        "version": version,
        "engine": engine_id,
        "repo": (entry or {}).get("repo"),
        "kind": (entry or {}).get("kind") or "unknown",
        "enabled": bool((entry or {}).get("enabled")),
        "licence": (entry or {}).get("license"),
        "route": row.get("route"),
        "playable": bool(row.get("playable")),
        "why": row.get("why"),
    }


def _state() -> dict:
    """The state the route tools read: the catalog's memory, which the repository carries."""
    path = ROOT / "state.json"
    state = json.loads(path.read_text()) if path.is_file() else {}
    state.setdefault("jvm", {})
    state.setdefault("manifest", {})
    state.setdefault("built", {})
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--field", default=None, choices=("engine", "repo", "kind", "enabled", "licence", "route"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    found = answer(args.version)
    if args.json:
        print(json.dumps(found, indent=1))
    elif args.field:
        value = found.get(args.field)
        if value in (None, ""):
            print("", end="")
            return 1 if args.field == "repo" else 0
        print("true" if value is True else "false" if value is False else value)
    else:
        for key in ("version", "route", "engine", "repo", "kind"):
            print(f"{key}: {found.get(key) or '—'}")
    # A version with no engine is not an error of this tool: it is the honest answer, and the build job has to
    # decide what to do about it rather than being handed a default that builds the wrong thing.
    return 0 if found.get("engine") else 1


if __name__ == "__main__":
    sys.exit(main())
