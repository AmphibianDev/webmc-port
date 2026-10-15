#!/usr/bin/env python3
"""Tests for the launch plan: what a browser is told to load, in what order, for which mode.

    python3 tests/test_launchplan.py

Offline, no JDK, no browser, no network: the Fabric profile is a fixture shaped exactly like the
document meta.fabricmc.net returns (that shape is asserted here, so if Fabric changes it this test is
where it shows up), and the version verdicts come from the real classifier reading a real Mojang
document (fixtures/version-1.12.2.json).

What it pins down, because these are the things that would silently break a launch:

  * a mod's declared range means what it says ('1.12.2', '1.12.*', '>=1.14 <1.20.5');
  * a plan says which *row* a version is on, and a blocked row says so instead of pretending;
  * Fabric's own profile becomes a classpath of real Maven URLs, with the loader's libraries first;
  * mods are attached per mode, so a Fabric mod is not offered to a vanilla launch;
  * the index the launcher reads carries no paths, and one plan file per launch does.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import jvm_route  # noqa: E402
import launchplan  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


MOJANG_1_12_2 = json.loads((ROOT / "fixtures" / "version-1.12.2.json").read_text())

# What meta.fabricmc.net/v2/versions/loader/1.12.2/<loader>/profile/json returns, trimmed to four
# libraries but otherwise exact - including the two that matter: intermediary, and the loader.
FABRIC_PROFILE = {
    "id": "fabric-loader-0.19.5-1.12.2",
    "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
    "libraries": [
        {"name": "org.ow2.asm:asm:9.10.1", "url": "https://maven.fabricmc.net/"},
        {"name": "net.fabricmc:sponge-mixin:0.17.4+mixin.0.8.7", "url": "https://maven.fabricmc.net/"},
        {"name": "net.legacyfabric:intermediary:1.12.2", "url": "https://maven.legacyfabric.net/"},
        {"name": "net.fabricmc:fabric-loader:0.19.5", "url": "https://maven.fabricmc.net/"},
        {"name": "com.google.guava:guava:21.0", "url": "https://maven.fabricmc.net/"},
    ],
}

FABRIC_SUPPORT = {
    "newest_loader": "0.19.5",
    "supported": ["1.12.2", "1.14", "1.20.4", "26.3"],
    "stable": ["1.12.2", "1.14", "1.20.4"],
}


def verdict_for(document: dict, version_id: str, **extra) -> dict:
    """A verdict the way tools/jvm_route.py writes one into state.json."""
    verdict = jvm_route.classify(document)
    verdict.update({
        "id": version_id,
        "type": document.get("type"),
        "released": document.get("releaseTime"),
        "source": "document",
        "jar_url": (document.get("downloads") or {}).get("client", {}).get("url"),
        "jar_sha1": (document.get("downloads") or {}).get("client", {}).get("sha1"),
        "jar_bytes": (document.get("downloads") or {}).get("client", {}).get("size"),
        "main_class": document.get("mainClass"),
        "asset_index": (document.get("assetIndex") or {}).get("id"),
        "meta_url": "https://piston-meta.mojang.com/v1/packages/x/1.12.2.json",
        "lwjgl": jvm_route.lwjgl_version(document),
    })
    verdict.update(extra)
    return verdict


def version_document(java: int, lwjgl: str, main: str = "net.minecraft.client.main.Main") -> dict:
    """A Mojang document with one thing changed: the Java level or the LWJGL generation."""
    document = json.loads(json.dumps(MOJANG_1_12_2))
    document["javaVersion"] = {"component": "test", "majorVersion": java}
    document["mainClass"] = main
    libraries = [lib for lib in document["libraries"]
                 if not str(lib.get("name", "")).startswith(("org.lwjgl.lwjgl:", "org.lwjgl:"))]
    if lwjgl == "3":
        libraries.insert(0, {"name": f"org.lwjgl:lwjgl:{'3.3.3'}", "downloads": {}})
    else:
        libraries.insert(0, {"name": f"org.lwjgl.lwjgl:lwjgl:{lwjgl}", "downloads": {}})
    document["libraries"] = libraries
    return document


def make_mod(mods_dir: pathlib.Path, name: str, payload: str = "x") -> str:
    """A real (tiny) jar, so the manifest's size and hash checks have something to be right about."""
    mods_dir.mkdir(parents=True, exist_ok=True)
    path = mods_dir / name
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("webmc.mod.json", '{"id": "x", "entrypoint": "x.Y"}')
        jar.writestr("payload.txt", payload)
    return path.name


def test_matching() -> None:
    print("\na mod's declared versions")
    check("an exact version matches", launchplan.matches("1.12.2", "1.12.2"))
    check("and nothing else does", not launchplan.matches("1.12.2", "1.12.1"))
    check("a prefix matches its family", launchplan.matches("1.12.", "1.12.2")
          and launchplan.matches("1.12.*", "1.12.2"))
    check("a range holds at both ends, exclusive",
          launchplan.matches(">=1.14 <1.20.5", "1.14") and launchplan.matches(">=1.14 <1.20.5", "1.20.4")
          and not launchplan.matches(">=1.14 <1.20.5", "1.20.5"))
    check("'any' means any", launchplan.matches("*", "b1.7.3") and launchplan.matches("", "26.3"))
    check("a list of versions works", launchplan.matches("1.8.9 1.12.2", "1.12.2")
          and not launchplan.matches("1.8.9 1.12.2", "1.11.2"))
    check("snapshots sort below the release they lead to",
          launchplan.version_key("26.4-snapshot-4") < launchplan.version_key("26.4"),
          f"{launchplan.version_key('26.4-snapshot-4')} vs {launchplan.version_key('26.4')}")
    check("betas sort below releases", launchplan.version_key("b1.7.3") < launchplan.version_key("1.7.3"))


def test_mods(tmp: pathlib.Path) -> list[dict]:
    print("\nthe mods folder")
    mods_dir = tmp / "mods"
    jar = make_mod(mods_dir / "jars", "hello.jar")
    (mods_dir / "mods.json").write_text(json.dumps({"mods": [
        {"id": "hello", "name": "Hello", "version": "1.0", "file": f"jars/{jar}",
         "entrypoint": "com.example.Hello", "game": ["1.12.2"], "loaders": ["vanilla"]},
        {"id": "fabric-mod", "name": "Fabric Mod", "version": "2.0", "file": f"jars/{jar}",
         "game": [">=1.14 <1.20.5"], "loaders": ["fabric"]},
        {"id": "gone", "name": "Not built yet", "version": "3.0", "file": "jars/absent.jar",
         "game": ["*"], "loaders": ["vanilla"]},
    ]}) + "\n")

    lines: list[str] = []
    mods = launchplan.load_mods(mods_dir / "mods.json", ROOT, log=lines.append)
    check("a jar that is there is loaded", [mod["id"] for mod in mods] == ["hello", "fabric-mod"],
          str([mod["id"] for mod in mods]))
    check("a jar that is missing is reported, not fatal",
          any("gone" in line and "skipped" in line for line in lines), str(lines))
    check("its size and hash are recorded", mods[0]["bytes"] > 0 and len(mods[0]["sha256"]) == 64)
    check("its file is a site-relative path, which is what the page fetches",
          mods[0]["file"] == "mods/hello.jar", mods[0]["file"])
    check("vanilla-only means vanilla only",
          [mod["id"] for mod in launchplan.mods_for(mods, "1.12.2", "vanilla")] == ["hello"])
    check("a Fabric mod with a range is offered in the range",
          [mod["id"] for mod in launchplan.mods_for(mods, "1.20.4", "fabric")] == ["fabric-mod"])
    check("and not outside it", launchplan.mods_for(mods, "1.20.5", "fabric") == [])

    # a manifest that pins the wrong hash must not silently ship the wrong jar
    (mods_dir / "bad.json").write_text(json.dumps({"mods": [
        {"id": "hello", "file": f"jars/{jar}", "sha256": "0" * 64, "game": ["*"], "loaders": ["vanilla"]},
    ]}) + "\n")
    lines = []
    check("a hash that does not match is refused",
          launchplan.load_mods(mods_dir / "bad.json", ROOT, log=lines.append) == []
          and any("sha256" in line for line in lines), str(lines))
    return mods


def test_plans(mods: list[dict]) -> None:
    print("\none version, three rows")
    one_twelve = verdict_for(version_document(8, "2.9.4"), "1.12.2")
    shim = verdict_for(version_document(17, "3", "net.minecraft.client.main.Main"), "1.16.5")
    modern = verdict_for(version_document(25, "3", "net.minecraft.client.main.Main"), "26.3")
    old = verdict_for(version_document(8, "2.9.4", "net.minecraft.client.Minecraft"), "1.8.9")

    check("1.12.2 is the real-Java row", one_twelve["route"] == "cheerpj", one_twelve["route"])
    check("and its LWJGL version comes out of its own metadata", one_twelve["lwjgl"] == "2.9.4",
          str(one_twelve.get("lwjgl")))
    check("1.16.5 is blocked on our LWJGL 3, and the verdict says so",
          shim["route"] == "needs-lwjgl3" and "LWJGL" in shim["why"], shim["why"])
    check("26.3 is blocked on Java 25", modern["route"] == "needs-jvm", modern["why"])

    fabric = {"fabric": {"1.12.2": {"supported": True, "loader": "0.19.5"}, "26.3": {"supported": True, "loader": "0.19.5"}},
              "legacyfabric": {"supported": ["1.12.2", "1.8.9"]}}
    profiles = {("fabric", "1.12.2"): FABRIC_PROFILE, ("legacyfabric", "1.12.2"): FABRIC_PROFILE,
                ("fabric", "1.8.9"): FABRIC_PROFILE, ("legacyfabric", "1.8.9"): FABRIC_PROFILE}
    plan = launchplan.plan_version({"id": "1.12.2", "type": "release"},
                                   one_twelve, mods, fabric, profiles, "0.19.5")
    check("a playable version is marked playable", plan["playable"] is True)
    check("vanilla is always offered", "vanilla" in plan["modes"])
    check("vanilla starts the game's own main class",
          plan["modes"]["vanilla"]["main"] == "net.minecraft.client.main.Main")
    check("fabric is offered when Fabric's meta knows the version", "fabric" in plan["modes"])
    check("legacy fabric is offered from its own meta, which is what 1.8.9 - 1.12.2 have",
          "legacyfabric" in plan["modes"], str(list(plan["modes"])))
    check("fabric starts Fabric's entry class, not the game's",
          plan["modes"]["fabric"]["main"] == "net.fabricmc.loader.impl.launch.knot.KnotClient",
          plan["modes"]["fabric"]["main"])
    check("the vanilla-only mod comes with vanilla, and not with Fabric",
          plan["modes"]["vanilla"]["mods"] == ["hello"] and plan["modes"]["fabric"]["mods"] == [])
    check("a version with a vanilla mod asks for the loader",
          plan["modes"]["vanilla"]["loader"] is True)

    no_support = launchplan.plan_version({"id": "1.12.2", "type": "release"},
                                          one_twelve, mods, {"fabric": {}, "legacyfabric": {}}, {}, "0.19.5")
    check("a version Fabric never supported simply has no Fabric mode",
          "fabric" not in no_support["modes"] and "mode_errors" not in no_support,
          str(no_support.get("mode_errors")))

    broken = launchplan.plan_version({"id": "1.12.2", "type": "release"},
                                     one_twelve, mods,
                                     {"fabric": {"1.12.2": {"supported": True}}, "legacyfabric": {}},
                                     {("fabric", "1.12.2"): {"error": "meta unreachable"}}, "0.19.5")
    check("a profile that could not be fetched is recorded, not hidden",
          broken["mode_errors"]["fabric"] == "meta unreachable", str(broken.get("mode_errors")))

    plan_shim = launchplan.plan_version({"id": "1.16.5", "type": "release"},
                                        shim, mods, fabric, {("fabric", "1.16.5"): FABRIC_PROFILE}, "0.19.5")
    check("the shim row is not playable", plan_shim["playable"] is False)
    check("and says what it is waiting for", "LWJGL 3" in plan_shim["blocker"], plan_shim.get("blocker"))
    check("but it still describes the run, so CI can test the shim with it",
          plan_shim["modes"]["vanilla"]["main"] == "net.minecraft.client.main.Main")
    check("with our shim's natives, not CheerpJ's LWJGL 2 layer",
          plan_shim["natives"] == "webmc-lwjgl3")

    plan_aot = launchplan.plan_version({"id": "26.3", "type": "release"}, modern, mods, fabric, {}, "0.19.5")
    check("the compiled row is not this page's job", plan_aot["playable"] is False
          and "Java 21" in plan_aot["note"] and plan_aot["modes"] == {})
    check("and it says mods there are baked at build time, not loaded at play time",
          any("build time" in caveat for caveat in plan_aot.get("caveats") or []),
          str(plan_aot.get("caveats")))

    queued = verdict_for(version_document(8, "2.9.4"), "1.12.2", jar_url=None)
    plan_queued = launchplan.plan_version({"id": "1.12.2", "type": "release"}, queued, mods, fabric, {}, None)
    check("no client jar means no route", plan_queued["playable"] is False
          and "no client jar" in plan_queued.get("blocker", ""), str(plan_queued.get("blocker")))

    old_plan = launchplan.plan_version({"id": "1.8.9", "type": "release"}, old, mods, fabric, profiles, "0.19.5")
    check("an alpha/beta-era client is a real-Java version too", old_plan["row"] == "cheerpj")
    check("and Fabric reaches it through Legacy Fabric, not modern Fabric",
          "fabric" not in old_plan["modes"] and "legacyfabric" in old_plan["modes"],
          str(list(old_plan["modes"])))


def test_fabric_profile() -> None:
    print("\nFabric's own profile, turned into a classpath")
    run = launchplan.fabric_run(FABRIC_PROFILE, "fabric")
    check("the loader's jars come back as real Maven URLs",
          "https://maven.fabricmc.net/net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar" in run["jars"],
          str(run["jars"][:2]))
    check("Legacy Fabric's intermediary comes from Legacy Fabric's Maven",
          any("meta.legacyfabric" in url or "maven.legacyfabric" in url for url in run["jars"]),
          str(run["jars"]))
    check("a plus in a version becomes a path segment, not an escape",
          any("sponge-mixin-0.17.4+mixin.0.8.7.jar" in url for url in run["jars"]), str(run["jars"]))
    check("a library the game already has is marked as a duplicate",
          any(lib["duplicate_of_mojang"] for lib in run["libraries"]))
    check("the properties Fabric actually reads are set", run["props"]["fabric.side"] == "client"
          and run["props"]["fabric.modsFolder"] == "/str/mods")
    check("the game-version property is filled in per version, not here",
          run["props"]["fabric.gameVersion"] is None)
    check("legacy fabric mode names the other loader",
          launchplan.fabric_run(FABRIC_PROFILE, "legacyfabric")["loader"] == "legacyfabric")


def test_index_and_cli(tmp: pathlib.Path, mods: list[dict]) -> None:
    print("\nthe index, the plans, and the command that writes them")
    one_twelve = verdict_for(version_document(8, "2.9.4"), "1.12.2")
    shim = verdict_for(version_document(17, "3"), "1.16.5")
    modern = verdict_for(version_document(25, "3"), "26.3")
    jvm = {"1.12.2": one_twelve, "1.16.5": shim, "26.3": modern}
    fabric = {"supported": ["1.12.2", "1.20.4"], "newest_loader": "0.19.5"}
    legacy = {"supported": ["1.12.2", "1.8.9"], "newest_loader": "0.19.5"}
    manifest = {"versions": [{"id": "1.12.2", "type": "release", "releaseTime": "2017-09-18T12:00:00+00:00"}]}
    fetched: list[str] = []

    def fake_fetch(url):
        fetched.append(url)
        return FABRIC_PROFILE

    loader = {"jar": "loader/webmc-loader.jar", "class": "webmc.loader.WebMcLoader",
              "bytes": 1234, "sha256": "a" * 64}
    document = launchplan.build(manifest, jvm, fabric, mods, loader, limit=5, fetch=fake_fetch,
                                log=lambda *args: None, legacy=legacy)
    check("every classified version gets a plan, not only the ones in the launcher's list",
          sorted(document["versions"]) == ["1.12.2", "1.16.5", "26.3"], str(sorted(document["versions"])))
    check("Fabric's profile is fetched once per version and per Fabric, and no more",
          len(fetched) == 2, str(fetched))

    index = launchplan.index_of(document)
    check("the index keeps the mods list", [mod["id"] for mod in index["mods"]] == ["hello", "fabric-mod"])
    check("the index says which modes a version has",
          index["versions"]["1.12.2"]["modes"].get("legacyfabric") == []
          and "vanilla" in index["versions"]["1.12.2"]["modes"])
    check("the index carries no URLs: it is the launcher's list, not a launch",
          "https://" not in json.dumps(index) and "client" not in index["versions"]["1.12.2"],
          json.dumps(index)[:200])
    check("a blocked version's reason travels with it",
          "LWJGL 3" in index["versions"]["1.16.5"]["blocker"])

    # the command line, writing into a scratch site the way the workflow does
    mods_dir = tmp / "mods"
    (tmp / "site").mkdir(exist_ok=True)
    (tmp / "manifest.json").write_text(json.dumps(manifest))
    (tmp / "state.json").write_text(json.dumps({"jvm": jvm}))
    (tmp / "fabric.json").write_text(json.dumps(fabric))
    (tmp / "legacy.json").write_text(json.dumps(legacy))
    mock_loader = tmp / "webmc-loader.jar"
    mock_loader.write_bytes(b"PK\x03\x04 pretend loader")
    # The command line is offline like the rest of the suite: the one thing that would otherwise leave
    # the machine is the Fabric profile fetch, and it is replaced for exactly this call.
    real_profile = launchplan.fabric_profile
    launchplan.fabric_profile = lambda meta, game, loader, fetch=None: FABRIC_PROFILE
    try:
        code = launchplan.main([
            "--manifest", str(tmp / "manifest.json"), "--state", str(tmp / "state.json"),
            "--fabric", str(tmp / "fabric.json"), "--legacy", str(tmp / "legacy.json"),
            "--mods", str(mods_dir / "mods.json"),
            "--loader-jar", str(mock_loader),
            "--out", str(tmp / "site" / "launch.json"), "--plans", str(tmp / "site" / "plans"),
            "--stage", str(tmp / "site" / "mods"), "--write",
        ])
    finally:
        launchplan.fabric_profile = real_profile
    check("it exits zero", code == 0, str(code))
    staged = sorted(path.name for path in (tmp / "site" / "mods").glob("*.jar"))
    check("the mods are staged into the site", staged == ["hello.jar"], str(staged))
    written = json.loads((tmp / "site" / "launch.json").read_text())
    check("the index is what was written to --out", "versions" in written and "hello" in json.dumps(written))
    plans = sorted(path.name for path in (tmp / "site" / "plans").glob("*.json"))
    check("one plan per launchable version, and none for the compiled row",
          plans == ["1.12.2.json", "1.16.5.json"], str(plans))
    plan = json.loads((tmp / "site" / "plans" / "1.12.2.json").read_text())
    check("the plan file is the whole launch", plan["client"]["url"].startswith("https://piston-data")
          and plan["modes"]["fabric"]["jars"], str(plan.get("client")))


def test_loader_block(tmp: pathlib.Path) -> None:
    print("\nthe loader the plans point at")
    missing = launchplan.loader_block(tmp / "not-built.jar")
    check("an unbuilt loader is named as missing, and the fallback is stated",
          "missing" in missing and "starting the game class itself" in missing["missing"], str(missing))
    built = tmp / "webmc-loader.jar"
    built.write_bytes(b"PK\x03\x04 pretend jar")
    block = launchplan.loader_block(built)
    check("a built loader is pinned by hash and size",
          block["bytes"] == len(b"PK\x03\x04 pretend jar") and len(block["sha256"]) == 64 and "missing" not in block, str(block))


def test_page_contract(tmp: pathlib.Path, mods: list[dict]) -> None:
    """The names the two pages read must be the names the plan actually has.

    A plan is data with a schema, and the schema is only real if something checks it: the launcher
    reads `plan.playable` and `plan.modes`, the launch page reads `plan.client.url` and
    `modes.<mode>.jars`, and none of that is visible to a Python type checker. So this test reads the
    field names out of the pages (they are plain `plan.x` / `launch.x` accesses) and asserts each one
    the pages depend on is present in a generated plan - and, in the other direction, that each field a
    plan carries is one a page reads, so a field nobody uses is a field to delete rather than to keep
    "just in case".
    """
    print("\nthe pages and the plans agree on the field names")
    one_twelve = verdict_for(version_document(8, "2.9.4"), "1.12.2")
    fabric = {"fabric": {"1.12.2": {"supported": True}},
              "legacyfabric": {"1.12.2": {"supported": True}}}
    profiles = {("fabric", "1.12.2"): FABRIC_PROFILE, ("legacyfabric", "1.12.2"): FABRIC_PROFILE}
    plan = launchplan.plan_version({"id": "1.12.2", "type": "release"}, one_twelve, mods, fabric,
                                   profiles, "0.19.5")
    document = {"note": "n", "updated": "u",
                "loader": launchplan.loader_block(tmp / "absent.jar"), "mods": mods,
                "versions": {"1.12.2": plan}}
    index = launchplan.index_of(document)

    launcher = (ROOT / "site" / "index.html").read_text()
    launch_page = (ROOT / "site" / "play.html").read_text()

    # What the launch page reads off a plan, and why it needs it.
    for field, why in {
        "row": "to say which engine this is",
        "why": "to say why a blocked row is blocked",
        "playable": "to refuse instead of showing a black canvas",
        "java": "to start the JVM in the mode the client needs",
        "natives": "to find the right native layer under java.library.path",
        "client": "to fetch the client jar from Mojang",
        "meta_url": "to fetch the version document (the asset index lives in it)",
        "asset_index": "to install the index the client looks its textures up in",
        "libs": "to install the version's own classpath",
        "modes": "to start the mode that was asked for",
    }.items():
        check(f"the plan carries '{field}' ({why})", field in plan)
        check(f"and the launch page reads it", f".{field}" in launch_page)
    for field, why in {
        "libs_ready": "to warn that the client will stop early without its libraries",
        "blocker": "to explain a version that cannot run (only set when it cannot)",
    }.items():
        check(f"the launch page reads '{field}' ({why})", f".{field}" in launch_page)
    check("a playable plan does not claim a blocker", "blocker" not in plan, plan.get("blocker"))
    check("but it does say that Fabric on this row is unproven",
          any("cheerpj-meta#231" in caveat for caveat in plan.get("caveats") or []),
          str(plan.get("caveats")))
    blocked_plan = launchplan.plan_version(
        {"id": "1.16.5", "type": "release"},
        verdict_for(version_document(17, "3"), "1.16.5"), mods, fabric, {}, "0.19.5")
    check("and a blocked one does, so the page has something to say", "blocker" in blocked_plan)
    for field in ("main", "jars", "props", "mods", "loader", "mode"):
        check(f"every mode carries '{field}'", field in plan["modes"]["vanilla"]
              and field in plan["modes"]["fabric"], str(sorted(plan["modes"]["vanilla"])))
        check(f"and the launch page reads it", f"run.{field}" in launch_page or f"mode.{field}" in launch_page)

    # What the launcher reads off the index.
    for field in ("loader", "mods", "versions"):
        check(f"the index carries '{field}'", field in index)
    for field, why in {
        "row": "to label the row honestly",
        "playable": "to rank and to refuse",
        "modes": "to offer vanilla and Fabric",
        "java": "to say what runtime a version needs",
        "why": "to explain a version's verdict in its own words",
        "caveats": "to warn about what is unproven on that row before somebody presses play",
    }.items():
        check(f"every index entry carries '{field}' ({why})", field in index["versions"]["1.12.2"])
    # The launcher reads the plan for which modes and mods a version has, and the index (site/routes.json)
    # for how it runs and who builds it - two files, two jobs, and this is the seam between them.
    check("the launcher reads the plan for its modes and mods",
          "plan.modes" in launcher and "function modsOf" in launcher)
    check("and the route index for how it runs and who builds it",
          "route.engine" in launcher and "route.route" in launcher and "route.bakes_mods" in launcher)
    check("the launcher reads a mod's file, which is what it fetches",
          "mod.file" in launcher or "mod.id" in launcher)
    check("the index names our loader and where to get it",
          index["loader"]["jar"] == "loader/webmc-loader.jar"
          and index["loader"]["class"] == "webmc.loader.WebMcLoader")
    check("and says so when it has not been built",
          "missing" in index["loader"] and "starting the game class itself" in index["loader"]["missing"])

    # Fields a page never reads are fields nobody should have to maintain.
    launcher_fields = {"row", "playable", "modes", "java", "why", "blocker", "note", "caveats"}
    unused = set(index["versions"]["1.12.2"]) - launcher_fields
    check("the index carries nothing the launcher does not read", not unused, str(sorted(unused)))


def with_libs(tmp: pathlib.Path, *versions: str) -> pathlib.Path:
    """A vendored-library index for these versions, so a plan can be 'playable' in a test."""
    directory = tmp / f"libs-{'-'.join(versions) or 'none'}"
    directory.mkdir(parents=True, exist_ok=True)
    for version in versions:
        (directory / f"{version}.json").write_text(json.dumps({"classpath": ["libs/fake.jar"]}))
    return directory


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        tmp = pathlib.Path(scratch)
        # every version in these tests is "deployed" unless a test says otherwise, so the assertions
        # describe the plan rather than the state of a checkout
        launchplan.LIBS_DIR = with_libs(tmp, "1.12.2", "1.8.9", "1.16.5", "26.3")
        test_matching()
        mods = test_mods(tmp)
        test_plans(mods)
        test_fabric_profile()
        test_index_and_cli(tmp, mods)
        test_page_contract(tmp, mods)
        test_loader_block(tmp)
    print(f"\n{FAILURES} launch plan test(s) FAILED" if FAILURES else "\nlaunch plan tests passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
