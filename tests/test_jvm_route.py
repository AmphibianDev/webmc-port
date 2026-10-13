#!/usr/bin/env python3
"""Tests for the JVM classifier: which versions a browser JVM can really run.

    python3 tests/test_jvm_route.py

Offline: the version documents here are written by hand from the shapes Mojang actually serves, so
the verdicts are checked without asking Mojang anything.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import jvm_route  # noqa: E402
from update import catalog_entry  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


def document(identifier: str, kind: str, java, lwjgl: str, main: str = "net.minecraft.client.main.Main") -> dict:
    return {
        "id": identifier, "type": kind, "mainClass": main,
        "javaVersion": {"majorVersion": java} if java else {},
        "assetIndex": {"id": identifier},
        "downloads": {"client": {"url": f"https://piston-data.mojang.com/v1/objects/x/{identifier}.jar",
                                 "sha1": "0" * 40, "size": 9_000_000}},
        "libraries": [{"name": f"org.lwjgl.lwjgl:lwjgl:{lwjgl}", "natives": {}}] if lwjgl.startswith("2.")
                     else [{"name": f"org.lwjgl:lwjgl:{lwjgl}"}],
    }


def main() -> int:
    print("what the metadata says about the JVM")
    checks = {
        "1.12.2": (document("1.12.2", "release", 8, "2.9.4-nightly-20150209"), "cheerpj"),
        "1.8.9": (document("1.8.9", "release", 8, "2.9.4-nightly-20150209"), "cheerpj"),
        "1.5.2": (document("1.5.2", "release", 8, "2.9.0"), "cheerpj"),
        "snapshot 17w47a": (document("17w47a", "snapshot", 8, "2.9.4-nightly-20150209"), "cheerpj"),
        "1.13": (document("1.13", "release", 8, "3.1.6"), "needs-lwjgl3"),
        "1.20.4": (document("1.20.4", "release", 8, "3.3.2"), "needs-lwjgl3"),
        "1.20.6 (needs Java 21)": (document("1.20.6", "release", 21, "3.3.3"), "needs-jvm"),
        "26.2 (needs Java 25)": (document("26.2", "release", 25, "3.3.3"), "needs-jvm"),
        "b1.7.3 (pre-shader)": (document("b1.7.3", "old_beta", 8, "2.9.0", "net.minecraft.client.Minecraft"), "cheerpj-old"),
        "a1.2.6 (pre-shader)": (document("a1.2.6", "old_alpha", 8, "2.4.2", "net.minecraft.client.Minecraft"), "cheerpj-old"),
    }
    for label, (doc, expected) in checks.items():
        verdict = jvm_route.classify(doc)
        check(f"{label} -> {expected}", verdict["route"] == expected,
              f"{verdict['route']}: {verdict['why']}")

    print()
    print("an older Minecraft than the browser JVM's natives is an attempt, not a promise")
    old = jvm_route.classify(document("1.5.2", "release", 8, "2.9.0"))
    check("2.9.x is stated as runnable", old["route"] == "cheerpj")
    older = jvm_route.classify(document("1.5.2", "release", 8, "2.8.5"))
    check("older than 2.9 says it is an attempt", older["route"] == "cheerpj-old"
          and "attempt" in older["why"], older["why"])
    pre = jvm_route.classify(document("b1.7.3", "old_beta", 8, "2.9.0", "net.minecraft.client.Minecraft"))
    check("alpha and beta name fixed-function OpenGL", "fixed-function" in pre["why"], pre["why"])
    java_wall = jvm_route.classify(document("1.20.6", "release", 21, "3.3.3"))
    check("the Java wall is reported before the LWJGL one", "Java 21" in java_wall["why"], java_wall["why"])

    print()
    print("the refresh: only plausible versions, only once each")
    manifest = {"versions": [
        {"id": "1.12.2", "type": "release", "releaseTime": "2017-09-18T11:00:00+00:00", "url": "u1"},
        {"id": "26.2", "type": "release", "releaseTime": "2026-06-16T12:00:00+00:00", "url": "u2"},
        {"id": "b1.7.3", "type": "old_beta", "releaseTime": "2011-07-08T00:00:00+00:00", "url": "u3"},
    ]}
    asked: list[str] = []

    def fake_fetch(url: str):
        asked.append(url)
        if url == "u3":  # the beta's own document, which is what decides its route
            return document("b1.7.3", "old_beta", 8, "2.9.0", "net.minecraft.client.Minecraft")
        return document("1.12.2", "release", 8, "2.9.4-nightly-20150209")

    state: dict = {}
    table = jvm_route.refresh(manifest, state, limit=50, fetch=fake_fetch, log=lambda *_: None)
    check("the LWJGL 2 era release and the beta are worth a request", asked == ["u1", "u3"], str(asked))
    check("the modern version is not asked about", "26.2" not in table)
    check("an alpha or beta is asked about, because its jar url is in the document", "b1.7.3" in table)
    check("and its route comes from the type, not the date", table["b1.7.3"]["route"] == "cheerpj-old")
    check("the verdict records the jar it would run",
          table["1.12.2"]["jar_url"].endswith("1.12.2.jar") and table["1.12.2"]["asset_index"] == "1.12.2")
    again = jvm_route.refresh(manifest, state, limit=50, fetch=fake_fetch, log=lambda *_: None)
    check("a second refresh asks nothing again", len(asked) == 2, str(asked))
    check("and keeps what it already knew", again["1.12.2"]["route"] == "cheerpj")

    print()
    print("what the launcher gets: a launch entry, not a queue entry")
    entry = catalog_entry(
        {"id": "1.12.2", "type": "release", "released": "2017-09-18", "verdict": "needs-port",
         "engine": None, "engine_name": None, "minutes": None, "mods": None},
        engines=[], links={}, client_urls=None, jvm=table, previous=None, strict_urls=True, ports={})
    check("the verdict becomes launch", entry["verdict"] == "launch", entry["verdict"])
    check("the jar comes from Mojang", entry["jar_url"].startswith("https://piston-data.mojang.com/"))
    check("it is marked as Mojang\'s url, not test data", entry["jar_url_source"] == "mojang")
    check("the real main class is kept", entry["main_class"] == "net.minecraft.client.main.Main")
    check("and the page is told why this version can run at all",
          entry["jvm_route"] == "cheerpj" and "browser JVM can run" in entry["jvm_why"])
    check("we still host nothing for it", entry["url"] is None)
    check("and the entry says whether its libraries are deployed",
          "libs_ready" in entry, str(entry.get("libs_ready")))

    blocked = catalog_entry(
        {"id": "1.16.5", "type": "release", "released": "2021-01-15", "verdict": "needs-port",
         "engine": None, "engine_name": None, "minutes": None, "mods": None},
        engines=[], links={}, client_urls=None,
        jvm={"1.16.5": jvm_route.classify(document("1.16.5", "release", 8, "3.2.1"))},
        previous=None, strict_urls=True, ports={})
    check("a blocked version stays unplayable", blocked["verdict"] == "needs-port")
    check("but carries the reason in Mojang\'s terms", "LWJGL 3" in (blocked.get("jvm_why") or ""))
    check("and has no jar url to offer", not blocked.get("jar_url"))

    print()
    if FAILURES == 0:
        print("jvm route tests passed")
    else:
        print(f"{FAILURES} jvm route test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
