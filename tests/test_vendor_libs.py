#!/usr/bin/env python3
"""Tests for vendoring a version's libraries and for the link checker.

    python3 tests/test_vendor_libs.py

Offline: the version document is a committed fixture, downloads are faked, and the link checker is
given a probe that lies on purpose.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_links  # noqa: E402
import vendor_libs  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


import hashlib  # noqa: E402

DOCUMENT = json.loads((ROOT / "fixtures" / "version-1.12.2.json").read_text())
META_URL = "https://example.invalid/1.12.2.json"
MANIFEST = {"versions": [{"id": "1.12.2", "type": "release", "releaseTime": "2017-09-18T00:00:00+00:00",
                          "url": META_URL}]}


def fake_bytes(url: str) -> bytes:
    return b"jar:" + url.encode()


def fake_fetch(url: str) -> bytes:
    """The document, and library bytes that really do match the sha1 it publishes."""
    if url == META_URL:
        document = json.loads(json.dumps(DOCUMENT))
        for library in document["libraries"]:
            artifact = (library.get("downloads") or {}).get("artifact")
            if artifact and artifact.get("url"):
                artifact["sha1"] = hashlib.sha1(fake_bytes(artifact["url"])).hexdigest()
        return json.dumps(document).encode()
    return fake_bytes(url)


def main() -> int:
    print("a download that does not match the published sha1 is refused")
    def bad_fetch(url: str) -> bytes:
        if url == META_URL:
            return json.dumps(DOCUMENT).encode()  # real sha1s, fake bytes
        return b"not the published bytes"
    try:
        with tempfile.TemporaryDirectory() as scratch:
            vendor_libs.vendor("1.12.2", pathlib.Path(scratch) / "libs", manifest=MANIFEST,
                               fetch_fn=bad_fetch, log=lambda *_: None)
        check("a corrupted jar is refused", False, "no error raised")
    except ValueError as error:
        check("a corrupted jar is refused", "sha1" in str(error), str(error)[:60])

    print()
    print("the classpath comes from the version's own metadata")
    jars = vendor_libs.needed_jars(DOCUMENT)
    check("it reads the declared libraries", len(jars) >= 8, str(len(jars)))
    check("it keeps the metadata order", jars[0]["name"] == DOCUMENT["libraries"][0]["name"], jars[0]["name"])
    check("native jars are left to the browser JVM",
          all("natives" not in jar["name"] for jar in jars))
    check("each jar knows its file name", all(jar["file"].endswith(".jar") for jar in jars))

    with tempfile.TemporaryDirectory() as scratch:
        out = pathlib.Path(scratch) / "libs"
        index = vendor_libs.vendor("1.12.2", out, manifest=MANIFEST, fetch_fn=fake_fetch, log=lambda *_: None)
        check("the classpath lists every jar", len(index["classpath"]) == len(jars), str(len(index["classpath"])))
        check("the files are on disk", all((out.parent / path).is_file() for path in index["classpath"]))
        check("the main class comes from the document", index["main_class"] == DOCUMENT["mainClass"])
        check("the asset index is recorded", index["asset_index"] == DOCUMENT["assetIndex"]["id"])
        check("it writes an index the page can read", (out / "1.12.2.json").is_file())
        again = vendor_libs.vendor("1.12.2", out, manifest=MANIFEST, fetch_fn=fake_fetch, log=lambda *_: None)
        check("a second run reuses the files", len(again["classpath"]) == len(index["classpath"]))

    print()
    print("the link checker: a raw file is a download, not a game")
    raw = "https://github.com/someone/port/raw/HEAD/client.html"
    check("it can guess the Pages deployment for a raw link",
          check_links.pages_url(raw) == "https://someone.github.io/port/", str(check_links.pages_url(raw)))
    check("html counts as playable", check_links.is_playable({"ok": True, "content_type": "text/html"}))
    check("text/plain does not", not check_links.is_playable({"ok": True, "content_type": "text/plain"}))
    check("nor does a 404", not check_links.is_playable({"ok": False, "status": 404, "content_type": ""}))

    def lying_probe(url: str) -> dict:
        if url.startswith("https://someone.github.io"):
            return {"status": 200, "content_type": "text/html", "bytes": 100, "ok": True}
        return {"status": 200, "content_type": "text/plain", "bytes": 75576620, "ok": True}

    with tempfile.TemporaryDirectory() as scratch:
        path = pathlib.Path(scratch) / "builds.json"
        path.write_text(json.dumps({"versions": [
            {"version": "26.3", "files": [{"role": "client", "url": raw}]}]}))
        result = check_links.fix_builds(path, probe_fn=lying_probe, log=lambda *_: None)
        fixed = json.loads(path.read_text())["versions"][0]["files"][0]
        check("a live Pages deployment replaces the raw file", result["changed"] == 1 and fixed["url"] ==
              "https://someone.github.io/port/", fixed["url"])
        check("the raw url is kept as a fallback", fixed["fallback_url"] == raw)
        check("and the entry says it is served as html", fixed["served_as"] == "html")

        raw_only = pathlib.Path(scratch) / "raw.json"
        raw_only.write_text(json.dumps({"versions": [
            {"version": "26.2", "files": [{"role": "client", "url": raw}]}]}))

        def download_only(url: str) -> dict:
            return {"status": 200, "content_type": "text/plain", "bytes": 1, "ok": True}

        check_links.fix_builds(raw_only, probe_fn=download_only, log=lambda *_: None)
        marked = json.loads(raw_only.read_text())["versions"][0]["files"][0]
        check("without a deployment it is marked as a download", marked["served_as"] == "download")

    print()
    print("the launch page and the launcher agree on this")
    page = (ROOT / "site" / "play.html").read_text()
    launcher = (ROOT / "site" / "index.html").read_text()
    # The plan names the vendored set (tools/launchplan.py writes libs/<version>.json and checks the
    # file is really there), and the page reads the classpath out of it. Two files, one decision.
    plan_tool = (ROOT / "tools" / "launchplan.py").read_text()
    check("the launch plan points at the version's vendored classpath",
          'f"libs/{version_id}.json"' in plan_tool and '"libs_ready": libs_index.is_file()' in plan_tool)
    check("the launch page reads that classpath and installs every jar in it",
          "launch.libsIndex" in page and "index.classpath" in page and "launch.libs =" in page)
    check("a version whose libraries are not vendored is marked not playable, with the reason",
          'plan["playable"] = bool(client["url"]) and plan["libs_ready"]' in plan_tool
          and "its own libraries are not deployed yet" in plan_tool)
    check("and the page says what a missing classpath costs instead of failing silently",
          "classpath unavailable" in page and "missing class" in page)
    # The metadata url moved to the plan (site/plans/<version>.json): the launcher's link carries the
    # version, the mode and the mods, and the launch page reads the rest from the plan.
    # The launcher's link carries the version and the mode. Mods are the launch page's business now: absent
    # from its query string means "everything the plan offers", which is what a player expects from the mods
    # folder, and an empty value is how you ask for none.
    check("the launcher's play link carries the version and the mode",
          'play.html?version=' in launcher and '&mode=' in launcher)
    check("and the launch page decides the mod set, defaulting to what the plan offers",
          'params.has("mods")' in (ROOT / "site" / "play.html").read_text())
    check("and the plan carries the version's metadata url for the asset index",
          'meta_url' in (ROOT / "tools" / "launchplan.py").read_text())
    check("and warns when a link only downloads", "[data-panel]" in launcher or "Download the client" in launcher,
          "the launcher has to say when a target is a download rather than a page")

    print()
    if FAILURES == 0:
        print("vendor and link tests passed")
    else:
        print(f"{FAILURES} vendor/link test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
