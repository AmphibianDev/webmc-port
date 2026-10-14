#!/usr/bin/env python3
"""One version in, one route out: how it runs, who can build it, and whether mods fit.

    python3 tools/route.py --version 1.16.5
    python3 tools/route.py --version 1.21.11 --json
    python3 tools/route.py --span 1.0 1.21.11            # coverage across a range

The launcher and the build job both need the same answer to "what happens if somebody picks this
version", and it is not one answer:

    real-java   the official jar runs in a JVM in the page (CheerpJ, Java 8/17, LWJGL 2). No compile,
                and Fabric/Legacy Fabric jars load at play time. This is 1.0 - 1.12.2.
    needs-shim  the same, but the client wants LWJGL 3, which only our JavaScript shim provides - and
                that shim has not drawn a frame yet. 1.13 - 1.20.4.
    aot         no browser JVM runs this client's Java (21 or newer), so it is compiled ahead of time by
                an engine. Mods can only be applied during that compile. 1.20.5 and later.

The engine named for an `aot` version is the one whose profiles (or generatable profile range) cover it,
and `bakes_mods` says whether that engine can take a mod set at all: the engines with source hooks can,
the ones that only patch a compiled client cannot.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import jvm_route  # noqa: E402
import plan as planner  # noqa: E402
import versions as version_tools  # noqa: E402
import launchplan  # noqa: E402


def facts(version_id: str, state: dict | None = None) -> dict:
    """What the version's own metadata said, from the cache when it has been read.

    The classifier only spends a request on versions whose document it needs, so the modern ones are
    known to the *catalogue* (type and release date) without their Java level having been read: that is
    the difference between "this needs a compile, by date" and "this needs a compile, by its metadata",
    and the route says which one it is standing on.
    """
    state = state or {}
    known = (state.get("jvm") or {}).get(version_id) or {}
    if known:
        return {"java": known.get("java"), "lwjgl": known.get("lwjgl"),
                "type": known.get("type"), "released": known.get("released"),
                "source": known.get("source") or "document",
                "verdict": known.get("route"), "why": known.get("why")}
    listed = (state.get("manifest") or {}).get(version_id) or {}
    return {"java": None, "lwjgl": None, "type": listed.get("type"),
            "released": listed.get("released"), "source": "catalogue" if listed else "unknown",
            "verdict": None, "why": None}


def route_for(version_id: str, engines: list[dict], state: dict | None = None) -> dict:
    """The route, the engine, and whether mods can be baked — for one version."""
    info = facts(version_id, state)
    lwjgl = str(info.get("lwjgl") or "")
    java = info.get("java")

    if java is not None and int(java) > 17:
        route, why = "aot", f"needs Java {java}; no browser JVM runs that, so it is compiled ahead of time"
    elif lwjgl.startswith("3"):
        route, why = "needs-shim", ("needs LWJGL 3, which only this project's JavaScript shim provides, "
                                    "and the shim has not drawn a frame yet")
    elif lwjgl.startswith("2"):
        route, why = "real-java", f"LWJGL {lwjgl} with Java {java or 8}: the official jar runs in the page"
    else:
        # no document read yet: an era verdict is a guess by date, and it is labelled as one
        era = jvm_route.era_verdict({"type": info.get("type") or "release",
                                     "releaseTime": info.get("released") or ""})
        route = {"cheerpj": "real-java", "cheerpj-old": "real-java", "needs-lwjgl3": "needs-shim",
                 "needs-jvm": "aot"}.get(era["route"], "unknown")
        why = era["why"] + " (guessed from the release date; its own metadata has not been read)"
        if route == "unknown" and str(info.get("released") or "") < "2013-06-01" and info.get("released"):
            # 1.0 - 1.2.5 bundle LWJGL 2 inside the jar instead of declaring it, so no document mentions
            # it and the classifier can only say "unknown". The CheerpJ team's own browsercraft demo runs
            # 1.2.5 unmodified, so the honest answer is "real Java, and it is an attempt".
            route = "real-java"
            why = ("LWJGL is not in this version's metadata (it shipped inside the jar), but this era runs "
                   "in the page: browsercraft runs 1.2.5 unmodified the same way. An attempt, not a promise.")

    engine = None
    if route == "real-java":
        engine = next((e for e in engines if e.get("launch") and version_id in (e.get("profiles") or [])),
                      next((e for e in engines if e.get("launch")), None))
    elif route == "aot":
        for candidate in engines:
            if version_id in (candidate.get("profiles") or []):
                engine = candidate
                break
            rule = planner.profile_rule_for(version_id, candidate.get("profile_rules") or [])
            if rule and not planner.engine_blocker_for(version_id, candidate):
                engine = candidate
                break

    built = ((state or {}).get("built") or {}).get(version_id)
    return {
        "version": version_id,
        "route": route,
        "why": why,
        "java": java,
        "lwjgl": lwjgl or None,
        "metadata": info.get("source"),
        "engine": (engine or {}).get("id"),
        "engine_license": (engine or {}).get("license"),
        "engine_repo": (engine or {}).get("repo"),
        "bakes_mods": bool((engine or {}).get("bakes_mods")),
        "mods": ("at play time (real JVM)" if route == "real-java"
                 else ("baked at build time" if (engine or {}).get("bakes_mods") and route == "aot"
                       else "only when an engine with source hooks can build it")),
        "built": {"url": built.get("url"), "bytes": built.get("bytes")} if built else None,
        "playable": bool(built) or route == "real-java",
    }


def span(first: str, last: str, engines: list[dict], state: dict | None = None) -> dict:
    """How a whole range divides up, which is the honest answer to "which versions do you support"."""
    low, high = launchplan.version_key(first)[0], launchplan.version_key(last)[0]
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    # every version the catalogue knows, not only those whose document has been read: the modern ones
    # route by date, and a coverage answer that skipped them would be the wrong answer
    known = sorted(set(((state or {}).get("jvm") or {})) | set(((state or {}).get("manifest") or {})),
                   key=launchplan.version_key)
    for version_id in known:
        key = launchplan.version_key(version_id)[0]
        if not (low <= key <= high):
            continue
        verdict = route_for(version_id, engines, state)
        counts[verdict["route"]] = counts.get(verdict["route"], 0) + 1
        examples.setdefault(verdict["route"], [])
        if len(examples[verdict["route"]]) < 4:
            examples[verdict["route"]].append(version_id)
    return {"from": first, "to": last, "counts": counts, "examples": examples,
            "versions_read": len(known)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="one version to route")
    parser.add_argument("--span", nargs=2, metavar=("FROM", "TO"), help="a range to summarise")
    parser.add_argument("--state", default=str(ROOT / "state.json"))
    parser.add_argument("--manifest", default=str(ROOT / "site" / "manifest.json"),
                        help="the launcher's list, for a version's type and release date")
    parser.add_argument("--ports", default=str(ROOT / "ports.json"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--export", default=None,
                        help="write every version's route to this file, for the launcher to read")
    args = parser.parse_args(argv)

    state = json.loads(pathlib.Path(args.state).read_text()) if pathlib.Path(args.state).is_file() else {}
    if pathlib.Path(args.manifest).is_file():
        listed = json.loads(pathlib.Path(args.manifest).read_text())
        state["manifest"] = {entry["id"]: {"type": entry.get("type"), "released": entry.get("released")}
                             for entry in listed.get("versions") or []}
    engines = planner.load_engines(pathlib.Path(args.ports), include_disabled=False)

    if args.version:
        verdict = route_for(args.version, engines, state)
        if args.json:
            print(json.dumps(verdict, indent=1))
            return 0
        print(f"{verdict['version']}: {verdict['route']}")
        print(f"  why      : {verdict['why']}")
        print(f"  metadata : {verdict['metadata']}"
              + (f" (Java {verdict['java']}, LWJGL {verdict['lwjgl']})" if verdict['java'] else ""))
        print(f"  engine   : {verdict['engine'] or 'none'}"
              + (f" [{verdict['engine_license'] or 'no licence'}]" if verdict['engine'] else ""))
        print(f"  mods     : {verdict['mods']}")
        print(f"  playable : {'yes' if verdict['playable'] else 'not yet'}"
              + (f" (built: {verdict['built']['url']})" if verdict['built'] else ""))
        return 0

    if args.export:
        listed = json.loads(pathlib.Path(args.manifest).read_text()) if pathlib.Path(args.manifest).is_file() else {}
        known = sorted(set((state.get("jvm") or {})) | set((state.get("manifest") or {}))
                       | {entry["id"] for entry in listed.get("versions") or []},
                       key=launchplan.version_key)
        routes = {version_id: route_for(version_id, engines, state) for version_id in known}
        out = pathlib.Path(args.export)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "note": ("Generated by tools/route.py: how each version runs, which engine builds it, and "
                     "whether that engine can take a mod set. The launcher reads this next to the catalog."),
            "versions": routes,
        }, indent=1, sort_keys=True) + "\n")
        counts: dict[str, int] = {}
        for verdict in routes.values():
            counts[verdict["route"]] = counts.get(verdict["route"], 0) + 1
        print(f"wrote {out} with {len(routes)} routes: "
              + ", ".join(f"{name} {count}" for name, count in sorted(counts.items())))
        return 0

    if args.span:
        summary = span(args.span[0], args.span[1], engines, state)
        if args.json:
            print(json.dumps(summary, indent=1))
            return 0
        print(f"{summary['from']} to {summary['to']}, from {summary['versions_read']} versions whose own "
              f"metadata has been read:")
        for route, count in sorted(summary["counts"].items()):
            print(f"  {route:12s} {count:4d}   e.g. {', '.join(summary['examples'][route])}")
        return 0

    parser.error("give --version or --span")


if __name__ == "__main__":
    sys.exit(main())
