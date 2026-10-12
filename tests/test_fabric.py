#!/usr/bin/env python3
"""Tests for the Fabric column: the meta fetch, the annotation, and the launcher's use of it.

    python3 tests/test_fabric.py

Offline: the API is replaced by a fake, and the committed fixture stands in for a real refresh, so
this proves the mapping without depending on Fabric being up.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fabric  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


GAMES = [
    {"version": "26.2", "stable": True},
    {"version": "1.20.4", "stable": True},
    {"version": "1.20.5-pre1", "stable": False},
    {"version": "1.14", "stable": True},
]
LOADERS = [{"version": "0.19.5"}, {"version": "0.19.4"}]


def fake_fetch(url: str):
    if url.endswith("/versions/game"):
        return GAMES
    if url.endswith("/versions/loader"):
        return LOADERS
    raise AssertionError(f"unexpected fetch: {url}")


def main() -> int:
    print("the meta fetch, twice for the whole catalog")
    fetched: list[str] = []

    def counting(url: str):
        fetched.append(url)
        return fake_fetch(url)

    data = fabric.build(fetch=counting)
    check("two requests cover every version", len(fetched) == 2, str(fetched))
    check("the newest loader is taken first", data["newest_loader"] == "0.19.5", str(data["newest_loader"]))
    check("supported versions come from the API", set(data["supported"]) == {"26.2", "1.20.4", "1.20.5-pre1", "1.14"})
    check("the stable flag is kept", "26.2" in data["stable"] and "1.20.5-pre1" not in data["stable"])
    check("prereleases count as supported", "1.20.5-pre1" in data["supported"])

    print("\none version at a time")
    modern = fabric.annotate("1.20.4", data)
    check("a supported version gets a loader", modern and modern["loader"] == "0.19.5", json.dumps(modern)[:80])
    check("its loader jar is on Fabric's maven",
          "/net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar" in modern["artifacts"]["loader"])
    check("the intermediary matches the game version",
          "/intermediary/1.20.4/intermediary-1.20.4.jar" in modern["artifacts"]["intermediary"])
    check("the profile tells a launcher the rest", modern["artifacts"]["profile"].endswith("/profile/json"))
    check("a version Fabric never supported gets nothing", fabric.annotate("1.8.9", data) is None)
    check("nor does 1.12.2", fabric.annotate("1.12.2", data) is None)
    check("nor a version that does not exist", fabric.annotate("99.9", data) is None)

    print("\nthe committed fixture, which is what the launcher reads")
    fixture = json.loads((ROOT / "fixtures" / "fabric.json").read_text())
    check("it has a supported list", len(fixture.get("supported", [])) > 100, str(len(fixture.get("supported", []))))
    check("it names a newest loader", bool(fixture.get("newest_loader")), str(fixture.get("newest_loader")))
    check("it knows the newest Minecraft is supported", "26.2" in fixture["supported"])
    check("and that Fabric never existed for 1.8.9", "1.8.9" not in fixture["supported"])
    check("it records where the data came from", "meta.fabricmc.net" in fixture.get("source", {}).get("game", ""))

    print("\nthe launcher")
    page = (ROOT / "site" / "index.html").read_text()
    check("it reads the Fabric table", 'fetch("fabric.json")' in page)
    check("it offers a loader and its artefacts", "fabric-loader-" in page and "intermediary-" in page)
    check("it can build a kit in the browser", "buildZip" in page and "crc32" in page)
    check("the kit is built in a form browsers can run", "fetch(fabric.profileUrl)" in page, page.count("profileUrl"))
    check("it explains a version Fabric never had",
          "Fabric has never supported" in page and "first release was for" in page)
    # The honest half: downloading a Fabric kit is not the same as running Fabric, and the page must
    # not blur that. A browser route has either no JVM (an AOT build) or one older than Fabric.
    check("it refuses to promise a Fabric launch in the browser", "needs a JVM" in page)
    check("it says an AOT build has no class loader",
          "class loader" in page and "baked in at build time" in page)
    check("it says a port cannot load Fabric jars either", "cannot load Fabric jars" in page)

    print()
    if FAILURES == 0:
        print("fabric tests passed")
    else:
        print(f"{FAILURES} fabric test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
