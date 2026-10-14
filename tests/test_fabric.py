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

    print("\nthe fixture the launcher reads")
    fixture = json.loads((ROOT / "fixtures" / "fabric.json").read_text())
    check("it has a supported list", len(fixture.get("supported", [])) > 100, str(len(fixture.get("supported", []))))
    check("it names a newest loader", bool(fixture.get("newest_loader")), str(fixture.get("newest_loader")))
    check("it knows the newest Minecraft is supported", "26.2" in fixture["supported"])
    check("and that Fabric never existed for 1.8.9", "1.8.9" not in fixture["supported"])
    check("it records where the data came from", "meta.fabricmc.net" in fixture.get("source", {}).get("game", ""))

    print("\nthe other Fabric: Legacy Fabric, which is what 1.8.9 - 1.12.2 have")
    legacy_games = [{"version": "1.12.2", "stable": True}, {"version": "1.8.9", "stable": True}]

    def legacy_fetch(url: str):
        if url.endswith("/versions/game"):
            return legacy_games
        if url.endswith("/versions/loader"):
            return LOADERS
        raise AssertionError(f"unexpected fetch: {url}")

    legacy = fabric.build(fetch=legacy_fetch, meta=fabric.LEGACY_META, maven=fabric.LEGACY_MAVEN,
                          first_supported="1.3")
    check("the same two calls describe the legacy catalogue", len(legacy["supported"]) == 2)
    check("and it knows 1.12.2, which modern Fabric does not", "1.12.2" in legacy["supported"])
    check("its maven is named in the file, so a plan can point at it", "legacyfabric" in legacy["maven"])
    legacy_calls: list[str] = []
    fabric.build(fetch=lambda url: (legacy_calls.append(url), legacy_fetch(url))[1],
                 meta=fabric.LEGACY_META, maven=fabric.LEGACY_MAVEN, first_supported="1.3")
    check("the two calls go to the legacy meta, not the modern one",
          len(legacy_calls) == 2 and all("meta.legacyfabric.net" in url for url in legacy_calls),
          str(legacy_calls))

    print()
    print("the launcher offers Fabric through the plan, not by guessing")
    page = (ROOT / "site" / "index.html").read_text()
    check("the launcher reads the pipeline's index", 'fetch("launch.json")' in page)
    check("and does not fetch Fabric's meta itself: the plan already knows",
          'fetch("fabric.json")' not in page)
    check("it can offer vanilla and Fabric per version", "chosenMode" in page and "modesOf" in page)
    check("and it carries the choice into the play link", "&mode=" in page and "&mods=" in page)
    check("the data is still deployed, so the plan can be rebuilt offline",
          (ROOT / "site" / "fabric.json").is_file())
    check("and the tool refreshes both lists", "legacy_out" in (ROOT / "tools" / "fabric.py").read_text())

    print()
    if FAILURES == 0:
        print("fabric tests passed")
    else:
        print(f"{FAILURES} fabric test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
