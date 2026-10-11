#!/usr/bin/env python3
"""Tests for tools/serve_builds.py — the local launcher page over the builds on disk."""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import serve_builds  # noqa: E402

FAILURES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition or not detail else f" — {detail}"))
    if not condition:
        FAILURES += 1


def main() -> int:
    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-serve-"))
    try:
        root = work / "builds-modded"
        (root / "26.2").mkdir(parents=True)
        (root / "1.8.8").mkdir(parents=True)
        (root / "26.2" / "Eaglercraft 26.2 Zeus.modded.html").write_text("<html>a build</html>")
        (root / "1.8.8" / "Eaglercraft 1.8.8.modded.html").write_text("<html>b</html>")
        (root / "1.8.8" / "notes.txt").write_text("not a build")
        (root / "index.html").write_text("the launcher's own page")

        print("finding builds")
        builds = serve_builds.find_builds(root)
        check("every build is found", len(builds) == 2, str([b["file"] for b in builds]))
        check("the launcher page is not listed as a build", all("index" not in b["file"] for b in builds))
        check("non-html files are ignored", all(b["file"].endswith(".html") for b in builds))
        check("the modded marker is noticed", all(b["modded"] for b in builds))
        check("spaces in names are encoded for the url", any("%20" in b["file"] for b in builds),
              str([b["file"] for b in builds]))

        print("\nthe page")
        html = serve_builds.page(root, "http://127.0.0.1:8099")
        check("it counts the builds", "2 build(s)" in html, html[:200])
        check("it links each one", html.count('class="play"') == 2, str(html.count('class="play"')))
        check("the links point at this server", html.count("http://127.0.0.1:8099/") >= 3)
        check("it says the mod API is in them", "mod API injected" in html)
        check("it explains how to load a mod", "?mod=" in html and "mods/my-mod.js" in html)
        check("and shows a mod's shape", "WebMCModAPI.on" in html and "registerKeybind" in html)
        check("the biggest build is first", html.index("26.2") < html.index("1.8.8"), "order")
        check("the page is html", html.lstrip().startswith("<!DOCTYPE html>"))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILURES == 0:
        print("serve_builds tests passed")
    else:
        print(f"{FAILURES} serve_builds test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
