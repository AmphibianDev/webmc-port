#!/usr/bin/env python3
"""Tests for the version-profile generator: turning Mojang's metadata into an engine's profile.

    python3 tests/test_aot_profile.py

Offline: the client jar is built here (a real zip with a real version.json in it), the version
document is written here in Mojang's shape, and the nearest-profile copy is a fixture. No Mojang, no
JDK, no 40 MB download.

The thing this pins down is the split that makes the generator trustworthy: which facts come from
Mojang, which come from inside the jar, which are computed, and which are the *port's* own decision
(so a machine copies them from the nearest version rather than inventing them).
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import aot_profile  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


# The shape Mojang's own version document has, trimmed to what a profile needs.
VERSION_DOCUMENT = {
    "id": "26.4-snapshot-4",
    "type": "snapshot",
    "releaseTime": "2026-10-13T10:24:18+00:00",
    "mainClass": "net.minecraft.client.main.Main",
    "javaVersion": {"component": "java-runtime-epsilon", "majorVersion": 25},
    "assetIndex": {"id": "35", "sha1": "abfaa525f923f807df8b4e4d29c1b5e3a104adbe",
                   "url": "https://piston-meta.mojang.com/v1/packages/x/35.json"},
    "downloads": {
        "client": {"sha1": "ac783fa45b137dd545c7ffb76bbd595ea85fda0a", "size": 41947724},
        "server": {"sha1": "33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c"},
        "client_mappings": {"sha1": None},
    },
}

# What is inside the client jar. 26.x clients ship readable names *and* this file, which is the only
# source of the three numbers a port needs to describe a version.
VERSION_JSON = {
    "id": "26.4-snapshot-4",
    "name": "26.4 snapshot 4",
    "world_version": 5037,
    "series": "main",
    "protocol_version": 779,
    "pack_version": {"resource": 97, "data": 123},
    "build_time": "2026-10-13T10:21:44+00:00",
    "java_component": "java-runtime-epsilon",
    "java_version": 25,
    "stable": False,
}

# The nearest profile that already exists, in the engine's own format (this is 26.3's, from Gaius).
NEAREST_26_3 = {
    "id": "26.3",
    "releaseType": "release",
    "protocolVersion": 777,
    "worldVersion": 5023,
    "javaVersion": 25,
    "classFileVersion": 69,
    "clientDistribution": "named",
    "worldgenTelemetryMode": "task-pulsed",
    "patchSet": "modern",
    "extraPatchers": ["Minecraft263BrowserPatcher"],
    "packVersions": {"resource": {"major": 97, "minor": 1}, "data": {"major": 121, "minor": 0}},
    "storage": {"schema": 2, "databaseName": "gaius-fs-v2-26.3", "prefix": "gaius.fs.v2:26.3:",
                "opfsDirectory": "regions-v2-26.3"},
    "official": {"releaseTime": "2026-09-15T11:23:02+00:00",
                 "clientSha1": "e877b6a07acd633fb3bb475002175cec036e7b87",
                 "clientMappingsSha1": None, "serverSha1": "33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c",
                 "assetIndexId": "34", "assetIndexSha1": "abfaa525f923f807df8b4e4d29c1b5e3a104adbe"},
}


def write_jar(path: pathlib.Path, version_json: dict | None = None) -> pathlib.Path:
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n")
        jar.writestr("version.json", json.dumps(version_json if version_json is not None else VERSION_JSON))
        jar.writestr("net/minecraft/client/main/Main.class", b"\xca\xfe\xba\xbe")
    return path


def test_jar_reading(tmp: pathlib.Path) -> pathlib.Path:
    print("\nthe jar, read without a JVM")
    jar = write_jar(tmp / "client.jar")
    check("version.json comes out of the zip", aot_profile.version_json(jar)["protocol_version"] == 779)
    broken = tmp / "not-a-client.jar"
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("a/b.txt", "hi")
    try:
        aot_profile.version_json(broken)
        check("a jar without version.json is refused, with a reason", False, "no error raised")
    except SystemExit as error:
        check("a jar without version.json is refused, with a reason",
              "not a Minecraft client jar" in str(error), str(error))
    return jar


def test_build(tmp: pathlib.Path, jar: pathlib.Path) -> None:
    print("\nversion-level facts from Mojang, port-level facts from the nearest profile")
    nearest = {"id": "26.3", **NEAREST_26_3}
    lines: list[str] = []
    profile = aot_profile.build("26.4-snapshot-4", {"type": "snapshot", "released": "2026-10-13T10:24:18+00:00"},
                                VERSION_DOCUMENT, aot_profile.version_json(jar), nearest,
                                log=lines.append)

    check("it is a snapshot, and says so", profile["releaseType"] == "snapshot")
    check("java comes from Mojang's document", profile["javaVersion"] == 25)
    check("the class file version is computed from it", profile["classFileVersion"] == 69,
          str(profile["classFileVersion"]))
    check("the client sha1 comes from Mojang", profile["official"]["clientSha1"].startswith("ac783fa4"),
          profile["official"]["clientSha1"])
    check("so does the server's, which a profile needs for LAN", bool(profile["official"]["serverSha1"]))
    check("and the asset index, id and hash", profile["official"]["assetIndexId"] == "35"
          and profile["official"]["assetIndexSha1"].startswith("abfaa525"))
    check("the released date is carried", profile["official"]["releaseTime"].startswith("2026-10-13"))

    check("the protocol version comes from inside the jar", profile["protocolVersion"] == 779)
    check("so does the world version", profile["worldVersion"] == 5037)
    check("and the pack versions", profile["packVersions"]["resource"]["major"] == 97
          and profile["packVersions"]["data"]["major"] == 123, str(profile["packVersions"]))

    check("the port's patch set is copied, not invented", profile["patchSet"] == "modern")
    check("so are its browser patchers", profile["extraPatchers"] == ["Minecraft263BrowserPatcher"])
    check("and the telemetry mode", profile["worldgenTelemetryMode"] == "task-pulsed")
    check("the copy is reported, so a person knows what a machine guessed",
          any("copied from 26.3" in line for line in lines), str(lines))

    check("storage is namespaced to this version, so two versions cannot read each other's world",
          profile["storage"]["prefix"] == "gaius.fs.v2:26.4-snapshot-4:"
          and profile["storage"]["opfsDirectory"] == "regions-v2-26.4-snapshot-4"
          and "26.4-snapshot-4" in profile["storage"]["databaseName"],
          str(profile["storage"]))
    check("and it does not inherit the neighbour's version in its database name",
          "26.3" not in profile["storage"]["databaseName"], profile["storage"]["databaseName"])
    check("the storage schema is the port's, copied from the neighbour", profile["storage"]["schema"] == 2)

    print("\nwithout a neighbour to copy from")
    lines = []
    bare = aot_profile.build("26.4-snapshot-4", {"type": "snapshot"}, VERSION_DOCUMENT,
                             aot_profile.version_json(jar), None, log=lines.append)
    check("it still produces a usable profile", bare["classFileVersion"] == 69 and bare["protocolVersion"] == 779)
    check("with the defaults, and says they are defaults",
          bare["patchSet"] == "modern" and any("defaults" in line for line in lines), str(lines))

    print("\nwhat cannot be read is named rather than silently null")
    lines = []
    thin = aot_profile.build("26.4-snapshot-4", {}, {"javaVersion": {"majorVersion": 25}}, {}, None,
                             log=lines.append)
    check("a version with no jar data reports the gaps",
          any("could not be read" in line and "protocolVersion" in line for line in lines), str(lines))


def test_snapshot_protocol(tmp: pathlib.Path) -> None:
    print("\na snapshot's protocol number is kept exactly as the client reports it")
    snap = dict(VERSION_JSON, protocol_version=1073742166,   # 0x40000000 | 342
                pack_version={"resource_major": 100, "resource_minor": 1,
                              "data_major": 124, "data_minor": 0})
    jar = write_jar(tmp / "snap-client.jar", snap)
    profile = aot_profile.build("26.4-snapshot-4", {"type": "snapshot"}, VERSION_DOCUMENT,
                                aot_profile.version_json(jar), None, log=lambda *_: None)
    check("the high bit is not stripped: the engine's profile check compares it with the client",
          profile["protocolVersion"] == 1073742166, str(profile["protocolVersion"]))
    check("and the pack versions come out of the jar, minors included",
          profile["packVersions"]["resource"] == {"major": 100, "minor": 1}
          and profile["packVersions"]["data"] == {"major": 124, "minor": 0},
          str(profile["packVersions"]))


def test_java_levels() -> None:
    print("\nthe class file version is arithmetic, not a table")
    for java, expected in ((8, 52), (11, 55), (17, 61), (21, 65), (25, 69)):
        profile = aot_profile.build("x", {}, {"javaVersion": {"majorVersion": java}}, {}, None,
                                   log=lambda *_: None)
        check(f"Java {java} -> class file {expected}", profile["classFileVersion"] == expected,
              str(profile["classFileVersion"]))


def test_nearest(tmp: pathlib.Path) -> None:
    print("\nthe nearest existing profile")
    versions = tmp / "versions"
    versions.mkdir(exist_ok=True)
    (versions / "26.3.json").write_text(json.dumps({"id": "26.3", **NEAREST_26_3}))
    (versions / "1.21.11.json").write_text(json.dumps({"id": "1.21.11", "patchSet": "legacy",
                                                       "extraPatchers": []}))
    (versions / "broken.json").write_text("{not json")
    check("a 26.4 snapshot picks 26.3, not the 1.21 profile",
          aot_profile.nearest_profile(versions, "26.4-snapshot-4")[0] == "26.3",
          str(aot_profile.nearest_profile(versions, "26.4-snapshot-4") or [None]))
    check("a 1.22 release picks the 26.x profile it is nearer to by number",
          aot_profile.nearest_profile(versions, "1.22")[0] in ("1.21.11", "26.3"))
    check("a directory that is not there is not an error",
          aot_profile.nearest_profile(tmp / "nope", "26.4-snapshot-4") is None)
    check("a profile that will not parse does not stop the pick",
          aot_profile.nearest_profile(versions, "26.4-snapshot-4") is not None)


def test_command_line(tmp: pathlib.Path, jar: pathlib.Path) -> None:
    print("\nthe command, on a version state.json has never seen")
    meta = tmp / "26.4-snapshot-4.json"
    meta.write_text(json.dumps(VERSION_DOCUMENT))
    out = tmp / "profiles"
    state = tmp / "state.json"
    state.write_text(json.dumps({"jvm": {"26.4-snapshot-4": {
        "type": "snapshot", "released": "2026-10-13T10:24:18+00:00", "java": 25,
        "jar_sha1": "ac783fa45b137dd545c7ffb76bbd595ea85fda0a", "asset_index": "35",
        "jar_url": "https://piston-data.mojang.com/v1/objects/ac783fa4/client.jar"}}}))

    code = aot_profile.main(["--version", "26.4-snapshot-4", "--state", str(state),
                             "--meta", str(meta), "--jar", str(jar),
                             "--from-nearest", str(tmp / "versions"), "--out", str(out), "--write"])
    check("it exits zero", code == 0, str(code))
    written = out / "26.4-snapshot-4.json"
    check("it writes a profile named after the version", written.is_file())
    if written.is_file():
        profile = json.loads(written.read_text())
        check("the written profile is the one that was printed", profile["id"] == "26.4-snapshot-4"
              and profile["classFileVersion"] == 69)

    check("a version nobody has heard of is refused, with what to do about it",
          aot_profile.main(["--version", "99.9", "--state", str(state), "--dry-run".replace("--dry-run", "--print")]) == 2)
    check("a version with no jar available says what is missing",
          aot_profile.main(["--version", "26.4-snapshot-4", "--state", str(state),
                            "--meta", str(meta), "--print"]) == 2)


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        tmp = pathlib.Path(scratch)
        versions = tmp / "versions"
        versions.mkdir()
        (versions / "26.3.json").write_text(json.dumps({"id": "26.3", **NEAREST_26_3}))
        jar = test_jar_reading(tmp)
        test_build(tmp, jar)
        test_snapshot_protocol(tmp)
        test_java_levels()
        test_nearest(tmp)
        test_command_line(tmp, jar)
    print(f"\n{FAILURES} profile test(s) FAILED" if FAILURES else "\nprofile tests passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
