#!/usr/bin/env python3
"""Tests for tools/fetch_builds.py — the downloader for the catalogued builds.

    python3 tests/test_fetch_builds.py

Hermetic: the "network" here is file:// URLs pointing at files this test writes itself, so it
proves the download path, the sniffing, the resume-by-size behaviour and the failure handling
without fetching 700 MB from GitHub.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fetch_builds  # noqa: E402

FAILURES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition or not detail else f" — {detail}"))
    if not condition:
        FAILURES += 1


def main() -> int:
    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-builds-"))
    sources = work / "sources"
    sources.mkdir()

    client = sources / "Eaglercraft 26.2.html"
    client.write_text("<!doctype html><html><head><title>Eaglercraft</title></head>"
                      "<body><canvas id='c'></canvas>Minecraft in a tab</body></html>")
    payload = sources / "classes.js"
    payload.write_text("// teavm output\n" + "x" * 5000)
    pack = sources / "assets.epk"
    pack.write_text("EPK1" + "a" * 2000)
    junk = sources / "not-a-client.html"
    junk.write_text("404: Not Found")

    builds = {
        "note": "fixture",
        "versions": [
            {"version": "26.2", "label": "26.2", "era": "newest", "folder": "26.2", "source_repo": "example/repo",
             "files": [{"name": client.name, "url": client.as_uri(), "role": "client",
                        "bytes": len(client.read_bytes())}]},
            {"version": "1.13.1", "label": "1.13.1", "era": "legacy", "folder": "1.13.1",
             "source_repo": "example/repo",
             "files": [{"name": "classes.js", "url": payload.as_uri(), "role": "client"},
                       {"name": "assets.epk", "url": pack.as_uri(), "role": "assets"}]},
            {"version": "broken", "label": "broken", "era": "modern", "folder": "broken",
             "source_repo": "example/repo",
             "files": [{"name": "not-a-client.html", "url": junk.as_uri(), "role": "client"},
                       {"name": "missing.html", "url": (sources / "gone.html").as_uri(), "role": "client"}]},
        ],
    }
    builds_path = work / "builds.json"
    builds_path.write_text(json.dumps(builds))
    dest = work / "builds"

    print("sniffing")
    ok, what = fetch_builds.looks_like_a_client(b"<!doctype html><html>Eaglercraft canvas</html>", "a.html")
    check("an Eaglercraft page is recognised", ok and "Eaglercraft" in what, what)
    ok, what = fetch_builds.looks_like_a_client(b"404: Not Found", "a.html")
    check("an error page is not", not ok, what)
    ok, what = fetch_builds.looks_like_a_client(b"\x00asm\x01\x00\x00\x00", "a.wasm")
    check("a wasm module is", ok and "webassembly" in what, what)
    ok, _ = fetch_builds.looks_like_a_client(b"PK\x03\x04", "a.zip")
    check("a zip is", ok)

    print("\nthe real run (over file://)")
    finished = subprocess.run([sys.executable, str(ROOT / "tools" / "fetch_builds.py"),
                               "--builds", str(builds_path), "--dest", str(dest)],
                              capture_output=True, text=True)
    check("it exits non-zero when a file fails", finished.returncode == 1, str(finished.returncode))
    check("the client was fetched", (dest / "26.2" / client.name).is_file())
    check("a file set was fetched", (dest / "1.13.1" / "classes.js").is_file()
          and (dest / "1.13.1" / "assets.epk").is_file())
    check("a failed fetch leaves no .part file", not list(dest.rglob("*.part")))

    manifest = json.loads((dest / "manifest.json").read_text())
    check("the manifest counts what happened",
          manifest["summary"]["fetched"] == 3 and manifest["summary"]["failed"] == 1
          and manifest["summary"]["not_a_client"] == 1,
          str(manifest["summary"]))
    check("a file that fetched but is not a client is called out, not counted as good",
          manifest["versions"]["broken"]["files"][0]["state"] == "not-a-client",
          manifest["versions"]["broken"]["files"][0]["state"])
    twenty_six = manifest["versions"]["26.2"]["files"][0]
    check("a fetched file has a size and a hash",
          twenty_six["sha256"] and twenty_six["bytes"] == len(client.read_bytes()), str(twenty_six)[:120])
    check("it recorded what the file looked like", "Eaglercraft" in twenty_six["marker"], twenty_six["marker"])
    check("the junk file is marked as not looking like a client",
          not manifest["versions"]["broken"]["files"][0]["looks_like"],
          str(manifest["versions"]["broken"]["files"][0])[:120])
    check("a missing url is reported, not crashed on",
          manifest["versions"]["broken"]["files"][1]["state"] == "failed")

    print("\nthe second run")
    finished = subprocess.run([sys.executable, str(ROOT / "tools" / "fetch_builds.py"),
                               "--builds", str(builds_path), "--dest", str(dest)],
                              capture_output=True, text=True)
    manifest_again = json.loads((dest / "manifest.json").read_text())
    check("already-present files are not downloaded again",
          manifest_again["summary"]["already_here"] == 1, str(manifest_again["summary"]))

    print("\nthe dry run")
    finished = subprocess.run([sys.executable, str(ROOT / "tools" / "fetch_builds.py"),
                               "--builds", str(builds_path), "--dest", str(work / "dry"), "--dry-run"],
                              capture_output=True, text=True)
    check("a dry run fetches nothing", finished.returncode == 0 and not (work / "dry").exists())
    check("and says what it would do", "would fetch" in finished.stdout or "MB to fetch" in finished.stdout,
          finished.stdout[-160:])

    print("\n--only")
    finished = subprocess.run([sys.executable, str(ROOT / "tools" / "fetch_builds.py"),
                               "--builds", str(builds_path), "--dest", str(work / "one"),
                               "--only", "26.2"], capture_output=True, text=True)
    one = json.loads((work / "one" / "manifest.json").read_text())
    check("one version can be fetched on its own", list(one["versions"]) == ["26.2"], str(list(one["versions"])))

    print()
    if FAILURES == 0:
        print("fetch_builds tests passed")
    else:
        print(f"{FAILURES} fetch_builds test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
