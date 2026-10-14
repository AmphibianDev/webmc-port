#!/usr/bin/env python3
"""Tests for preparing a brand-new version inside a compiled-client engine.

    python3 tests/test_aot_snapshot.py

Offline: the engine is a directory tree built here in the engine's own layout, the client jar is a real
zip with a real version.json in it, and the Mojang document is written in Mojang's shape. What is being
pinned down is the part of "support the latest snapshot" that a machine can do without a compiler:

  * `latest-snapshot` resolves through Mojang's own `latest` block;
  * the profile lands in the engine's versions directory with the right Java level;
  * the source overrides come from the *nearest* version by number (26.3 for a 26.4 snapshot, not
    1.21.11), and the ones that still name their old version are reported, because those are what a
    person has to look at;
  * the bring-up list is seeded from the neighbour's, with its own header, and never overwrites a list
    somebody has already edited;
  * the version-gated patcher that will run is named, and the fact that it was written for another
    version is on the review list rather than hidden.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import aot_snapshot  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


MANIFEST = {
    "latest": {"release": "26.3", "snapshot": "26.4-snapshot-4"},
    "versions": [
        {"id": "26.4-snapshot-4", "type": "snapshot", "releaseTime": "2026-10-13T10:24:18+00:00",
         "url": "https://piston-meta.mojang.com/v1/packages/x/26.4-snapshot-4.json"},
        {"id": "26.3", "type": "release", "releaseTime": "2026-09-15T11:23:02+00:00",
         "url": "https://piston-meta.mojang.com/v1/packages/y/26.3.json"},
    ],
}

DOCUMENT = {
    "id": "26.4-snapshot-4", "type": "snapshot", "releaseTime": "2026-10-13T10:24:18+00:00",
    "javaVersion": {"component": "java-runtime-epsilon", "majorVersion": 25},
    "assetIndex": {"id": "35", "sha1": "abfaa525f923f807df8b4e4d29c1b5e3a104adbe"},
    "downloads": {"client": {"sha1": "ac783fa45b137dd545c7ffb76bbd595ea85fda0a"},
                  "server": {"sha1": "33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c"}},
}

VERSION_JSON = {"id": "26.4-snapshot-4", "world_version": 5037, "protocol_version": 779,
                "pack_version": {"resource": 97, "data": 123}}
VERSION_JSON_26_3 = {"id": "26.3", "world_version": 5023, "protocol_version": 777,
                     "pack_version": {"resource": 97, "resource_minor": 1,
                                      "data": 121, "data_minor": 0}}

PROFILE_26_3 = {
    "id": "26.3", "releaseType": "release", "protocolVersion": 777, "worldVersion": 5023,
    "javaVersion": 25, "classFileVersion": 69, "clientDistribution": "named",
    "worldgenTelemetryMode": "task-pulsed", "patchSet": "modern",
    "extraPatchers": ["Minecraft263BrowserPatcher"],
    "packVersions": {"resource": {"major": 97, "minor": 1}, "data": {"major": 121, "minor": 0}},
    "storage": {"schema": 2, "databaseName": "gaius-fs-v2-26.3", "prefix": "gaius.fs.v2:26.3:",
                "opfsDirectory": "regions-v2-26.3"},
    "official": {"releaseTime": "2026-09-15T11:23:02+00:00", "clientSha1": "e877b6a0",
                 "clientMappingsSha1": None, "serverSha1": "33680f5f", "assetIndexId": "34",
                 "assetIndexSha1": "abfaa525"},
}


def fake_engine(root: pathlib.Path) -> pathlib.Path:
    """The engine's layout, as far as this tool cares about it."""
    (root / "port" / "versions").mkdir(parents=True)
    (root / "port" / "versions" / "26.3.json").write_text(json.dumps(PROFILE_26_3))
    (root / "port" / "versions" / "1.21.11.json").write_text(
        json.dumps({"id": "1.21.11", "extraPatchers": ["Minecraft12111BrowserPatcher"]}))
    (root / "port" / "versions" / "26.2.json").write_text(
        json.dumps({"id": "26.2", "extraPatchers": ["Minecraft262BrowserPatcher"]}))

    overrides = root / "port" / "overrides" / "client" / "src" / "versions"
    for version, files in (("26.3", {"java/net/minecraft/client/Font.java": "// 26.3 font patch\n",
                                     "java/dev/gaius/browser/BrowserInput.java": "// browser input\n",
                                     "excludes.txt": "# nothing excluded\n"}),
                           ("1.21.11", {"java/net/minecraft/client/Old.java": "// old\n"}),
                           ("26.2", {"java/net/minecraft/client/Old262.java": "// old\n"})):
        directory = overrides / version
        for name, body in files.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
    (overrides / "26.3" / "java" / "net" / "minecraft" / "client" / "Tagged.java").write_text(
        "// only valid for 26.3\n")

    bringup = root / "port" / "tools" / "bringup"
    bringup.mkdir(parents=True)
    (bringup / "26.3.txt").write_text(
        "# 26.3 bring-up list\n#\n# Format: <patchId> | <owner> | <reason>\n"
        "RenderPatches263.menuBlur | P3 | the blur changed shape\n"
        "step:LwjglPlatformGuard@lwjgl | P0 | guard not ported\n")

    tools = root / "port" / "tools" / "src" / "main" / "java" / "dev" / "gaius" / "tools"
    tools.mkdir(parents=True)
    for name in ("Minecraft12111BrowserPatcher", "Minecraft262BrowserPatcher",
                 "Minecraft263BrowserPatcher", "ModernSymbols", "PatchRegistry"):
        (tools / f"{name}.java").write_text(f"// {name}\n")
    return root


def client_jar(path: pathlib.Path, version_json: dict | None = None) -> pathlib.Path:
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("version.json", json.dumps(version_json or VERSION_JSON))
        jar.writestr("net/minecraft/client/Minecraft.class", b"\xca\xfe\xba\xbe")
    return path


def test_resolve() -> None:
    print("\nwhich version is 'the latest snapshot'")
    resolved = aot_snapshot.resolve_version("latest-snapshot", fetch=lambda url: MANIFEST)
    check("it comes from Mojang's own latest block", resolved["id"] == "26.4-snapshot-4", str(resolved))
    check("with its document url and date", resolved["meta_url"].endswith("26.4-snapshot-4.json")
          and resolved["released"].startswith("2026-10-13"))
    release = aot_snapshot.resolve_version("latest-release", fetch=lambda url: MANIFEST)
    check("'latest-release' means the release, not the newest version", release["id"] == "26.3",
          str(release))
    explicit = aot_snapshot.resolve_version("26.3", fetch=lambda url: MANIFEST)
    check("an explicit id is looked up rather than guessed", explicit["id"] == "26.3")
    try:
        aot_snapshot.resolve_version("99.9", fetch=lambda url: MANIFEST)
        check("an unknown id is refused", False, "no error")
    except SystemExit as error:
        check("an unknown id is refused", "no 99.9" in str(error), str(error))


def test_prepare(tmp: pathlib.Path) -> None:
    print("\npreparing the version inside the engine")
    engine = fake_engine(tmp / "engine")
    jar = client_jar(tmp / "client.jar")
    lines: list[str] = []
    report = aot_snapshot.prepare("26.4-snapshot-4", engine, jar, DOCUMENT,
                                  {"type": "snapshot", "released": DOCUMENT["releaseTime"]},
                                  log=lines.append)

    profile_path = engine / "port" / "versions" / "26.4-snapshot-4.json"
    check("the profile is written where the engine looks for it", profile_path.is_file())
    profile = json.loads(profile_path.read_text())
    check("with this version's java level from Mojang", profile["javaVersion"] == 25
          and profile["classFileVersion"] == 69)
    check("and this version's protocol and world versions from inside its jar",
          profile["protocolVersion"] == 779 and profile["worldVersion"] == 5037)
    check("the port's own fields come from 26.3, and the report says so",
          report["overrides"]["copied_from"] == "26.3"
          and any("copied from 26.3" in line for line in lines), str(lines))
    check("storage is namespaced to the new version",
          "26.4-snapshot-4" in profile["storage"]["prefix"]
          and "26.3" not in profile["storage"]["databaseName"], str(profile["storage"]))

    overrides = engine / "port" / "overrides" / "client" / "src" / "versions" / "26.4-snapshot-4"
    check("the source overrides are copied for the new version",
          (overrides / "excludes.txt").is_file()
          and (overrides / "java" / "dev" / "gaius" / "browser" / "BrowserInput.java").is_file()
          and (overrides / "java" / "net" / "minecraft" / "client" / "Tagged.java").is_file(),
          str(sorted(p.name for p in overrides.rglob("*"))[:6]))
    check("the neighbour's own overrides are left where they are",
          (engine / "port" / "overrides" / "client" / "src" / "versions" / "26.3"
           / "java" / "net" / "minecraft" / "client" / "Tagged.java").is_file())
    check("the files that still name the old version are listed as needing review",
          len(report["overrides"]["mentions_old_version"]) == 2
          and set(report["overrides"]["mentions_old_version"]) <= set(report["review"]),
          str(report["overrides"]["mentions_old_version"]))
    check("a file that does not name it is not on the list",
          not any(path.endswith("BrowserInput.java")
                  for path in report["overrides"]["mentions_old_version"]),
          str(report["overrides"]["mentions_old_version"]))

    bringup = engine / "port" / "tools" / "bringup" / "26.4-snapshot-4.txt"
    check("a bring-up list exists for the new version", bringup.is_file())
    text = bringup.read_text()
    check("seeded from 26.3's, carrying the entries but not its comments",
          "RenderPatches263.menuBlur" in text and "step:LwjglPlatformGuard@lwjgl" in text
          and "26.3 bring-up list" not in text)
    check("with a header that says what each line means and that the list must empty",
          "Format: <patchId> | <owner work package> | <reason>" in text
          and "must shrink to empty" in text)
    check("and a warning that the carried-over lines need review",
          "review every one" in text.lower(), text[:400])

    check("the patcher written for 26.3 is the one that will run",
          report["patchers"]["will_run"] == "Minecraft263BrowserPatcher", str(report["patchers"]))
    check("and running a patcher declared by another version is on the review list",
          any("version-gated patcher" in item for item in report["review"]), str(report["review"]))

    print("\nrunning it again, and on other versions")
    before = {path: path.read_text() for path in overrides.rglob("*") if path.is_file()}
    again = aot_snapshot.prepare("26.4-snapshot-4", engine, jar, DOCUMENT,
                                 {"type": "snapshot"}, log=lambda *_: None)
    after = {path: path.read_text() for path in overrides.rglob("*") if path.is_file()}
    check("a second run does not overwrite the overrides", before == after)
    check("and it says it left them alone", again["overrides"]["created"] is False)

    check("a version with its own profile gets that profile's patcher, even though the names are "
          "ambiguous (Minecraft12111 is 1.21.11, not 12111)",
          aot_snapshot.patcher_report(engine, "1.21.11")["will_run"] == "Minecraft12111BrowserPatcher",
          str(aot_snapshot.patcher_report(engine, "1.21.11")))
    check("a version between two profiles takes the nearest older one",
          aot_snapshot.patcher_report(engine, "26.3.5")["profile"] == "26.3",
          str(aot_snapshot.patcher_report(engine, "26.3.5")))
    check("and a version above them all takes the newest profile below it",
          aot_snapshot.patcher_report(engine, "26.5")["profile"] == "26.4-snapshot-4",
          str(aot_snapshot.patcher_report(engine, "26.5")))
    check("never a newer one: a snapshot must not inherit from a profile above it",
          aot_snapshot.patcher_report(engine, "26.2.5")["profile"] in ("26.2", "1.21.11"),
          str(aot_snapshot.patcher_report(engine, "26.2.5")))
    check("a version that already has a profile uses that profile's patcher and says so",
          aot_snapshot.patcher_report(engine, "26.4-snapshot-4")["own_profile"] is True,
          str(aot_snapshot.patcher_report(engine, "26.4-snapshot-4")))

    print("\nthe nearest neighbour, which is the whole point of the copy")
    source = engine / "port" / "overrides" / "client" / "src" / "versions"
    check("a 26.4 snapshot copies 26.3, not 1.21.11",
          aot_snapshot.nearest(source, "26.4-snapshot-4").name == "26.3",
          str(aot_snapshot.nearest(source, "26.4-snapshot-4")))
    check("a 26.2.5 copies 26.2", aot_snapshot.nearest(source, "26.2.5").name == "26.2",
          str(aot_snapshot.nearest(source, "26.2.5")))
    check("a version name survives the trip through the filesystem (26.4-snapshot-4 is not '26')",
          aot_snapshot.version_of(source / "26.4-snapshot-4") == "26.4-snapshot-4"
          and aot_snapshot.version_of(pathlib.Path("26.3.txt")) == "26.3")


def test_fixups(tmp: pathlib.Path) -> None:
    print("\nfixups: patching the engine's own source for a version it has not ported")
    engine = fake_engine(tmp / "engine-fixups")
    broken = engine / "port" / "overrides" / "libraries" / "lwjgl-opengl" / "src" / "main" / "java"
    broken = broken / "org" / "lwjgl" / "opengl" / "BrowserOpenGL.java"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text(
        "class BrowserOpenGL {\n"
        "    void describe(Object widget) {\n"
        "        jsonField(builder, \"visible\", widget.visible).append(',');\n"
        "    }\n}\n")
    fixups = tmp / "fixups.json"
    fixups.write_text(json.dumps({"fixups": [{
        "file": "port/overrides/libraries/lwjgl-opengl/src/main/java/org/lwjgl/opengl/BrowserOpenGL.java",
        "find": 'jsonField(builder, "visible", widget.visible)',
        "replace": 'jsonField(builder, "visible", widget.isVisible())',
        "since": "26.4",
        "why": "26.4 made AbstractWidget.visible protected",
    }]}))
    jar = client_jar(tmp / "fix-client.jar")
    lines: list[str] = []

    report = aot_snapshot.prepare("26.4-snapshot-4", engine, jar, DOCUMENT, {"type": "snapshot"},
                                  fixups, log=lines.append)
    check("the fixup is applied to the engine's file",
          "widget.isVisible()" in broken.read_text() and "widget.visible" not in broken.read_text())
    check("and reported with its reason",
          report["fixups"]["applied"] and "protected" in report["fixups"]["applied"][0]["why"], str(report["fixups"]))
    check("the log says what was patched and why",
          any("patched" in line and "protected" in line for line in lines), str(lines))

    lines = []
    again = aot_snapshot.prepare("26.4-snapshot-4", engine, jar, DOCUMENT, {"type": "snapshot"},
                                 fixups, log=lines.append)
    check("applying it twice is a no-op, not a second edit",
          again["fixups"]["applied"] == [] and again["fixups"]["already"], str(again["fixups"]))
    check("and the file is unchanged the second time",
          broken.read_text().count("isVisible()") == 1)

    print("\nfixups that no longer apply, and fixups for other versions")
    older = aot_snapshot.prepare("26.3", engine, jar, DOCUMENT, {"type": "release"}, fixups,
                                 log=lambda *_: None)
    check("a fixup scoped to 26.4+ is not applied to 26.3",
          older["fixups"]["applied"] == [] and older["fixups"]["already"] == [],
          str(older["fixups"]))

    moved = tmp / "moved.json"
    moved.write_text(json.dumps({"fixups": [{
        "file": "port/overrides/libraries/lwjgl-opengl/src/main/java/org/lwjgl/opengl/BrowserOpenGL.java",
        "find": "jsonField(builder, \"gone\", widget.gone)",
        "replace": "irrelevant()",
        "since": "26.4",
        "why": "the engine's file changed under us",
    }]}))
    try:
        aot_snapshot.apply_fixups(engine, "26.4-snapshot-4", moved, log=lambda *_: None)
        check("an anchor that has moved is a refusal, not a silent skip", False, "no error raised")
    except SystemExit as error:
        check("an anchor that has moved is a refusal, not a silent skip",
              "review the fixup" in str(error), str(error)[:200])

    print("\nthe fixup file this repository ships")
    shipped = json.loads((ROOT / "tools" / "aot_fixups.json").read_text())
    check("it is valid json with entries", isinstance(shipped.get("fixups"), list) and shipped["fixups"])
    for entry in shipped["fixups"]:
        check(f"each entry names a file, an anchor, a replacement and a reason ({entry.get('file', '?')[-30:]})",
              all(entry.get(key) for key in ("file", "find", "replace", "why")))
        check("and is scoped to a version range rather than applying everywhere",
              entry.get("since") or entry.get("versions") not in (None, "*"),
              str({k: entry.get(k) for k in ("since", "until", "versions")}))


def profile_document(asset_index_sha1: str) -> dict:
    """26.3's version document, as Mojang publishes it (the engine's own profile is derived from this)."""
    return {
        "id": "26.3", "type": "release", "releaseTime": "2026-09-15T11:23:02+00:00",
        "javaVersion": {"majorVersion": 25},
        "assetIndex": {"id": "34", "sha1": asset_index_sha1},
        "downloads": {"client": {"sha1": "e877b6a0"}, "server": {"sha1": "33680f5f"},
                      "client_mappings": {"sha1": None}},
    }


def test_existing_profile(tmp: pathlib.Path) -> None:
    print("\na profile the engine already has is not replaced by a generated one")
    engine = fake_engine(tmp / "engine-existing")
    jar = client_jar(tmp / "existing-client.jar", VERSION_JSON_26_3)
    curated = json.loads((engine / "port" / "versions" / "26.3.json").read_text())
    lines: list[str] = []

    # the document that profile was written from: nothing should change
    same = tmp / "26.3-up-to-date.json"
    same.write_text(json.dumps(profile_document(curated["official"]["assetIndexSha1"])))
    report = aot_snapshot.prepare("26.3", engine, jar, json.loads(same.read_text()),
                                  {"type": "release"}, None, None, log=lines.append)
    after = json.loads((engine / "port" / "versions" / "26.3.json").read_text())
    check("an up-to-date profile is left completely alone", after == curated)
    check("and the report says no fact needed refreshing", report["refreshed_from_mojang"] == [],
          str(report["refreshed_from_mojang"]))
    check("the patcher still comes from the engine's profile, not a neighbour's",
          report["patchers"]["will_run"] == "Minecraft263BrowserPatcher"
          and report["patchers"]["profile"] == "26.3", str(report["patchers"]))
    check("and it is not reported as a profile this tool generated", report["generated_profile"] is False)

    print("\nbut a fact that drifted from Mojang is refreshed, and only that fact")
    drifted = tmp / "26.3-drifted.json"
    drifted.write_text(json.dumps(profile_document("e787e8afd149a4cefa89d341b91f5aed8f5d93a0")))
    lines = []
    report = aot_snapshot.prepare("26.3", engine, jar, json.loads(drifted.read_text()),
                                  {"type": "release"}, None, None, log=lines.append)
    after = json.loads((engine / "port" / "versions" / "26.3.json").read_text())
    check("the version facts are refreshed from Mojang",
          after["official"]["assetIndexSha1"] == "e787e8afd149a4cefa89d341b91f5aed8f5d93a0",
          after["official"]["assetIndexSha1"])
    check("and the report names exactly which facts changed",
          report["refreshed_from_mojang"] == ["official"], str(report["refreshed_from_mojang"]))
    check("while the engine's own decisions about how to build it survive",
          after["patchSet"] == curated["patchSet"] and after["extraPatchers"] == curated["extraPatchers"]
          and after["storage"] == curated["storage"] and after["worldgenTelemetryMode"]
          == curated["worldgenTelemetryMode"],
          str({k: after[k] for k in ("patchSet", "extraPatchers", "storage")}))
    check("and the log says so instead of silently editing somebody's file",
          any("refreshed from Mojang" in line for line in lines), str(lines))

    print("\nand a version the engine does not know yet is generated")
    fresh = aot_snapshot.prepare("26.3.5", engine, jar, json.loads(drifted.read_text()),
                                 {"type": "release"}, None, None, log=lambda *_: None)
    check("a new version gets a profile generated for it",
          fresh["generated_profile"] is True
          and (engine / "port" / "versions" / "26.3.5.json").is_file())
    check("and the patcher it will run came from the nearest older profile's, via the copy",
          fresh["patchers"]["will_run"] == "Minecraft263BrowserPatcher"
          and fresh["patchers"]["profile"] == "26.3.5"
          and fresh["overrides"]["copied_from"] == "26.3", str(fresh["patchers"]))


def test_bringup(tmp: pathlib.Path) -> None:
    print("\nbring-up: patches this project declares as not ported yet")
    engine = fake_engine(tmp / "engine-bringup")
    ours = tmp / "bringup.json"
    ours.write_text(json.dumps({"entries": [
        {"patch": "MinecraftClientPatcher.patchBuiltInRegistriesBrowserStartupYield",
         "owner": "P8", "since": "26.4",
         "why": "createContents was rewritten, so the entry lambda the patch hooks is gone (run 38545663119)"},
        {"patch": "RenderPatches263.oldOnly", "owner": "P3", "until": "26.3",
         "why": "only applies to the version it was written for"},
    ]}))
    lines: list[str] = []
    report = aot_snapshot.bringup_list(engine, "26.4-snapshot-4", log=lines.append, ours=ours)
    text = (engine / "port" / "tools" / "bringup" / "26.4-snapshot-4.txt").read_text()
    check("our declared patch is in the list the engine reads",
          "MinecraftClientPatcher.patchBuiltInRegistriesBrowserStartupYield" in text, text[-300:])
    check("with its owner and its reason on the same line",
          "| P8 |" in text and "entry lambda" in text, text[-300:])
    check("the neighbour's own deferred patches come along too",
          "RenderPatches263.menuBlur" in text and "step:LwjglPlatformGuard@lwjgl" in text)
    check("an entry scoped to an older version is not carried forward",
          "RenderPatches263.oldOnly" not in text)
    check("the report counts ours and the neighbour's separately",
          report["ours"] == 1 and report["entries"] >= 3, str(report))
    check("and the log says which is which",
          any("carried-over and 1 declared" in line for line in lines), str(lines[-1]))
    check("the list explains where each group came from",
          "Declared by this repository" in text and "Carried over from the neighbour" in text)

    again = aot_snapshot.bringup_list(engine, "26.4-snapshot-4", log=lambda *_: None, ours=ours)
    check("a second run leaves the list alone", again["path"] and
          (engine / "port" / "tools" / "bringup" / "26.4-snapshot-4.txt").read_text() == text)

    print("\nthe entries this repository ships")
    shipped = json.loads((ROOT / "tools" / "aot_bringup.json").read_text())
    check("each one names the patch, the owner and the reason",
          all(all(entry.get(key) for key in ("patch", "owner", "why")) for entry in shipped["entries"]),
          str(shipped["entries"])[:200])
    check("the reason quotes the run that proved it",
          any("run " in entry["why"] for entry in shipped["entries"]), str(shipped["entries"])[:200])
    check("the owner is a work package the engine's own validator accepts",
          all(re.fullmatch(r"P[1-9]", entry["owner"]) for entry in shipped["entries"]),
          str([entry["owner"] for entry in shipped["entries"]]))


def test_command_line(tmp: pathlib.Path) -> None:
    print("\nthe command")
    engine = fake_engine(tmp / "engine-cli")
    jar = client_jar(tmp / "cli-client.jar")
    meta = tmp / "meta.json"
    meta.write_text(json.dumps(DOCUMENT))
    out = tmp / "report.json"
    code = aot_snapshot.main(["--version", "26.4-snapshot-4", "--engine", str(engine),
                              "--client-jar", str(jar), "--meta", str(meta), "--out", str(out)])
    check("it exits zero with a jar and a document already in hand", code == 0, str(code))
    check("it writes the report", out.is_file())
    if out.is_file():
        report = json.loads(out.read_text())
        check("the report names the version, the profile and the review list",
              report["version"] == "26.4-snapshot-4" and report["profile"].endswith(".json")
              and isinstance(report["review"], list), str(report)[:200])
    check("with no engine, it says so instead of writing nothing",
          aot_snapshot.main(["--version", "26.4-snapshot-4", "--engine", str(tmp / "nope"),
                             "--client-jar", str(jar)]) == 2)
    check("with no client jar and no --fetch, it says what is missing",
          aot_snapshot.main(["--version", "26.4-snapshot-4", "--engine", str(engine),
                             "--meta", str(meta)]) == 2)


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        tmp = pathlib.Path(scratch)
        test_resolve()
        test_prepare(tmp)
        test_fixups(tmp)
        test_existing_profile(tmp)
        test_bringup(tmp)
        test_command_line(tmp)
    print(f"\n{FAILURES} snapshot test(s) FAILED" if FAILURES else "\nsnapshot tests passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
