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
    check("a version with an engine profile is buildable", rows["26.2"]["verdict"] == "build", rows["26.2"]["why"])
    check("and names the Eaglercraft workspace engine", rows["26.2"]["engine"] == "eagler-workspace-26.2",
          str(rows["26.2"]["engine"]))
    check("a buildable entry says what it costs",
          rows["26.2"]["minutes"] and rows["26.2"]["ram_gb"] and rows["26.2"]["mods"],
          str({k: rows["26.2"][k] for k in ("minutes", "ram_gb", "mods")}))
    check("a version whose only engine is disabled needs a port", rows["26.3"]["verdict"] == "needs-port",
          rows["26.3"]["why"] + " (engine " + str(rows["26.3"]["engine"]) + ")")
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
    check("a 26.x version is blocked by the Java version too", rows["26.3"]["roadmap"] == "cheerpj-java21")
    check("the roadmap says what it would unlock", bool(rows["26.3"].get("unlocks")))
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
    check("the disabled one is still documented", any(e["id"] == "gaius" and e.get("disabled_why")
                                                      for e in every))
    for engine in enabled:
        has_build = bool((engine.get("build") or {}).get("output")) and bool((engine.get("build") or {}).get("compile"))
        has_release = bool((engine.get("release") or {}).get("asset"))
        has_launch = bool(engine.get("launch_plan") or engine.get("launch"))
        check(f"engine {engine['id']} knows how to produce a client", has_build or has_release or has_launch,
              str(engine.get("build"))[:80])
    check("every enabled engine covers at least one version",
          all(engine.get("profiles") or engine.get("versions") for engine in enabled))
    check("a real version with no profile needs a port", rows["1.21.4"]["verdict"] == "needs-port",
          rows["1.21.4"]["why"])
    check("a fresh snapshot needs a port too", rows["26.3-snapshot-7"]["verdict"] == "needs-port")
    links_any = planner.engine_links(engines)
    check("a prebuilt client is still available as the fallback for classics",
          "Eaglercraft_Offline_WASM.html" in (links_any.get("b1.8.1") or ""), str(sorted(links_any))[:80])
    check("the TeaVM route is still offered where no JVM reaches", rows["26.2"]["verdict"] == "build")

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
        code, output = run("tools/update.py", "--manifest", str(FIXTURE), "--state", str(state),
                           "--kinds", "release,snapshot", "--client-urls",
                           str(ROOT / "fixtures" / "client-urls.json"), "--write")
        check("it runs against the fixture", code == 0, output[-200:])
        check("it wrote a state file", state.is_file())
        check("it wrote the page's manifest", (ROOT / "site" / "manifest.json").is_file())
        saved = json.loads(state.read_text())
        check("it recorded every version it saw", len(saved["seen"]) >= 14, str(len(saved["seen"])))
        check("it queued the versions that need a port",
              {entry["id"] for entry in saved["queued"]} >= {"1.21.4", "26.3-snapshot-7"}, str(saved["queued"]))
        check("it catalogued the buildable ones",
              any(entry["id"] == "26.2" and entry["verdict"] == "build" for entry in saved["catalog"]))
        check("queued entries say why", all(entry.get("why") for entry in saved["queued"]))

        # a refresh with no way to resolve urls must keep the ones already known
        before_urls = {entry["id"]: entry.get("jar_url") for entry in json.loads(state.read_text())["catalog"]
                       if entry.get("verdict") == "launch"}
        run("tools/update.py", "--manifest", str(FIXTURE), "--state", str(state),
            "--kinds", "release,snapshot", "--write")   # deliberately without --client-urls
        after_urls = {entry["id"]: entry.get("jar_url") for entry in json.loads(state.read_text())["catalog"]
                      if entry.get("verdict") == "launch"}
        check("an offline refresh keeps the jar url it already knew",
              before_urls and after_urls == before_urls, f"{before_urls} -> {after_urls}")
        check("and records where that url came from",
              all(entry.get("jar_url_source") in ("fixture", "mojang")
                  for entry in json.loads(state.read_text())["catalog"] if entry.get("jar_url")))
        run("tools/update.py", "--manifest", str(FIXTURE), "--state", str(state), "--kinds",
            "release,snapshot", "--strict-urls", "--write")
        strict = {entry["id"]: entry.get("jar_url") for entry in json.loads(state.read_text())["catalog"]
                  if entry.get("verdict") == "launch"}
        check("a strict run drops test urls instead of publishing them",
              strict and all(value is None for value in strict.values()), str(strict))

        # a second run with nothing new must not add anything
        before = json.loads(state.read_text())
        code, output = run("tools/update.py", "--manifest", str(FIXTURE), "--state", str(state),
                           "--kinds", "release", "--write")
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
                           "--client-urls", str(ROOT / "fixtures" / "client-urls.json"), "--write")
        check("a new release is noticed", "27.0" in output and code == 0)
        final = json.loads(state.read_text())
        check("and queued for a port", any(entry["id"] == "27.0" for entry in final["queued"]))

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

    print("\nthe build list")
    builds_doc = _json.loads((ROOT / "builds.json").read_text())
    builds = {group["version"]: group["files"] for group in builds_doc["versions"]}
    wanted_versions = ("26.3", "26.2", "1.20.6", "1.16.5", "1.14.4", "1.13.1", "1.12.2",
                       "1.8.8", "1.5.2", "1.2.5", "b1.8.1", "a1.2.6", "in-20100223")
    check("every version with a build on GitHub is listed",
          all(version in builds for version in wanted_versions),
          str([v for v in wanted_versions if v not in builds]))
    check("the versions with no verifiable build are absent, not pretended",
          not {"1.18.2", "1.21.5", "1.21.11"} & set(builds), str(sorted(builds)))
    entries = [entry for group in builds.values() for entry in group]
    check("every entry is a GitHub address", all("github.com" in entry["url"] for entry in entries))
    check("every file says what it is for", all(entry.get("role") for entry in entries))
    # Sizes come from the repositories that store their builds as files (the mirrors). A release
    # asset's size is not known until it is fetched, and one of them is a three-file bundle, so the
    # honest assertion is "most of them", not "all of them".
    with_size = sum(1 for entry in entries if entry.get("bytes"))
    # Exactly the release assets (EaglerPorts' seven) have no size until fetched; every file in a
    # repository does. So this asserts the split rather than a number that drifts.
    unknown = [e["url"] for e in entries if not e.get("bytes")]
    check("only release assets lack a known size",
          all("/releases/" in url for url in unknown) and with_size >= 17,
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
    check("a 26.x version is blocked by Java, not by us", states.get("26.2") == "ahead-of-time",
          str(states.get("26.2")))
    check("and 1.21.11 is blocked outright", states.get("1.21.11") == "blocked")
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
    workflow = (ROOT / ".github" / "workflows" / "build-client.yml").read_text()
    build = workflow[workflow.index("Compile, link and package"):]
    publish = build[build.index("Publish the client as a launcher card"):]
    check("the build step fails on a non-zero toolchain status", 'if [ "$STATUS" != "0" ]' in build)
    check("and exits non-zero rather than noting it",
          "exit 1" in build.split('if [ "$STATUS" != "0" ]')[1])
    check("publishing nothing fails the job",
          'echo "::error::the toolchain produced no client' in publish)

    # sounds.epk is documented by the engine as externally supplied; the job must create it, and
    # must do so by reusing their packager rather than reimplementing the archive format.
    check("the job creates the audio archive the packager reads unconditionally",
          "Pack the audio archives the engine needs" in workflow)
    init = (ROOT / "tools" / "empty-epk.init.gradle.kts").read_text()
    check("it reuses the engine's packager classpath", "classpath = template.classpath" in init)
    check("it does not reimplement the archive format", "EAGPKG" not in init)
    check("it tells the packager this is not the assets tree",
          "--include-web-unreachable" in init)
    check("and feeds the optional music-pack export a name it accepts",
          "assets/minecraft/sounds/music/" in workflow)
    catalog = (ROOT / ".github" / "workflows" / "catalog.yml").read_text()
    check("the long compile is not in the push-triggered workflow", "Compile, link and package" not in catalog)
    check("and a push can no longer cancel a build", "cancel-in-progress: true" in catalog
          and "cancel-in-progress: false" in workflow)
    check("and it records why the archive is empty", "separately authorized" in init)

    print()
    if FAILURES == 0:
        print("version pipeline tests passed")
    else:
        print(f"{FAILURES} pipeline test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
