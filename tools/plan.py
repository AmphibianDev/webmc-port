#!/usr/bin/env python3
"""Decide what happens to a version: build it, reuse a release, or queue it for a port.

    python3 tools/plan.py --kinds release,snapshot --limit 6
    python3 tools/plan.py --version 26.2
    python3 tools/plan.py --json

A verdict is one of:

  build         an engine has a profile for exactly this version: it can be compiled
  reuse         an engine publishes a prebuilt asset for it (classic versions, CheerpJ)
  needs-port    a real version with no profile: somebody has to write the browser platform
                layer for it. That is the honest bottleneck of this whole idea, and the point
                of the report is to name it instead of failing halfway through a 50-minute
                build.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import versions as version_tools  # noqa: E402


def load_engines(path: pathlib.Path | None = None, include_disabled: bool = False) -> list[dict]:
    """The engines that may be used. A disabled one is documentation, not a candidate."""
    document = json.loads((path or (ROOT / "ports.json")).read_text())
    engines = list(document.get("engines") or [])
    return engines if include_disabled else [e for e in engines if e.get("enabled", True)]


def engine_links(engines: list[dict]) -> dict:
    """Where each prebuilt client can be fetched from, without this repository hosting it."""
    links = {}
    for engine in engines:
        release = engine.get("release") or {}
        asset = release.get("asset")
        if not asset:
            continue
        for version_id, slug in (engine.get("versions") or {}).items():
            repo = (engine.get("repo") or "").replace("{slug}", slug)
            tag = release.get("tag", "latest")
            base = (f"https://github.com/{repo}/releases/latest/download"
                    if tag == "latest" else f"https://github.com/{repo}/releases/download/{tag}")
            links[version_id] = f"{base}/{asset}"
    return links


def verdict_for(version_id: str, kind: str, engines: list[dict]) -> dict:
    """Which engine handles this version, and how."""
    for engine in engines:
        if engine.get("launch") and version_id in (engine.get("profiles") or []):
            requires = engine.get("requires") or {}
            return {
                "verdict": "launch",
                "engine": engine["id"],
                "engine_name": engine.get("name", engine["id"]),
                "why": f"{engine['id']} runs this version unmodified in the browser (real JVM, official jar)",
                "java": requires.get("java"),
                "minutes": requires.get("minutes"),
                "mods": engine.get("mods"),
                "launch": engine.get("launch_plan"),
                "build": None,
            }
        if version_id in (engine.get("profiles") or []):
            requires = engine.get("requires") or {}
            return {
                "verdict": "build",
                "engine": engine["id"],
                "engine_name": engine.get("name", engine["id"]),
                "why": f"{engine['id']} has a profile for {version_id}",
                "java": requires.get("java_compile") or requires.get("java"),
                "ram_gb": requires.get("ram_gb"),
                "minutes": requires.get("minutes"),
                "mods": engine.get("mods"),
                "build": engine.get("build"),
            }
        if version_id in (engine.get("versions") or {}):
            return {
                "verdict": "reuse",
                "engine": engine["id"],
                "engine_name": engine.get("name", engine["id"]),
                "why": f"{engine['id']} publishes a client for {version_id}",
                "release": engine.get("release"),
                "repo": (engine.get("repo") or "").replace(
                    "{slug}", (engine.get("versions") or {}).get(version_id, version_id)),
            }
    if kind in ("release", "snapshot"):
        return {
            "verdict": "needs-port",
            "engine": None,
            "engine_name": None,
            "why": "no engine has a browser platform layer for this version yet",
        }
    return {
        "verdict": "needs-port",
        "engine": None,
        "engine_name": None,
        "why": f"{kind} versions need a classic port (different assets and code layout)",
    }


def plan(manifest: dict, engines: list[dict], kinds=("release",),
         limit: int | None = None, version_id: str | None = None) -> list[dict]:
    if version_id:
        chosen = [v for v in version_tools.versions(manifest) if v.get("id") == version_id]
        if not chosen:
            raise SystemExit(f"{version_id} is not in the manifest")
    else:
        chosen = version_tools.newest(manifest, tuple(kinds), limit)
    rows = []
    for entry in chosen:
        detail = verdict_for(entry["id"], version_tools.kind_of(entry), engines)
        rows.append({"id": entry["id"], "type": version_tools.kind_of(entry),
                     "released": entry.get("releaseTime", ""), **detail})
    return rows


def report(rows: list[dict]) -> str:
    icon = {"build": "🔨", "reuse": "📦", "needs-port": "🧱", "launch": "▶"}
    lines = ["| version | kind | verdict | engine | why |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        lines.append(f"| **{row['id']}** | {row['type']} | {icon[row['verdict']]} {row['verdict']} |"
                     f" {row['engine'] or '—'} | {row['why']} |")
    counts = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    lines += ["", " · ".join(f"{icon[key]} {key}: {value}" for key, value in sorted(counts.items()))]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="What to do with each Minecraft version")
    parser.add_argument("--manifest", default=version_tools.MANIFEST_URL)
    parser.add_argument("--ports", default=str(ROOT / "ports.json"))
    parser.add_argument("--kinds", default="release")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--version", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest = version_tools.load_manifest(args.manifest)
    engines = load_engines(pathlib.Path(args.ports))
    rows = plan(manifest, engines, tuple(args.kinds.split(",")), args.limit, args.version)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
