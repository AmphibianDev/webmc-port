#!/usr/bin/env python3
"""A version profile for a modern client, generated from Mojang's own metadata.

    python3 tools/aot_profile.py --version 26.4-snapshot-4 --print
    python3 tools/aot_profile.py --version 26.4-snapshot-4 --jar work/client.jar \\
        --meta work/26.4-snapshot-4.json --from-nearest port/versions --write port/versions

Why this exists: the engines that compile a Java 25 client for the web (this project's
`eagler-workspace` route, and the `gaius` route) each need a small per-version profile, and hand
writing one per release is why "it only supports three versions" is the state of things. Almost
everything in such a profile is a fact Mojang already publishes, or a fact that is inside the client
jar itself:

    from Mojang's version document   javaVersion, releaseTime, client sha1, server sha1,
                                     assetIndex id + sha1, the client jar url
    from version.json inside the jar  protocol_version, world_version, pack_version{resource,data}
    computed                          classFileVersion = 44 + javaVersion  (52=Java 8, 65=21, 69=25)

What is *not* derivable is what the port itself decided: which patch set to apply, which browser
patchers to run, how the browser's storage is namespaced. Those are copied from the profile of the
nearest version that already has one - and printed as `copied`, so a person reviews exactly the part
that a machine cannot know rather than re-deriving the whole file.

The client jar is needed only for the three numbers in version.json. Downloading 40 MB to read one
file is honest but wasteful, so `--fetch` caches it in work/ and a caller that already has the jar (a
build job that just downloaded the client) passes --jar and pays nothing.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import sys
import urllib.request
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

# Fields the *port* owns rather than the version: they come from the nearest existing profile.
PORT_FIELDS = ("patchSet", "extraPatchers", "worldgenTelemetryMode", "clientDistribution")
# 44 + java version: Java 8 is 52, 11 is 55, 17 is 61, 21 is 65, 25 is 69.
CLASS_FILE_BASE = 44


def load_state(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def fetch_json(url: str, timeout: int = 60) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+aot-profile)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bytes(url: str, timeout: int = 600) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+aot-profile)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def version_json(jar: pathlib.Path) -> dict:
    """version.json, out of the client jar - the only place these three numbers exist.

    The client jar is a zip and this reads one entry out of it, so no Java, no launch and no
    decompiler is involved: a Python zipfile read of a few hundred bytes in a 40 MB archive.
    """
    with zipfile.ZipFile(jar) as archive:
        names = archive.namelist()
        for candidate in ("version.json", "/version.json"):
            if candidate in names:
                return json.loads(archive.read(candidate).decode("utf-8"))
    raise SystemExit(f"{jar} has no version.json: it is not a Minecraft client jar "
                     f"(a jar this old is also not a client the profile system can describe)")


def nearest_profile(versions_dir: pathlib.Path, version_id: str) -> tuple[str, dict] | None:
    """The existing profile closest to this version, which is where the port's own fields come from.

    Closeness is the version number, not the date: 26.4-snapshot-1 is nearer 26.3 than 1.21.11 is,
    and that is the profile whose patch set and browser patchers a new snapshot should start from.
    """
    sys.path.insert(0, str(HERE))
    import launchplan  # noqa: PLC0415 - only needed for its version ordering

    if not versions_dir.is_dir():
        return None
    candidates = []
    for path in sorted(versions_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if document.get("id"):
            candidates.append((document["id"], document))
    if not candidates:
        return None
    wanted = launchplan.version_key(version_id)
    return min(candidates, key=lambda pair: (abs(launchplan.version_key(pair[0])[0][0] - wanted[0][0]),
                                             abs(launchplan.version_key(pair[0])[0][1] - wanted[0][1])))


def build(version_id: str, entry: dict, document: dict, jar_version: dict, nearest: dict | None,
          log=print) -> dict:
    """The profile: version-level facts from Mojang, port-level facts from the nearest profile."""
    java = ((document.get("javaVersion") or {}).get("majorVersion")
            or entry.get("java") or 25)
    downloads = document.get("downloads") or {}
    asset_index = document.get("assetIndex") or {}
    pack = jar_version.get("pack_version") or {}
    if isinstance(pack, dict) and "resource" not in pack:
        # 1.21 and newer call them resource_major/data_major inside pack_version
        pack = {"resource": pack.get("resource_major", pack.get("resource")),
                "data": pack.get("data_major", pack.get("data"))}
    copied = {field: (nearest or {}).get(field) for field in PORT_FIELDS}
    copied["extraPatchers"] = copied.get("extraPatchers") or []
    copied["patchSet"] = copied.get("patchSet") or "modern"
    copied["worldgenTelemetryMode"] = copied.get("worldgenTelemetryMode") or "task-pulsed"
    copied["clientDistribution"] = copied.get("clientDistribution") or "named"
    storage = dict((nearest or {}).get("storage") or {})
    storage.update({
        # A version's world data must never be read by another version's client: the storage
        # namespace carries the version id, exactly like the existing profiles do it.
        "schema": storage.get("schema", 2),
        "databaseName": f"{storage.get('databaseName', 'gaius-fs-v2').split('-')[0]}-fs-v2-{version_id}",
        "prefix": f"gaius.fs.v2:{version_id}:",
        "opfsDirectory": f"regions-v2-{version_id}",
    })
    if nearest:
        storage["databaseName"] = storage["databaseName"].replace(
            f"-{nearest.get('id', '')}", "")
    profile = {
        "id": version_id,
        "releaseType": entry.get("type") or document.get("type") or "release",
        "protocolVersion": jar_version.get("protocol_version"),
        "worldVersion": jar_version.get("world_version"),
        "javaVersion": java,
        "classFileVersion": CLASS_FILE_BASE + int(java),
        **{field: copied[field] for field in PORT_FIELDS},
        "packVersions": {"resource": {"major": pack.get("resource"), "minor": 1},
                         "data": {"major": pack.get("data"), "minor": 0}},
        "storage": storage,
        "official": {
            "releaseTime": entry.get("released") or document.get("releaseTime"),
            "clientSha1": (downloads.get("client") or {}).get("sha1") or entry.get("jar_sha1"),
            "clientMappingsSha1": (downloads.get("client_mappings") or {}).get("sha1"),
            "serverSha1": (downloads.get("server") or {}).get("sha1"),
            "assetIndexId": asset_index.get("id") or entry.get("asset_index"),
            "assetIndexSha1": asset_index.get("sha1"),
        },
    }
    missing = [key for key, value in {
        "protocolVersion": profile["protocolVersion"],
        "worldVersion": profile["worldVersion"],
        "packVersions.resource": profile["packVersions"]["resource"]["major"],
        "packVersions.data": profile["packVersions"]["data"]["major"],
        "official.clientSha1": profile["official"]["clientSha1"],
    }.items() if value in (None, "")]
    if missing:
        log(f"  ! {version_id}: these could not be read and have to be filled in: {', '.join(missing)}")
    if nearest:
        log(f"  port fields copied from {nearest.get('id')}: "
            + ", ".join(f"{field}={copied[field]!r}" for field in PORT_FIELDS))
    else:
        log("  ! no existing profile to copy the port's own fields from: they are the defaults "
            "(patchSet=modern, no extra patchers). Point --from-nearest at the engine's versions/ "
            "directory once one exists.")
    return profile


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="the Minecraft version to describe")
    parser.add_argument("--state", default=str(ROOT / "state.json"),
                        help="state.json, for the version's java level, sha1 and jar url")
    parser.add_argument("--meta", default=None, help="the version document (json), if you have it")
    parser.add_argument("--fetch-meta", action="store_true",
                        help="fetch the version document from Mojang (piston-meta)")
    parser.add_argument("--jar", default=None, help="the client jar, for version.json")
    parser.add_argument("--fetch", action="store_true", help="download the client jar to work/")
    parser.add_argument("--from-nearest", default=None,
                        help="a versions/ directory to copy the port's own fields from")
    parser.add_argument("--out", default=None, help="a directory to write the profile into")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)

    state = load_state(pathlib.Path(args.state))
    entry = (state.get("jvm") or {}).get(args.version)
    if not entry and not args.meta:
        print(f"state.json does not know {args.version}: pass --meta (its version document) or run "
              f"tools/jvm_route.py for it first", file=sys.stderr)
        return 2

    document = {}
    if args.meta:
        document = json.loads(pathlib.Path(args.meta).read_text())
    elif args.fetch_meta and entry.get("meta_url"):
        document = fetch_json(entry["meta_url"])
    elif entry.get("meta_url"):
        # the catalog already read this document once; reading it again is one small request and it is
        # what makes the profile authoritative rather than remembered
        try:
            document = fetch_json(entry["meta_url"])
        except Exception as error:  # noqa: BLE001 - the cached fields can stand in for most of it
            print(f"  ! could not re-read the version document ({error}); using what state.json has",
                  file=sys.stderr)
    if entry and not document:
        entry = dict(entry)
    if entry and document:
        entry = dict(entry, **{"type": entry.get("type") or document.get("type"),
                               "released": entry.get("released") or document.get("releaseTime")})

    jar = pathlib.Path(args.jar) if args.jar else None
    if not jar and args.fetch:
        url = entry.get("jar_url")
        if not url:
            print(f"state.json has no client url for {args.version}", file=sys.stderr)
            return 2
        work = ROOT / "work"
        work.mkdir(parents=True, exist_ok=True)
        jar = work / f"client-{args.version}.jar"
        if jar.is_file() and (entry.get("jar_sha1") or "") in (
                hashlib.sha1(jar.read_bytes()).hexdigest(), ""):
            print(f"  using the client already in work/ ({jar.stat().st_size / 1048576:.0f} MB)")
        else:
            print(f"  downloading the client from Mojang ({url.split('/')[-1]})…")
            data = fetch_bytes(url)
            digest = hashlib.sha1(data).hexdigest()
            if entry.get("jar_sha1") and digest != entry["jar_sha1"]:
                print(f"Mojang's jar does not match the sha1 in state.json ({digest})", file=sys.stderr)
                return 2
            jar.write_bytes(data)
            print(f"  cached {jar.relative_to(ROOT)} ({len(data) / 1048576:.0f} MB, sha1 verified)")
    if not jar or not jar.is_file():
        print("a client jar is needed for protocol_version, world_version and pack_version: "
              "pass --jar, or --fetch to download it", file=sys.stderr)
        return 2

    nearest = nearest_profile(pathlib.Path(args.from_nearest), args.version) if args.from_nearest else None
    entry = entry or {}
    if not entry.get("type"):
        entry["type"] = document.get("type") or "snapshot"
    # nearest_profile returns (id, document) because the id is what the report names; build() wants
    # the document, which carries its own id.
    profile = build(args.version, entry, document, version_json(jar), nearest[1] if nearest else None)

    print(f"\n{args.version}: Java {profile['javaVersion']} (class file {profile['classFileVersion']}), "
          f"protocol {profile['protocolVersion']}, world {profile['worldVersion']}, "
          f"packs r{profile['packVersions']['resource']['major']} "
          f"d{profile['packVersions']['data']['major']}")
    if args.print:
        print(json.dumps(profile, indent=1))
    if args.write:
        out = pathlib.Path(args.out or ROOT / "work" / "aot-profiles")
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"{args.version}.json"
        target.write_text(json.dumps(profile, indent=1) + "\n")
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
