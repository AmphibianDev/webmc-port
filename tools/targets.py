#!/usr/bin/env python3
"""Print a verdict for every version this project is supposed to cover.

    python3 tools/targets.py                          # against the fixture (offline)
    python3 tools/targets.py --manifest https://...    # against Mojang's real list
    python3 tools/targets.py --json

Three verdicts, and the difference between them is the whole plan:

  real-java   a JVM in the page runs the official client unmodified (Java level and LWJGL
              version both work out)
  one-layer   real Java is reachable, but one JavaScript layer is missing (LWJGL 3, which the
              versions from 1.13 on use)
  blocked     impossible today: the version needs a Java the browser runtime does not have
              (1.20.5 and later want 21, 26.x wants 25)

For a blocked version the answer is not "give up", it is "not as real Java": the ahead-of-time
route (a TeaVM workspace that compiles the official client) handles those, which is a different
kind of work and a different kind of fidelity.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import plan as planner  # noqa: E402
import versions as version_tools  # noqa: E402

# What the browser JVM can run, and what LWJGL each era uses. Both are version facts, not
# preferences: CheerpJ runs Java 8, 11 and 17; Minecraft moved to LWJGL 3 in 1.13.
RUNTIME_JAVA = (8, 11, 17)


def real_java_verdict(version_id: str, row: dict) -> tuple[str, str]:
    """('real-java' | 'one-layer' | 'blocked', why)"""
    if row["verdict"] == "launch":
        java = row.get("java") or 8
        return "real-java", f"Java {java} + LWJGL 2, both available in the browser"
    blocked = row.get("blocked_by")
    if blocked:
        if "Java 21" in blocked or "Java 25" in blocked or "Java 2" in blocked:
            return "blocked", blocked
        return "one-layer", blocked
    if row["verdict"] == "build":
        return "ahead-of-time", f"{row['engine']} compiles it (real client, ahead-of-time)"
    if row["verdict"] == "reuse":
        return "prebuilt", f"{row['engine']} publishes a client for it"
    return "unknown", row.get("why", "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verdicts for the project's target versions")
    parser.add_argument("--manifest", default=str(ROOT / "fixtures" / "manifest.json"))
    parser.add_argument("--targets", default=str(ROOT / "targets.json"))
    parser.add_argument("--ports", default=str(ROOT / "ports.json"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest = version_tools.load_manifest(args.manifest)
    engines = planner.load_engines(pathlib.Path(args.ports))
    roadmap = planner.load_roadmap(pathlib.Path(args.ports))
    targets = json.loads(pathlib.Path(args.targets).read_text())
    known = {v["id"] for v in version_tools.versions(manifest)}

    rows = []
    for group in targets["groups"]:
        for version_id in group["versions"]:
            if version_id not in known:
                rows.append({"group": group["name"], "id": version_id, "state": "not-in-manifest",
                             "why": "not in the version list used for this run"})
                continue
            entry = next(v for v in version_tools.versions(manifest) if v["id"] == version_id)
            detail = planner.verdict_for(version_id, version_tools.kind_of(entry), engines, roadmap)
            state, why = real_java_verdict(version_id, detail)
            row = {"group": group["name"], "id": version_id, "type": version_tools.kind_of(entry),
                   "state": state, "why": why, "verdict": detail["verdict"],
                   "engine": detail.get("engine"), "java": detail.get("java")}
            # A version is never a dead end: when the JVM route cannot have it, the
            # ahead-of-time route can, as soon as a workspace for that version exists. Say so
            # here rather than leaving a bare "blocked".
            if state in ("blocked", "one-layer", "unknown"):
                blocked = planner.roadmap_for(version_id, roadmap) or {}
                row["alternatives"] = blocked.get("alternatives") or [
                    "ahead-of-time: a TeaVM workspace compiles the official client for this version, with mods "
                    "baked in (no runtime jars)"]
            rows.append(row)

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    icon = {"real-java": "🟢 real Java", "ahead-of-time": "🔨 ahead-of-time",
            "prebuilt": "📦 prebuilt", "one-layer": "🟡 one layer away",
            "blocked": "⛔ blocked", "not-in-manifest": "❔ unknown", "unknown": "❔"}
    group = None
    for row in rows:
        if row["group"] != group:
            group = row["group"]
            print(f"\n{group}")
        line = f"  {row['id']:<18} {icon.get(row['state'], row['state']):<18} {row['why']}"
        print(line)
        for alternative in row.get("alternatives", []):
            print(f"  {'':<18} {'':<18} ↳ {alternative}")

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    print()
    print(" · ".join(f"{icon.get(k, k)}: {v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
