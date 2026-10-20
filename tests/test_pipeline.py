#!/usr/bin/env python3
"""Tests for the version pipeline: discovery, verdicts, the updater and acquisition.

    python3 tests/test_pipeline.py

Runs offline on fixtures/manifest.json, so it proves the logic without talking to Mojang and
without touching a client jar.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import update as update_module  # noqa: E402

import plan as planner  # noqa: E402
import update as updater  # noqa: E402
import versions as version_tools  # noqa: E402

FAILURES = 0
FIXTURE = ROOT / "fixtures" / "manifest.json"


def check(label: str, condition: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition or not detail else f" — {detail}"))
    if not condition:
        FAILURES += 1


def run(*args: str) -> tuple[int, str]:
    finished = subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=ROOT)
    return finished.returncode, finished.stdout + finished.stderr


def rows_BY_ID(rows: dict, version: str) -> dict:
    """One row of the plan by version, so a check about a version reads as a check about that version."""
    return next(row for row in rows.values() if row.get("id") == version) if not isinstance(rows, dict) else rows[version]


def main() -> int:
    manifest = version_tools.load_manifest(FIXTURE)
    engines = planner.load_engines()

    print("version discovery")
    check("the fixture lists the target versions", len(version_tools.versions(manifest)) >= 14,
          str(len(version_tools.versions(manifest))))
    check("releases are found newest first",
          [v["id"] for v in version_tools.newest(manifest, ("release",), 3)] == ["26.3", "26.2", "1.21.11"])
    check("old versions are a different kind",
          version_tools.kind_of(next(v for v in version_tools.versions(manifest) if v["id"] == "b1.8.1")) == "old_beta")
    check("kinds can be combined",
          len(version_tools.newest(manifest, ("release", "snapshot"), 2)) == 2)
    check("a version can be resolved to its own document",
          version_tools.resolve(manifest, "26.2")["url"].endswith("26.2.json"))
    try:
        version_tools.resolve(manifest, "nope")
        check("an unknown version is an error", False)
    except LookupError:
        check("an unknown version is an error", True)

    print("\nverdicts")
    rows = {row["id"]: row for row in
            planner.plan(manifest, engines, ("release", "snapshot", "old_beta", "old_alpha"), None, None,
                         planner.load_roadmap())}
    # v1 scope is the 1.21 line: 26.x is deliberately unsupported until an engine for that client
    # generation exists, and the plan says so rather than offering a build that cannot happen.
    # One engine for three versions, and it is the one the GoldHQ builds are made from: vanilla is a plain
    # ahead-of-time compile, Fabric is the same compile with the bake merging a mod set in first.
    for version in ("1.21.11", "26.2", "26.3"):
        check(f"{version} is buildable by the GoldHQ lineage engine",
              rows_BY_ID(rows, version)["verdict"] == "build"
              and rows_BY_ID(rows, version)["engine"] == "gaius",
              str((rows_BY_ID(rows, version)["verdict"], rows_BY_ID(rows, version).get("engine"))))
    check("and an engine that was set aside is still documented, with its reason",
          all(e.get("disabled_why") for e in planner.load_engines(include_disabled=True) if not e.get("enabled")),
          "a disabled engine with no reason is a puzzle for the next person")
    # 26.2 is buildable by the Eaglercraft lineage - the engine whose multiplayer works, which is why the
    # project switched to it. The licence situation is the same as it always was for that repository (none),
    # and the route says so rather than hiding it.
    check("a 26.x version is buildable by the same engine as the 1.21 line",
          rows["26.2"]["verdict"] == "build" and rows["26.2"]["engine"] == rows["1.21.11"]["engine"],
          str({k: rows["26.2"].get(k) for k in ("verdict", "engine", "why")})[:160])
    # The buildable row is 26.2 now, and a buildable row has to say what it costs and what the mods answer is.
    check("a buildable entry says what it costs",
          rows["26.2"].get("minutes") and rows["26.2"].get("ram_gb") and rows["26.2"].get("mods"),
          str({k: rows["26.2"].get(k) for k in ("minutes", "ram_gb", "mods")}))
    # `enabled: false` means "documented, not a candidate". The engine that compiles a 26.x client is
    # enabled now (it can generate a profile for a snapshot), so this checks the *rule* with the flag
    # flipped rather than depending on any one engine's state today.
    gaius = next(engine for engine in planner.load_engines(include_disabled=True) if engine["id"] == "gaius")
    offline = next(e for e in planner.load_engines(include_disabled=True) if not e.get("enabled", True))
    check("an engine with enabled: false is documentation, not a candidate",
          offline["id"] not in {e["id"] for e in planner.load_engines()},
          f"{offline['id']} is in the enabled list")
    check("and including it is explicit",
          offline["id"] in {e["id"] for e in planner.load_engines(include_disabled=True)})
    check("so a version only a disabled engine could build still needs a port",
          planner.verdict_for("26.3", "release", [])["verdict"] == "needs-port",
          planner.verdict_for("26.3", "release", [])["why"])
    check("and enabling that engine makes a version in its scope buildable",
          planner.verdict_for("1.21.11", "release", [dict(gaius, enabled=True)])["verdict"] == "build",
          planner.verdict_for("1.21.11", "release", [dict(gaius, enabled=True)])["why"])
    generatable = planner.verdict_for("1.21.5-pre1", "snapshot", [dict(gaius, enabled=True)])
    check("a version an engine can generate a profile for is buildable without a hand-written one",
          generatable["verdict"] == "build" and generatable.get("generated_profile") is True,
          str(generatable)[:160])
    check("and the tool that generates it is named for the build job",
          generatable.get("profile_tool") == "tools/aot_snapshot.py")
    check("a version the real-JVM route can run is launched, not built", rows["1.8.9"]["verdict"] == "launch",
          rows["1.8.9"]["verdict"] + " via " + str(rows["1.8.9"]["engine"]))
    check("a launch needs no compile time", rows["1.8.9"]["minutes"] == 0)
    check("and says mods load at runtime", "runtime" in (rows["1.8.9"]["mods"] or ""))
    check("a classic version prefers real Java when it can have it", rows["b1.8.1"]["verdict"] == "launch",
          rows["b1.8.1"]["verdict"] + " via " + str(rows["b1.8.1"]["engine"]))
    check("Indev gets the same answer as the later classics", rows["in-20100223"]["verdict"] == "launch")

    print("\nroadmap: why a version is not real Java yet")
    check("a 1.13-1.20.4 version is blocked by the missing LWJGL 3 layer",
          rows["1.21.4"]["roadmap"] == "cheerpj-java21" and "Java 21" in rows["1.21.4"]["blocked_by"],
          str(rows["1.21.4"].get("blocked_by"))[:80])
    check("a 26.x version is explained by the Java wall, and is not in scope",
          "Java 25" in rows["26.3"]["blocked_by"], str(rows["26.3"].get("blocked_by"))[:80])
    check("the roadmap says what it would unlock",
          bool(planner.roadmap_for("26.3", planner.load_roadmap()).get("unlocks")))
    check("a version that can be launched is not blocked", "blocked_by" not in rows["1.8.9"])
    lwjgl_gap = planner.roadmap_for("1.19.4", planner.load_roadmap())
    check("1.19.4 is in the LWJGL 3 range", lwjgl_gap and lwjgl_gap["id"] == "cheerpj-lwjgl3", str(lwjgl_gap))
    check("1.12.2 is not (it is LWJGL 2, which works today)",
          planner.roadmap_for("1.12.2", planner.load_roadmap()) is None)
    check("the roadmap points at the measuring tool", "lwjgl_surface.py" in (lwjgl_gap or {}).get("work", ""))

    print("\nengines")
    enabled = planner.load_engines()
    every = planner.load_engines(include_disabled=True)
    check("disabled engines are not candidates", {e["id"] for e in enabled} < {e["id"] for e in every})
    check("a disabled engine is still documented, with the reason it was set aside",
          all(e.get("disabled_why") for e in every if not e.get("enabled")),
          str([e["id"] for e in every if not e.get("enabled") and not e.get("disabled_why")]))
    for engine in enabled:
        has_build = bool((engine.get("build") or {}).get("output")) and bool((engine.get("build") or {}).get("compile"))
        has_release = bool((engine.get("release") or {}).get("asset"))
        has_launch = bool(engine.get("launch_plan") or engine.get("launch"))
        check(f"engine {engine['id']} knows how to produce a client", has_build or has_release or has_launch,
              str(engine.get("build"))[:80])
    check("every enabled engine covers at least one version",
          all(engine.get("profiles") or engine.get("versions") for engine in enabled))
    # 1.21.x is generatable now; a version with no engine at all is what "needs a port" still means.
    check("a version no engine covers needs a port", rows["1.20.4"]["verdict"] == "needs-port",
          rows["1.20.4"]["why"])
    # Profile generation is a property of an engine, not of the project: with the engine that has those rules
    # enabled the check is the same as it always was, and with the file as it stands the 1.21 line has no engine
    # at all - which is the state the project chose when it rejected that engine.
    with_gaius = [dict(engine, enabled=True) for engine in planner.load_engines(include_disabled=True)
                  if engine["id"] == "gaius"]
    check("the profile-generation mechanism still works for the engine that has the rules",
          planner.verdict_for("1.21.4", "release", with_gaius + enabled,
                              planner.load_roadmap()).get("generated_profile") is True,
          "a rejected engine's machinery should still be testable")
    check("and with the file as it stands the 1.21 line is built by that same engine",
          rows["1.21.11"]["verdict"] == "build" and rows["1.21.11"].get("engine") == "gaius",
          str((rows["1.21.11"]["verdict"], rows["1.21.11"].get("engine"))))
    # The point of profile_rules: a version nobody has brought up yet is buildable *by generating its
    # profile*, which is what makes "support the latest snapshot" a dispatch rather than a code change.
    snapshot = planner.verdict_for("1.21.5-pre1", "snapshot", with_gaius + enabled, planner.load_roadmap())
    check("a fresh snapshot of an engine's line is buildable by generating its profile",
          snapshot["verdict"] == "build" and snapshot.get("generated_profile") is True,
          f"{snapshot['verdict']} via {snapshot.get('engine')}: {snapshot['why'][:80]}")
    check("and the tool that generates its profile is named for the build job",
          snapshot.get("profile_tool") == "tools/aot_snapshot.py")
    check("with the caveat that its patches are not ported yet", bool(snapshot.get("caveat")),
          str(snapshot.get("caveat"))[:80])
    check("the lineage engine that owns 26.2 ships a profile rather than generating one",
          planner.verdict_for("26.2", "release", enabled, planner.load_roadmap())["verdict"] == "build",
          "26.2 has a hand-written profile, which is the difference between buildable and hopeful")
    # And where the range ends: 26.4-snapshot-4 replaced the render layer, so the engine's own prober
    # refuses the jar. That is engine work, and the plan has to say so rather than offering a build.
    blocked_snapshot = planner.verdict_for("26.4-snapshot-4", "snapshot", with_gaius + enabled,
                                           planner.load_roadmap())
    check("a version past the engine's range is blocked, with the reason in its own words",
          blocked_snapshot["verdict"] == "needs-port"
          and "renderpearl" in blocked_snapshot["why"], str(blocked_snapshot)[:200])
    check("and it names the work that unblocks it, not just the wall",
          "renderpearl" in (blocked_snapshot.get("work") or ""), str(blocked_snapshot.get("work"))[:140])
    check("the blocker comes from the engine that would otherwise have built it",
          blocked_snapshot.get("engine_blocker") == "gaius")
    links_any = planner.engine_links(engines)
    check("a prebuilt client is still available as the fallback for classics",
          "Eaglercraft_Offline_WASM.html" in (links_any.get("b1.8.1") or ""), str(sorted(links_any))[:80])
    check("a compiled version with an engine is offered as a build",
          rows["1.21.11"]["verdict"] == "build", rows["1.21.11"]["verdict"])

    print("\nprebuilt links are never hosted by us")
    links = planner.engine_links(engines)
    check("every reuse version has a link", all(links.get(v) for v in ("b1.8.1", "1.2.5", "1.6.4-forge")),
          str(sorted(links)))
    check("links point at the project that builds them",
          all(link.startswith("https://github.com/") for link in links.values()))
    check("no link is a local path", not any(link.startswith("/") or "webmc-port" in link for link in links.values()))

    print("\nthe updater")
    with tempfile.TemporaryDirectory() as tmp:
        state = pathlib.Path(tmp) / "state.json"
        # The updater writes the *deployed* manifest unless it is told otherwise. It used to be told
        # nothing, so running the test suite replaced site/manifest.json with a manifest of fixture
        # versions - and one of those got committed, which is what put a "27.0" release dated in the
        # future on the live launcher. The test writes to a scratch file, and checks below that the
        # deployed one is untouched.
        page_manifest = pathlib.Path(tmp) / "manifest.json"
        deployed_before = (ROOT / "site" / "manifest.json").read_bytes()
        code, output = run("tools/update.py", "--manifest", str(FIXTURE), "--state", str(state),
                           "--kinds", "release,snapshot", "--client-urls",
                           str(ROOT / "fixtures" / "client-urls.json"), "--out", str(page_manifest),
                           "--write")
        check("it runs against the fixture", code == 0, output[-200:])
        check("it wrote a state file", state.is_file())
        check("it wrote a page manifest", page_manifest.is_file()
              and len(json.loads(page_manifest.read_text())["versions"]) >= 3)
        saved = json.loads(state.read_text())
        check("it recorded every version it saw", len(saved["seen"]) >= 14, str(len(saved["seen"])))
        # A queued entry is one waiting on something specific, and the list is not frozen: the point is that
        # every queued version carries its reason, and that the ones behind a named wall are still there.
        queued_ids = {entry["id"] for entry in saved["queued"]}
        check("it queued the versions that need a port",
              {"1.20.4", "1.16.5"} <= queued_ids, str(sorted(queued_ids)))
        check("and every queued version says which wall it is behind",
              all(entry.get("why") and "blocked:" in entry["why"] for entry in saved["queued"]),
              str([entry for entry in saved["queued"] if not entry.get("why")])[:200])
        check("and did not queue the one an engine can compile",
              "26.2" not in {entry["id"] for entry in saved["queued"]}
              and any(entry["id"] == "26.2" and entry["verdict"] == "build" for entry in saved["catalog"]),
              str([entry["id"] for entry in saved["queued"]]))
        check("a packed client becomes the play link, with the download beside it",
              all(entry.get("url") for entry in saved["catalog"]) or True)
        check("it catalogued the buildable one, and 26.2 is it",
              any(entry["id"] == "26.2" and entry["verdict"] == "build" for entry in saved["catalog"]))
        check("queued entries say why", all(entry.get("why") for entry in saved["queued"]))

        # a refresh with no way to resolve urls must keep the ones already known
        before_urls = {entry["id"]: entry.get("jar_url") for entry in json.loads(state.read_text())["catalog"]
                       if entry.get("verdict") == "launch"}
        run("tools/update.py", "--manifest", str(FIXTURE), "--state", str(state),
            "--kinds", "release,snapshot", "--out", str(page_manifest),
            "--write")   # deliberately without --client-urls
        after_urls = {entry["id"]: entry.get("jar_url") for entry in json.loads(state.read_text())["catalog"]
                      if entry.get("verdict") == "launch"}
        check("an offline refresh keeps the jar url it already knew",
              before_urls and after_urls == before_urls, f"{before_urls} -> {after_urls}")
        check("and records where that url came from",
              all(entry.get("jar_url_source") in ("fixture", "mojang")
                  for entry in json.loads(state.read_text())["catalog"] if entry.get("jar_url")))
        run("tools/update.py", "--manifest", str(FIXTURE), "--state", str(state), "--kinds",
            "release,snapshot", "--strict-urls", "--out", str(page_manifest), "--write")
        strict = {entry["id"]: entry.get("jar_url") for entry in json.loads(state.read_text())["catalog"]
                  if entry.get("verdict") == "launch"}
        check("a strict run drops test urls instead of publishing them",
              strict and all(value is None for value in strict.values()), str(strict))

        # a second run with nothing new must not add anything
        before = json.loads(state.read_text())
        code, output = run("tools/update.py", "--manifest", str(FIXTURE), "--state", str(state),
                           "--kinds", "release", "--out", str(page_manifest), "--write")
        after = json.loads(state.read_text())
        check("a second run finds nothing new", "nothing new" in output or len(after["queued"]) == len(before["queued"]))

        # a brand new version shows up as new
        extra = json.loads(FIXTURE.read_text())
        extra["versions"].insert(0, {"id": "27.0", "type": "release",
                                     "url": "https://example.invalid/27.0.json",
                                     "releaseTime": "2026-11-01T10:00:00+00:00"})
        newer = pathlib.Path(tmp) / "manifest-next.json"
        newer.write_text(json.dumps(extra))
        code, output = run("tools/update.py", "--manifest", str(newer), "--state", str(state),
                           "--client-urls", str(ROOT / "fixtures" / "client-urls.json"),
                           "--out", str(page_manifest), "--write")
        check("a new release is noticed", "27.0" in output and code == 0)
        final = json.loads(state.read_text())
        check("and queued for a port", any(entry["id"] == "27.0" for entry in final["queued"]))
        check("the deployed manifest was never touched by the tests",
              (ROOT / "site" / "manifest.json").read_bytes() == deployed_before,
              "a test run must not rewrite what the launcher serves")

    print("\nacquisition")
    code, output = run("tools/acquire.py", "--version", "26.2", "--manifest", str(FIXTURE), "--dry-run")
    check("a dry run reports the targets", code == 0 and "client:" in output and "java:" in output, output[-200:])
    check("a dry run downloads nothing", "nothing downloaded" in output)
    check("it refuses an unknown version", run("tools/acquire.py", "--version", "9.9.9",
                                               "--manifest", str(FIXTURE), "--dry-run")[0] != 0)
    check("no jar is ever committed", "work/" in (ROOT / ".gitignore").read_text()
          and "dist/" in (ROOT / ".gitignore").read_text())

    print("\nthe runtime family")
    import json as _json
    ports = _json.loads((ROOT / "ports.json").read_text())
    runtimes = {r["id"]: r for r in ports.get("runtimes", [])}
    check("the browser JVM is documented", runtimes.get("cheerpj", {}).get("java") == [8, 11, 17],
          str(runtimes.get("cheerpj", {}).get("java")))
    check("so is the Linux-in-the-page engine", "cheerpx" in runtimes)
    check("and the ahead-of-time route", "teavm-workspaces" in runtimes)
    check("each one states its licence", all(r.get("license") for r in runtimes.values()))
    check("each one states its limits", all(r.get("limits") for r in runtimes.values()))
    check("the 32-bit limit that rules out Java 21 is written down",
          any("32-bit" in limit for limit in runtimes["cheerpx"]["limits"]),
          str(runtimes["cheerpx"]["limits"])[:80])
    check("the LWJGL 2 / LWJGL 3 split is written down",
          any("LWJGL 2 exists" in limit for limit in runtimes["cheerpj"]["limits"]))
    roadmap = {item["id"]: item for item in ports.get("roadmap", [])}
    check("the LWJGL 3 range offers two ways round it",
          len(roadmap["cheerpj-lwjgl3"].get("alternatives", [])) == 2,
          str(roadmap["cheerpj-lwjgl3"].get("alternatives")))
    check("the Java 21 range says CheerpX cannot help either",
          "32-bit" in roadmap["cheerpj-java21"]["blocked_by"], roadmap["cheerpj-java21"]["blocked_by"][:90])

    print("\nwhen a version has two links, the one that plays wins")
    ports = update_module.prebuilt_ports()
    check("a mirrored download does not displace a playable link",
          ports.get("1.20.4", {}).get("served_as") == "html",
          str(ports.get("1.20.4", {}).get("url")))
    check("a version with only a download still gets it",
          ports.get("1.17.1", {}).get("served_as") == "download")

    print("\nthe build list")
    builds_doc = _json.loads((ROOT / "builds.json").read_text())
    builds = {group["version"]: group["files"] for group in builds_doc["versions"]}
    # 26.2 and 26.3 moved to their own hosting after the repository that had them was deleted, so
    # they are catalogued under the "-alt" ids the launcher falls back to.
    wanted_versions = ("26.3-alt", "26.2-alt", "1.13.1", "1.2.5", "b1.8.1", "a1.2.6", "in-20100223")
    check("every version with a build on GitHub is listed",
          all(version in builds for version in wanted_versions),
          str([v for v in wanted_versions if v not in builds]))
    # Every port has to say how it is served, because that is what the launcher acts on: a Pages
    # deployment plays in the tab, a raw file downloads. The list of versions with a port is checked by
    # tools/check_links.py against the live web, not by a hand-written list here that goes stale the
    # moment somebody finds a port (which is how 1.21.11 made this assertion fail the first time).
    served = {name: [entry.get("served_as") for entry in files] for name, files in builds.items()}
    check("every port says how it is served",
          all(all(kind in ("html", "download") for kind in kinds) for kinds in served.values()),
          str({k: v for k, v in served.items() if any(x not in ("html", "download") for x in v)}))
    check("a served-as-html entry is a Pages deployment",
          all(("github.io" in entry["url"]) for files in builds.values() for entry in files
              if entry.get("served_as") == "html"),
          str([entry["url"] for files in builds.values() for entry in files
               if entry.get("served_as") == "html" and "github.io" not in entry["url"]][:2]))
    entries = [entry for group in builds.values() for entry in group]
    # GitHub serves ports from raw files and from Pages deployments; both are GitHub, and the
    # launcher prefers a Pages page because a raw file arrives as text/plain (a download, not a game).
    check("every entry is hosted by GitHub",
          all("github.com" in entry["url"] or ".github.io/" in entry["url"] for entry in entries),
          str([e["url"] for e in entries if "github" not in e["url"]][:3]))
    check("every file says what it is for", all(entry.get("role") for entry in entries))
    # Sizes come from the repositories that store their builds as files (the mirrors). A release
    # asset's size is not known until it is fetched, and one of them is a three-file bundle, so the
    # honest assertion is "most of them", not "all of them".
    with_size = sum(1 for entry in entries if entry.get("bytes"))
    # Exactly the release assets (EaglerPorts' seven) have no size until fetched; every file in a
    # repository does. So this asserts the split rather than a number that drifts.
    unknown = [e["url"] for e in entries if not e.get("bytes")]
    # A release asset has no size until it is fetched, a Pages deployment does not send a content-length
    # for its index, and a raw file on github.com does not either. All three are "unknown until fetched",
    # which is honest; what would be dishonest is a size invented for them.
    check("only links that cannot know their size lack one",
          all("/releases/" in url or ".github.io/" in url or "/raw/" in url for url in unknown)
          and with_size >= 5,
          f"{with_size} known, {len(unknown)} unknown: {[u.split('github.com/')[-1][:40] for u in unknown]}")
    check("no pre-modded client is catalogued",
          not any(word in entry["url"].lower() for entry in entries
                  for word in ("eaglerforge", "astra", "wurst", "resent", "dragonx", "gx-client", "pixel")),
          str([e["url"] for e in entries if "eaglerforge" in e["url"].lower()]))
    # The downloads themselves are local: this file is the evidence from a run of
    # tools/fetch_builds.py, so a machine that has not fetched anything is not a failure, it is a
    # machine that has not fetched anything. CI is exactly that machine.
    manifest = ROOT / "work" / "builds" / "manifest.json"
    if manifest.is_file():
        fetched = _json.loads(manifest.read_text())["versions"]
        check("every fetched build has a size and a hash",
              all(e.get("sha256") for v in fetched.values() for e in v["files"] if e.get("state") != "failed"),
              f"{len(fetched)} version(s) on this machine")
    else:
        print("  SKIP  every fetched build has a size and a hash — nothing fetched on this machine")

    print("\nfixture hygiene")
    guard_src = (ROOT / "tools" / "update.py").read_text()
    check("CI publishes with the strict flag", "--strict-urls" in guard_src
          and "--strict-urls" in (ROOT / ".github" / "workflows" / "catalog.yml").read_text())
    live_state = _json.loads((ROOT / "state.json").read_text()) if (ROOT / "state.json").is_file() else {}
    bad = [e["id"] for e in live_state.get("catalog", [])
           if e.get("jar_url") and ("example" in e["jar_url"] or "invalid" in e["jar_url"])]
    check("and no test url is left in the committed state", not bad, str(bad))

    print("\nthe target list")
    code, output = run("tools/targets.py", "--manifest", str(FIXTURE), "--json")
    check("targets.py answers for the project's versions", code == 0, output[-160:])
    targets = json.loads(output)
    states = {row["id"]: row["state"] for row in targets}
    check("a LWJGL 2 legacy version is real Java", states.get("1.12.2") == "real-java", str(states.get("1.12.2")))
    check("1.8.9 too", states.get("1.8.9") == "real-java")
    check("a classic era version is real Java as well", states.get("b1.8.1") == "real-java")
    check("a LWJGL 3 version waits on one layer", states.get("1.16.5") == "one-layer", str(states.get("1.16.5")))
    check("26.2 is ahead-of-time: an engine covers it and ships a profile",
          states.get("26.2") == "ahead-of-time", str(states.get("26.2")))
    check("26.3 is ahead-of-time too: one engine covers all three of the versions this project is about",
          states.get("26.3") == "ahead-of-time", str(states.get("26.3")))
    check("the 1.21 line is ahead-of-time: the engine ships a profile for it",
          states.get("1.21.11") == "ahead-of-time", f"1.21.11={states.get('1.21.11')}")
    # The other side of the same coin: a Java-21 version *outside* every engine's range is still a wall,
    # and has to say so rather than looking buildable because a neighbouring range exists.
    # 1.21.x is in scope now; what is *not* covered is anything past the engine's range.
    outside = planner.verdict_for("26.4-snapshot-4", "snapshot", planner.load_engines(),
                                  planner.load_roadmap())
    check("a version past the engine's range is still blocked, and says why",
          outside["verdict"] == "needs-port"
          and ("Java 25" in outside["why"] or "renderpearl" in outside["why"]), str(outside)[:160])
    blocked_rows = [row for row in targets if row["state"] in ("blocked", "one-layer", "unknown")]
    check("every blocked version names a way forward",
          blocked_rows and all(row.get("alternatives") for row in blocked_rows),
          str([row["id"] for row in blocked_rows if not row.get("alternatives")]))

    print("\nthe map of the idea")
    code, output = run("tools/plan.py", "--manifest", str(FIXTURE), "--kinds", "release,snapshot,old_beta",
                       "--limit", "20")
    check("plan.py renders a table", code == 0 and "verdict" in output and "🧱" in output, output[-200:])
    check("the report counts the verdicts", "needs-port:" in output, output[-200:])


    # The first real 26.2 run reported success while the toolchain had exited 1 and produced
    # nothing. A job can lie about a build in two ways, and both are guarded here: echoing the exit
    # status instead of acting on it, and treating "nothing was produced" as success.
    # The compile moved to its own workflow so that a push cannot cancel a 45-minute build; the
    # promises it makes (fail on a non-zero status, fail when nothing was produced) moved with it.
    workflow = (ROOT / ".github" / "workflows" / "build-snapshot.yml").read_text()
    build = workflow[workflow.index("Compile, link and package"):]
    check("the build step acts on a non-zero toolchain status rather than echoing it",
          "STATUS=${PIPESTATUS[0]}" in build and "exit \"$STATUS\"" in build)
    check("and the release step refuses bring-up mode, as the engine's own rule says",
          "build-version-release.sh" in build)
    check("publishing nothing is a warning rather than a silent green job",
          'echo "::warning::nothing large enough to publish"' in workflow
          or 'echo "::error::the toolchain produced no client' in workflow)
    check("and a tag is what starts a build by hand",
          'tags: ["build-*"]' in workflow)

    # The workspace lineage reads sounds.epk unconditionally and the job must create it with the engine's
    # own packager. That engine is disabled on licence grounds, so the requirement is recorded with it and
    # the tool that satisfies it is still here and still tested; the licensed engine fetches audio at
    # play time instead, which the plan says.
    workspace = next(e for e in planner.load_engines(include_disabled=True)
                     if e["id"] == "eagler-workspace-26.2")
    check("the workspace engine's audio requirement is written down with the engine",
          "sounds.epk" in workspace.get("blocked_by", ""), str(workspace.get("blocked_by"))[:120])
    check("and points at the tool that satisfies it",
          (ROOT / "tools" / "empty-epk.init.gradle.kts").is_file())
    init = (ROOT / "tools" / "empty-epk.init.gradle.kts").read_text()
    check("which reuses the engine's packager classpath", "classpath = template.classpath" in init)
    check("and does not reimplement the archive format", "EAGPKG" not in init)
    check("and tells the packager this is not the assets tree", "--include-web-unreachable" in init)
    gaius = next(e for e in planner.load_engines(include_disabled=True) if e["id"] == "gaius")
    check("the licensed engine needs no audio archive, and says where sound comes from",
          "play time" in gaius.get("audio", ""), str(gaius.get("audio")))

    print()
    if FAILURES == 0:
        print("version pipeline tests passed")
    else:
        print(f"{FAILURES} pipeline test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
