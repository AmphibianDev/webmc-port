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

import jvm_route  # noqa: E402
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


def resolved_client_urls(manifest: dict, rows: list[dict], fixture: dict | None = None) -> dict:
    """The official jar url for every 'launch' version: the page fetches that, not us.

    Resolution needs the version's own document, which is one small request per version; a
    fixture (--client-urls) keeps tests offline, and a failure is not fatal: the entry simply
    says the url is resolved at serve time.
    """
    urls = {}
    for row in rows:
        if row["verdict"] != "launch":
            continue
        if fixture and row["id"] in fixture:
            urls[row["id"]] = {**fixture[row["id"]], "from_fixture": True}
            continue
        try:
            import urllib.request
            entry = version_tools.resolve(manifest, row["id"])
            request = urllib.request.Request(entry["url"], headers={"User-Agent": "webmc-port/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                document = json.loads(response.read().decode("utf-8"))
            targets = version_tools.download_targets(document)
            if targets["client_url"]:
                urls[row["id"]] = {"jar_url": targets["client_url"], "main_class": "net.minecraft.client.Minecraft",
                                   "java": targets["java_major"], "meta_url": entry["url"]}
        except Exception as error:  # noqa: BLE001
            print(f"  ! could not resolve the client url for {row['id']}: {error}")
    return urls


def prebuilt_ports(path: pathlib.Path | None = None) -> dict:
    """The builds catalogued in builds.json, as a version -> playable url map.

    These are GitHub addresses, and the page links them: this project copies no client. They exist
    because for those versions nothing else runs in a tab yet (no browser JVM for Java 21+, and no
    JavaScript implementation of LWJGL 3), so a version whose only reachable client is somebody
    else's port should say so and be playable rather than sit in a queue.
    """
    file = path or (ROOT / "builds.json")
    if not file.is_file():
        return {}
    ports = {}
    for group in json.loads(file.read_text()).get("versions", []):
        client = next((item for item in group["files"] if item.get("role") == "client"), None)
        if not client:
            continue
        entry = {
            "url": client["url"],
            "kind": "prebuilt port",
            "source_repo": group.get("source_repo"),
            "label": group.get("label", group["version"]),
            "era": group.get("era"),
            "bytes": client.get("bytes"),
            # "html" when the host serves a page (a GitHub Pages deployment), "download" when it
            # serves raw bytes as text/plain - which looks like a broken game but is a download
            "served_as": client.get("served_as"),
        }
        # the port's own id and any id Mojang uses for the same version
        for version_id in [group["version"]] + list(group.get("aliases") or []):
            ports[version_id] = entry
    return ports


def catalog_entry(row: dict, engines: list[dict], links: dict, client_urls: dict | None = None,
                  jvm: dict | None = None,
                  previous: dict | None = None, strict_urls: bool = False,
                  ports: dict | None = None, built: dict | None = None) -> dict:
    """One catalog row: what the launcher page needs to offer this version."""
    entry = {"id": row["id"], "type": row["type"], "released": row["released"],
             "verdict": row["verdict"], "engine": row["engine"],
             "engine_name": row.get("engine_name"), "minutes": row.get("minutes"),
             "mods": row.get("mods")}
    # What a browser JVM can do with this version, from Mojang's own document (tools/jvm_route.py).
    # This is what makes the launch route cover the whole Java family instead of nine hand-picked
    # versions: any version whose metadata says "LWJGL 2.9 + Java 8" is a launch version too.
    verdict = (jvm or {}).get(row["id"]) or jvm_route.era_verdict(
        {"type": row["type"], "releaseTime": row["released"]})
    if verdict:
        if verdict.get("source") == "era":
            entry["jvm_source"] = "era"
        # Every classified version carries its verdict, playable or not: the launcher shows this
        # sentence instead of inventing one, so "why not" is Mojang's metadata talking.
        entry["jvm_route"] = verdict.get("route")
        entry["jvm_why"] = verdict.get("why")
    if verdict.get("route", "").startswith("cheerpj") and verdict.get("jar_url"):
        entry["verdict"] = "launch"
        entry["jvm_route"] = verdict["route"]
        entry["jvm_why"] = verdict["why"]
        entry["kind"] = "launch"
        entry["jar_url"] = verdict["jar_url"]
        entry["jar_url_source"] = "mojang"
        entry["main_class"] = verdict.get("main_class") or "net.minecraft.client.Minecraft"
        entry["jar_bytes"] = verdict.get("jar_bytes")
        entry["asset_index"] = verdict.get("asset_index")
        entry["meta_url"] = verdict.get("meta_url")
        entry["url"] = None
        port = (ports or {}).get(row["id"])
        if port:
            entry["play_url"] = port["url"]
            entry["play_kind"] = port["kind"]
            entry["play_repo"] = port.get("source_repo")
            entry["play_bytes"] = port.get("bytes")
            entry["play_served_as"] = port.get("served_as")
        return entry

    port = (ports or {}).get(row["id"])
    if port:
        # Playable *today*, linked where the build lives. It is deliberately its own field: a
        # version can be both buildable by us later and playable through somebody's port now, and
        # the launch/build branches must not wipe that.
        entry["play_url"] = port["url"]
        entry["play_kind"] = port["kind"]
        entry["play_repo"] = port.get("source_repo")
        entry["play_bytes"] = port.get("bytes")
        entry["play_served_as"] = port.get("served_as")
    if row["verdict"] == "launch":
        resolved = (client_urls or {}).get(row["id"]) or {}
        # The catalog is a cache: a refresh that could not reach Mojang must keep the url it
        # already knows, not erase it (which is what an offline refresh used to do).
        known = (previous or {}).get("jar_url")
        # A url that came in through --client-urls is test data. CI runs with --strict-urls, so it
        # can never carry one of those forward into a real catalog; a local run keeps them, which is
        # what lets the offline tests check the retention behaviour.
        if known and (previous or {}).get("jar_url_source") == "fixture" and strict_urls:
            print(f"  ! dropping a test url for {row['id']}: {known[:60]}")
            known = None
        entry["kind"] = "launch"
        entry["jar_url"] = resolved.get("jar_url") or known
        if resolved.get("jar_url"):
            entry["jar_url_source"] = "fixture" if resolved.get("from_fixture") else "mojang"
        else:
            # carried forward: say where it came from the first time, so a strict run can still
            # recognise test data after an offline refresh
            entry["jar_url_source"] = (previous or {}).get("jar_url_source") if entry["jar_url"] else None
        entry["main_class"] = resolved.get("main_class") or "net.minecraft.client.Minecraft"
        entry["caveat"] = next((e.get("caveat") for e in engines
                                if e["id"] == row.get("engine")), None)
        entry["url"] = None  # we host nothing for this; the page builds itself from jar_url
        return entry
    if row["verdict"] == "reuse":
        entry["url"] = links.get(row["id"])
        entry["kind"] = "prebuilt"
    elif row["verdict"] == "build":
        # A build this project compiled and published. state.json is the catalogue's memory; the
        # publish step writes there, because the generated manifest is rebuilt from it on every run
        # (which is exactly how the first successful 26.2 build lost its link an hour after making it).
        published = (built or {}).get(row["id"]) or {}
        entry["url"] = published.get("url") or f"clients/{row['id']}.html"
        entry["kind"] = "built"
        if published:
            entry["built_bytes"] = published.get("bytes")
            entry["built_at"] = published.get("at")
            entry["built_sha256"] = published.get("sha256")
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
    parser.add_argument("--builds-file", default=str(ROOT / "builds.json"),
                        help="the prebuilt builds to offer as playable ports")
    parser.add_argument("--strict-urls", action="store_true",
                        help="never keep a url that came from --client-urls (CI uses this)")
    parser.add_argument("--client-urls", default=None,
                        help="json file of version -> {jar_url, main_class} to use instead of resolving from Mojang")
    args = parser.parse_args()

    state_path = pathlib.Path(args.state)
    state = read_state(state_path)
    manifest = version_tools.load_manifest(args.manifest)
    engines = planner.load_engines(pathlib.Path(args.ports))

    fresh = version_tools.changed_since(manifest, set(state["seen"]), tuple(args.kinds.split(",")),
                                        args.limit or None)
    print(f"manifest: {len(version_tools.versions(manifest))} versions, {len(fresh)} new for {args.kinds}")

    roadmap = planner.load_roadmap(pathlib.Path(args.ports))
    rows = planner.plan(manifest, engines, tuple(args.kinds.split(",")), None, None, roadmap) if not fresh else [
        {"id": v["id"], "type": version_tools.kind_of(v), "released": v.get("releaseTime", ""),
         **planner.verdict_for(v["id"], version_tools.kind_of(v), engines, roadmap)}
        for v in fresh
    ]

    if not rows:
        print("nothing new")
    else:
        print(planner.report(rows))

    # Classified in an earlier step of the same run (tools/jvm_route.py); state.json is the cache, so
    # each run classifies a few dozen more versions and the catalog converges.
    jvm = state.get("jvm") or {}
    built = state.get("built") or {}
    links = planner.engine_links(engines)
    fixture_urls = json.loads(pathlib.Path(args.client_urls).read_text()) if args.client_urls else None
    client_urls = resolved_client_urls(manifest, rows, fixture_urls)
    if client_urls:
        print(f"\nresolved {len(client_urls)} official client url(s): the page fetches those from Mojang")
    catalog = {entry["id"]: entry for entry in state.get("catalog", [])}
    for row in rows:
        catalog[row["id"]] = catalog_entry(row, engines, links, client_urls, jvm, catalog.get(row["id"]),
                                           strict_urls=args.strict_urls,
                                           ports=prebuilt_ports(pathlib.Path(args.builds_file)),
                                           built=built)
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
