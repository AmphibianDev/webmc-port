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
    check("the fixture lists versions", len(version_tools.versions(manifest)) == 8)
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
    check("a classic version with an EaglercraftX client is reused",
          rows["b1.8.1"]["engine"] == "eagler-ports")

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
    check("an old version with a prebuilt client is reused", rows["b1.8.1"]["verdict"] == "reuse",
          rows["b1.8.1"]["why"])
    check("and points at that client", "Eaglercraft_Offline_WASM.html" in (planner.engine_links(engines).get("b1.8.1") or ""))

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
        check("it recorded every version it saw", len(saved["seen"]) == 8)
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

    print("\nthe map of the idea")
    code, output = run("tools/plan.py", "--manifest", str(FIXTURE), "--kinds", "release,snapshot,old_beta",
                       "--limit", "20")
    check("plan.py renders a table", code == 0 and "verdict" in output and "🧱" in output, output[-200:])
    check("the report counts the verdicts", "needs-port:" in output, output[-200:])

    print()
    if FAILURES == 0:
        print("version pipeline tests passed")
    else:
        print(f"{FAILURES} pipeline test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
