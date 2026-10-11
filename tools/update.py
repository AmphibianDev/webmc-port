#!/usr/bin/env python3
"""The updater: watch Mojang's version list and say what changed, then act on it.

    python3 tools/update.py --state work/state.json --write      # record what we have seen
    python3 tools/update.py --state work/state.json --kinds release --build   # build new ones
    python3 tools/update.py --manifest fixtures/manifest.json --state work/state.json --dry-run

What it does with a new version is the whole design, and it is deliberately conservative:

  * a version an engine has a profile for        -> build it (or print the build plan with --dry-run)
  * a version an engine publishes prebuilt       -> add it to the catalog as a link
  * anything else (fresh snapshot, old version)  -> queue it in needs-port.json, with the reason

Nothing is downloaded from Mojang by this script beyond the version list itself: the client
jar is acquired per build, into work/, by acquire.py, and never committed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import plan as planner  # noqa: E402
import versions as version_tools  # noqa: E402


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_state(path: pathlib.Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            pass
    return {"seen": [], "catalog": [], "queued": [], "updated": None}


def write_json(path: pathlib.Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")


def queue_needs_port(queued: list[dict], rows: list[dict]) -> list[dict]:
    known = {entry["id"] for entry in queued}
    for row in rows:
        if row["verdict"] == "needs-port" and row["id"] not in known:
            queued.append({"id": row["id"], "type": row["type"], "released": row["released"],
                           "why": row["why"], "queued": now()})
    return queued


def catalog_entry(row: dict, engines: list[dict], links: dict) -> dict:
    """One catalog row: what the launcher page needs to offer this version."""
    entry = {"id": row["id"], "type": row["type"], "released": row["released"],
             "verdict": row["verdict"], "engine": row["engine"],
             "engine_name": row.get("engine_name"), "minutes": row.get("minutes"),
             "mods": row.get("mods")}
    if row["verdict"] == "reuse":
        entry["url"] = links.get(row["id"])
        entry["kind"] = "prebuilt"
    elif row["verdict"] == "build":
        entry["url"] = f"clients/{row['id']}.html"
        entry["kind"] = "built"
    else:
        entry["url"] = None
        entry["kind"] = "queued"
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch Minecraft's versions and act")
    parser.add_argument("--manifest", default=version_tools.MANIFEST_URL)
    parser.add_argument("--ports", default=str(ROOT / "ports.json"))
    parser.add_argument("--state", default=str(ROOT / "state.json"))
    parser.add_argument("--kinds", default="release,snapshot")
    parser.add_argument("--limit", type=int, default=0, help="only look at this many new versions")
    parser.add_argument("--write", action="store_true", help="record what has been seen")
    parser.add_argument("--build", action="store_true", help="run the build for new versions")
    parser.add_argument("--dry-run", action="store_true", help="print the build commands instead")
    args = parser.parse_args()

    state_path = pathlib.Path(args.state)
    state = read_state(state_path)
    manifest = version_tools.load_manifest(args.manifest)
    engines = planner.load_engines(pathlib.Path(args.ports))

    fresh = version_tools.changed_since(manifest, set(state["seen"]), tuple(args.kinds.split(",")),
                                        args.limit or None)
    print(f"manifest: {len(version_tools.versions(manifest))} versions, {len(fresh)} new for {args.kinds}")

    rows = planner.plan(manifest, engines, tuple(args.kinds.split(",")), None, None) if not fresh else [
        {"id": v["id"], "type": version_tools.kind_of(v), "released": v.get("releaseTime", ""),
         **planner.verdict_for(v["id"], version_tools.kind_of(v), engines)}
        for v in fresh
    ]

    if not rows:
        print("nothing new")
    else:
        print(planner.report(rows))

    links = planner.engine_links(engines)
    catalog = {entry["id"]: entry for entry in state.get("catalog", [])}
    for row in rows:
        catalog[row["id"]] = catalog_entry(row, engines, links)
    state["catalog"] = sorted(catalog.values(), key=lambda e: e["released"], reverse=True)
    state["queued"] = queue_needs_port(list(state.get("queued", [])), rows)
    state["seen"] = sorted(set(state["seen"]) | {v["id"] for v in version_tools.versions(manifest)})
    state["updated"] = now()

    buildable = [row for row in rows if row["verdict"] == "build"]
    for row in buildable:
        commands = row.get("build") or {}
        print(f"\n== build {row['id']} with {row['engine']}")
        for step in ("clone", "acquire", "remap", "compile"):
            command = commands.get(step)
            if command:
                print(f"   {step}: {command.format(version=row['id'], repo=row['engine'])}")
        if args.build and not args.dry_run:
            print("   (a real build needs the engine checked out in work/engine; see README.md)")

    if args.write:
        write_json(state_path, state)
        write_json(ROOT / "site" / "manifest.json", {
            "note": "Generated by tools/update.py. The launcher page reads this.",
            "updated": state["updated"],
            "versions": state["catalog"],
            "queued": state["queued"],
        })
        print(f"\nwrote {state_path} and site/manifest.json")
    else:
        print("\n(--write to record this)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
