#!/usr/bin/env python3
"""Prepare a Minecraft version inside a compiled-client engine, so it can be built.

    python3 tools/aot_snapshot.py --version latest-snapshot --engine work/engine --print
    python3 tools/aot_snapshot.py --version 26.4-snapshot-4 --engine work/engine \\
        --client-jar work/client.jar --meta work/26.4-snapshot-4.json

The engines that compile a modern client for the web (see MODERN-JAVA.md) all need the same three
things for a version nobody has brought up yet:

  1. a **profile** - Java level, protocol, world and pack versions, sha1s (`tools/aot_profile.py`
     generates it from Mojang's document and the version.json inside the client jar);
  2. the **source overrides** the previous version needed - browser replacements for classes the
     client expects to exist (fonts, input, terrain, resources), which are per-version because they
     patch a different source tree;
  3. a **bring-up list** for the patch chain, so a build can start while the patches whose anchors
     moved are still being ported (the engine's own documented process: `GAIUS_BRINGUP=1` plus
     `port/tools/bringup/<version>.txt`).

This tool does all three, in the engine's own layout, and then writes a report saying what it copied,
what a person still has to look at, and which patcher will run for the new version. It does not
pretend the build will work: step 2 and 3 are a starting point that the compiler then argues with, and
the report names which source overrides mention the version they came from, because those are the ones
that usually need a human.

Nothing here downloads a client unless it has to, and nothing writes outside the engine directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import aot_profile  # noqa: E402
import launchplan  # noqa: E402

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def resolve_version(wanted: str, fetch=aot_profile.fetch_json) -> dict:
    """'latest-snapshot' and 'latest-release' mean what Mojang means by them, today."""
    manifest = fetch(MANIFEST_URL)
    if wanted in ("latest-snapshot", "latest-release", "latest"):
        which = "snapshot" if wanted == "latest-snapshot" else "release"
        version_id = manifest["latest"][which]
    else:
        version_id = wanted
    for entry in manifest["versions"]:
        if entry["id"] == version_id:
            return {"id": version_id, "type": entry["type"], "released": entry["releaseTime"],
                    "meta_url": entry["url"]}
    raise SystemExit(f"Mojang's version list has no {version_id}")


def version_of(path: pathlib.Path) -> str:
    """The version a sibling names, whether it is a directory or a file.

    `Path.stem` is wrong for the directories this is used on: it strips the last dot-suffix, so
    `26.4-snapshot-4` becomes `26` and every 26.x directory looks identical - which silently picked a
    newer version's overrides to copy into an older one until a test caught it.
    """
    if path.is_dir():
        return path.name
    return path.name[: -len(path.suffix)] if path.suffix in (".json", ".txt") else path.name


def nearest(directory: pathlib.Path, version_id: str) -> pathlib.Path | None:
    """The closest older sibling by version number.

    Older, not merely close: copying a newer version's overrides into an older one is backwards, and
    26.4-snapshot-1 is nearer 26.3 than 1.21.11 is. If nothing older exists there is no neighbour to
    copy from at all, which is a different and louder situation.
    """
    if not directory.is_dir():
        return None
    candidates = [path for path in directory.iterdir()
                  if path.name != version_id and (path.is_dir() or path.suffix in (".txt", ".json"))]
    if not candidates:
        return None
    wanted = launchplan.version_key(version_id)[0]
    older = [path for path in candidates if launchplan.version_key(version_of(path))[0] <= wanted]
    if not older:
        return None
    return max(older, key=lambda path: launchplan.version_key(version_of(path))[0])


def copy_overrides(engine: pathlib.Path, version_id: str, log=print) -> dict:
    """Copy the nearest version's overrides to this one, and say which files mention the old id."""
    source_root = engine / "port" / "overrides" / "client" / "src" / "versions"
    target_root = source_root / version_id
    report = {"copied_from": None, "files": 0, "mentions_old_version": [], "created": False}
    neighbour = nearest(source_root, version_id)
    if neighbour is None:
        log(f"  ! no existing override directory under {source_root}: the new version starts with "
            f"none, and the build will say which classes need one")
        return report
    report["copied_from"] = neighbour.name
    if target_root.exists():
        log(f"  overrides for {version_id} already exist: left alone")
        return report
    shutil.copytree(neighbour, target_root)
    report["created"] = True
    for path in sorted(target_root.rglob("*")):
        if not path.is_file():
            continue
        report["files"] += 1
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if neighbour.name in text:
            report["mentions_old_version"].append(str(path.relative_to(engine)))
    log(f"  copied {report['files']} override file(s) from {neighbour.name} to {version_id}"
        + (f"; {len(report['mentions_old_version'])} of them still name {neighbour.name}"
           if report["mentions_old_version"] else ""))
    return report


def our_bringup_entries(path: pathlib.Path | None, version_id: str) -> list[dict]:
    """The patches this project declares as not-yet-ported for a version, from our own file.

    The engine's bring-up list is per-version and lives in the engine's tree. Writing our entries into it
    at build time keeps the record in this repository, next to the fixups, with the reason and the run
    that produced it - instead of a line somebody added to a clone and nobody can find again.
    """
    if path is None or not pathlib.Path(path).is_file():
        return []
    document = json.loads(pathlib.Path(path).read_text())
    numbers = launchplan.version_key(version_id)[0]
    wanted = []
    for entry in document.get("entries") or []:
        if entry.get("since") and numbers < launchplan.version_key(str(entry["since"]))[0]:
            continue
        if entry.get("until") and numbers > launchplan.version_key(str(entry["until"]))[0]:
            continue
        spec = entry.get("versions")
        if spec and spec not in ("*", "any") and not launchplan.matches(spec, version_id):
            continue
        wanted.append(entry)
    return wanted


def bringup_list(engine: pathlib.Path, version_id: str, log=print,
                 ours: pathlib.Path | None = None) -> dict:
    """Seed the engine's bring-up list from the nearest one.

    The list is the engine's documented way to build a version whose patches are still being ported:
    `GAIUS_BRINGUP=1` plus `port/tools/bringup/<version>.txt`. Starting from the neighbour's list is
    right for the same reason the overrides are: a patch that was already deferred on 26.3 is very
    likely to be deferred on a 26.4 snapshot too - and the compiler is what proves it either way.
    """
    directory = engine / "port" / "tools" / "bringup"
    declared = our_bringup_entries(ours, version_id)
    report = {"path": None, "seeded_from": None, "entries": 0, "ours": len(declared)}
    if not directory.is_dir():
        log(f"  ! {directory} does not exist: this engine has no bring-up mechanism, so every patch "
            f"must apply for the build to start")
        return report
    target = directory / f"{version_id}.txt"
    report["path"] = str(target.relative_to(engine))
    if target.exists():
        log(f"  bring-up list for {version_id} already exists: left alone")
        return report
    neighbour = nearest(directory, version_id)
    body = []
    if neighbour is not None and neighbour.is_file():
        report["seeded_from"] = neighbour.name
        body = [line for line in neighbour.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    own = [f"{entry['patch']} | {entry.get('owner', 'P0')} | {entry.get('why', 'not ported yet')}"
           for entry in declared]
    header = [
        f"# Minecraft {version_id} bring-up list.",
        "#",
        "# Seeded by tools/aot_snapshot.py from "
        + (f"{report['seeded_from']}." if report["seeded_from"] else "nothing (this is the first list)."),
        "# Format: <patchId> | <owner work package> | <reason>",
        "# Each line is a patch this build is allowed to skip while the version is brought up; every",
        "# skip prints BRINGUP_SKIP <patchId> and the list must shrink to empty before the release",
        "# build, which refuses bring-up mode entirely.",
    ]
    if body:
        header += ["#", "# Carried over from the neighbour - review every one: a patch that was deferred",
                   "# on that version may already apply here, and skipping it silently is worse than a",
                   "# failed build."]
    if own:
        header += ["#", "# Declared by this repository (tools/aot_bringup.json), each one a patch that was",
                   "# tried and did not apply to this version. The reason says which build said so."]
    if not body and not own:
        header += ["#", "# Empty: every patch has to apply. Add a line only with a reason."]
    target.write_text("\n".join(header + body + own) + "\n")
    report["entries"] = len(body) + len(own)
    log(f"  wrote {report['path']} with {len(body)} carried-over and {len(own)} declared entr(y/ies)")
    return report


def apply_fixups(engine: pathlib.Path, version_id: str, path: pathlib.Path, log=print) -> dict:
    """Patch the engine's own source for a version it has not ported yet.

    A snapshot changes the client's API, and the engine's browser code is written against the last
    version it built: 26.4-snapshot-4 made `AbstractWidget.visible` protected, so the LWJGL overlay that
    reads it stopped compiling. That is not a bug in either project - it is what a snapshot *is* - so it
    is handled as data: `tools/aot_fixups.json` lists a find/replace per breakage, with the version
    range it applies to and the reason, and this applies them.

    Three rules, because a silent patch set is worse than no patch set:

      * applying twice is a no-op (a build may be re-run in the same checkout);
      * an anchor that is no longer there is a **failure**, not a skip: the file changed, so the fixup
        needs a person, and continuing would produce a build that fails later for a worse reason;
      * every change is reported with its reason, so the build log says what was patched and why.
    """
    report = {"file": None, "applied": [], "already": [], "failed": []}
    if path is None:
        return report
    document = json.loads(pathlib.Path(path).read_text())
    fixups = document.get("fixups") or []
    report["file"] = str(pathlib.Path(path).name)
    wanted = launchplan.version_key(version_id)
    for fixup in fixups:
        # Scope is `since`/`until` by version *number*, not the mod-style matcher: a snapshot sorts
        # below its own release (so `>=26.4` would exclude 26.4-snapshot-4, which is exactly the
        # version a fixup for 26.4 is written for). The numbers alone are what "26.4 and later" means.
        numbers = launchplan.version_key(version_id)[0]
        if fixup.get("since") and numbers < launchplan.version_key(str(fixup["since"]))[0]:
            continue
        if fixup.get("until") and numbers > launchplan.version_key(str(fixup["until"]))[0]:
            continue
        spec = fixup.get("versions")
        if spec and spec not in ("*", "any") and not launchplan.matches(spec, version_id):
            continue
        relative = fixup.get("file")
        target = engine / relative if relative else None
        if not target or not target.is_file():
            report["failed"].append(f"{relative}: no such file in the engine")
            continue
        text = target.read_text()
        find, replace = fixup.get("find", ""), fixup.get("replace", "")
        if replace and replace in text and find not in text:
            report["already"].append(relative)
            log(f"  fixup already applied: {relative}")
            continue
        count = text.count(find)
        if count == 0:
            report["failed"].append(
                f"{relative}: the text this fixup anchors on is gone. The engine's source changed - "
                f"review the fixup ({fixup.get('why')}) instead of building without it")
            log(f"  ! fixup anchor missing in {relative}")
            continue
        expected = int(fixup.get("count", 1))
        if count != expected:
            report["failed"].append(
                f"{relative}: the anchor matches {count} times, the fixup says {expected}")
            log(f"  ! fixup anchor matches {count} times in {relative}, expected {expected}")
            continue
        target.write_text(text.replace(find, replace))
        report["applied"].append({"file": relative, "why": fixup.get("why"), "matches": count})
        log(f"  patched {relative}: {fixup.get('why')}")
    if report["failed"]:
        raise SystemExit("fixups that could not be applied:\n  " + "\n  ".join(report["failed"]))
    return report


def patcher_report(engine: pathlib.Path, version_id: str) -> dict:
    """Which patcher will run for this version: the one the nearest profile declares.

    Not guessed from the class name. The names collapse the dots - `Minecraft263` is 26.3 and
    `Minecraft12111` is 1.21.11, so 12111 is not version 12111 - and the authoritative mapping is the
    profile itself: its `extraPatchers` list is what the engine runs. So the nearest *profile* is what
    this reads, and the report says which version declared the patcher it found.
    """
    versions = engine / "port" / "versions"
    report = {"profile": None, "own_profile": False, "patchers": [], "will_run": None}
    if not versions.is_dir():
        return report
    if (versions / f"{version_id}.json").is_file():
        # after preparation the version has a profile of its own, and that profile is what the engine
        # will read: its extraPatchers are the patcher list, whatever they were copied from
        chosen, own = version_id, True
    else:
        known = [path.stem for path in versions.glob("*.json") if path.stem != version_id]
        wanted = launchplan.version_key(version_id)[0]
        older = [name for name in known if launchplan.version_key(name)[0] <= wanted]
        chosen, own = (max(older, key=lambda name: launchplan.version_key(name)[0]) if older else None), False
    if chosen is None:
        return report
    report["profile"] = chosen
    report["own_profile"] = own
    try:
        document = json.loads((versions / f"{chosen}.json").read_text())
    except (json.JSONDecodeError, OSError):
        return report
    patchers = [str(name) for name in (document.get("extraPatchers") or [])]
    report["patchers"] = patchers
    if patchers:
        report["will_run"] = patchers[0]
    return report


def find_engine_profile(engine: pathlib.Path) -> pathlib.Path:
    directory = engine / "port" / "versions"
    if not directory.is_dir():
        raise SystemExit(f"{engine} does not look like a compiled-client engine: no port/versions")
    return directory


def prepare(version_id: str, engine: pathlib.Path, client_jar: pathlib.Path, document: dict,
            entry: dict, fixups_path: pathlib.Path | None = None,
            bringup_path: pathlib.Path | None = None, log=print) -> dict:
    """Everything the engine needs for one version, written into the engine's own layout."""
    profile = aot_profile.build(version_id, entry, document,
                                aot_profile.version_json(client_jar),
                                aot_profile.nearest_profile(find_engine_profile(engine), version_id)[1]
                                if aot_profile.nearest_profile(find_engine_profile(engine), version_id)
                                else None, log=log)
    target = find_engine_profile(engine) / f"{version_id}.json"
    target.write_text(json.dumps(profile, indent=1) + "\n")
    log(f"  wrote {target.relative_to(engine)} (Java {profile['javaVersion']}, "
        f"class file {profile['classFileVersion']})")

    fixups = apply_fixups(engine, version_id, fixups_path, log=log)
    bringup = bringup_list(engine, version_id, log=log, ours=bringup_path)
    # A snapshot marks its protocol with a high bit; the profile carries the plain number, and saying so
    # here means nobody has to wonder later why 1073742166 became 22.
    raw = aot_profile.version_json(client_jar).get("protocol_version")
    if isinstance(raw, int) and raw & 0x40000000 and raw != profile.get("protocolVersion"):
        log(f"  protocol: snapshot marker stripped, {raw} -> {profile['protocolVersion']}")
    overrides = copy_overrides(engine, version_id, log=log)
    patchers = patcher_report(engine, version_id)
    review = list(overrides["mentions_old_version"])
    if patchers["will_run"]:
        origin = (f"declared by this version's profile, copied from {overrides['copied_from']}"
                  if patchers["own_profile"] and overrides["copied_from"]
                  else f"declared by the profile for {patchers['profile']}")
        log(f"  the patch chain will run {patchers['will_run']} for {version_id} ({origin})")
        if patchers["profile"] != version_id or overrides["copied_from"]:
            review.append(f"{patchers['will_run']}: a version-gated patcher declared by the profile "
                          f"for {patchers['profile']}"
                          + (f" (itself copied from {overrides['copied_from']})"
                             if overrides['copied_from'] else "")
                          + f", running for {version_id}. Its guard accepts anything but 26.2 and "
                            f"1.21.11, and every one of its patches has to agree with this client.")
    else:
        log("  no version-gated patcher was found: the shared patch chain is all that will run")

    return {
        "version": version_id,
        "profile": str(target.relative_to(engine)),
        "protocol_note": (f"snapshot marker stripped: {raw} -> {profile['protocolVersion']}"
                          if isinstance(raw, int) and raw & 0x40000000 else None),
        "java": profile["javaVersion"],
        "classFileVersion": profile["classFileVersion"],
        "protocolVersion": profile["protocolVersion"],
        "worldVersion": profile["worldVersion"],
        "overrides": overrides,
        "fixups": fixups,
        "bringup": bringup,
        "patchers": patchers,        "review": review,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="latest-snapshot",
                        help="a version id, or latest-snapshot / latest-release")
    parser.add_argument("--engine", required=True, help="the engine checkout to prepare")
    parser.add_argument("--client-jar", default=None, help="the client jar, if it is already here")
    parser.add_argument("--meta", default=None, help="the version document, if it is already here")
    parser.add_argument("--fetch", action="store_true",
                        help="download the client jar (verify its sha1) when it is not given")
    parser.add_argument("--fixups", default=None,
                        help="a json file of source fixups for a version the engine has not ported")
    parser.add_argument("--bringup", default=None,
                        help="a json file of bring-up entries (patches not ported yet) to declare")
    parser.add_argument("--out", default=None, help="where to write the report json")
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)

    engine = pathlib.Path(args.engine)
    if not engine.is_dir():
        print(f"no engine at {engine}", file=sys.stderr)
        return 2

    if args.meta and pathlib.Path(args.meta).is_file():
        document = json.loads(pathlib.Path(args.meta).read_text())
        resolved = {"id": document.get("id") or args.version, "type": document.get("type"),
                    "released": document.get("releaseTime"), "meta_url": None}
    else:
        resolved = resolve_version(args.version)
        document = aot_profile.fetch_json(resolved["meta_url"])
    version_id = resolved["id"]
    print(f"{args.version} -> {version_id} ({resolved['type']}, {str(resolved['released'])[:10]})")

    jar = pathlib.Path(args.client_jar) if args.client_jar else None
    if jar and not jar.is_file():
        print(f"no such client jar: {jar}", file=sys.stderr)
        return 2
    if not jar and args.fetch:
        work = ROOT / "work"
        work.mkdir(parents=True, exist_ok=True)
        jar = work / f"client-{version_id}.jar"
        url = ((document.get("downloads") or {}).get("client") or {}).get("url")
        expected = ((document.get("downloads") or {}).get("client") or {}).get("sha1")
        if not url:
            print(f"{version_id}'s document has no client download", file=sys.stderr)
            return 2
        if jar.is_file() and hashlib.sha1(jar.read_bytes()).hexdigest() == expected:
            print(f"  reusing the client already in work/ ({jar.stat().st_size / 1048576:.0f} MB)")
        else:
            print(f"  downloading the client from Mojang…")
            data = aot_profile.fetch_bytes(url)
            digest = hashlib.sha1(data).hexdigest()
            if expected and digest != expected:
                print(f"the download does not match Mojang's sha1 ({digest})", file=sys.stderr)
                return 2
            jar.write_bytes(data)
            print(f"  {jar.relative_to(ROOT)} ({len(data) / 1048576:.0f} MB, sha1 verified)")
    if not jar:
        print("a client jar is needed: pass --client-jar, or --fetch", file=sys.stderr)
        return 2

    entry = {"type": resolved["type"], "released": resolved["released"]}
    report = prepare(version_id, engine, jar, document, entry,
                     pathlib.Path(args.fixups) if args.fixups else None,
                     pathlib.Path(args.bringup) if args.bringup else None)
    report["metaless"] = not bool(resolved.get("meta_url"))

    print()
    print(f"{version_id}: profile written, {report['overrides']['files']} override(s) from "
          f"{report['overrides']['copied_from']}, bring-up list "
          f"{'seeded' if report['bringup']['seeded_from'] else 'created empty'}, "
          f"patcher {report['patchers']['will_run']}")
    if report["review"]:
        print(f"review before trusting the build ({len(report['review'])} item(s)):")
        for item in report["review"][:20]:
            print(f"  - {item}")
    if args.print:
        print(json.dumps(report, indent=1))
    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1) + "\n")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
