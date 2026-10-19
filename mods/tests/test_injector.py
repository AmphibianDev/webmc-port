#!/usr/bin/env python3
"""Tests for the mod API injector (mods/injector/inject_mod_api.py).

    python3 mods/tests/test_injector.py

Hermetic: it writes its own little builds - a plain one, one whose JavaScript still names its game
classes, and one that already ships a mod API - and checks what the injector does to each. The
injected script is also parsed by node, because a syntax error there would break the game it was
spliced into.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
MODS = HERE.parent
ROOT = MODS.parent
sys.path.insert(0, str(MODS / "injector"))

import inject_mod_api as injector  # noqa: E402

FAILURES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition or not detail else f" — {detail}"))
    if not condition:
        FAILURES += 1


def node_parses(script: str, work: pathlib.Path, name: str) -> tuple[bool, str]:
    path = work / name
    path.write_text(script)
    finished = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    return finished.returncode == 0, finished.stderr.strip()[:200]


def build_html(body: str, script: str = "") -> str:
    return ("<!DOCTYPE html>\n<html><head><title>Eaglercraft</title></head>\n"
            f"<body><canvas id='game' width='854' height='480'></canvas>\n"
            f"<script>{script}</script>\n{body}</body></html>\n")


def main() -> int:
    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-injector-"))
    try:
        print("what it can tell about a build")
        plain = injector.classify(build_html("", "var x = 1;"))
        check("a plain build is not deep", plain["deep"] is False, plain["kind"])
        named = injector.classify(build_html("", "const game = new net.minecraft.client.Minecraft();\n"
                                                "net.minecraft.client.Minecraft.prototype.runTick = function() {};\n"
                                                "class Minecraft {}\n"))
        check("a build whose JS names its game classes is deep", named["deep"] is True, named["kind"])
        minified = injector.classify(build_html("", "x" * 30_000 + "\n// teavm"))
        check("a minified build is not", minified["deep"] is False, minified["kind"])
        withapi = injector.classify(build_html("", "globalThis.ModAPI = { };"))
        check("an existing mod api is spotted", "ModAPI" in withapi["existing_mod_api"],
              str(withapi["existing_mod_api"]))
        wasm = injector.classify(build_html("", "WebAssembly.instantiate(bytes);"))
        check("a webassembly build is recognised", "wasm" in wasm["kind"] or "webassembly" in wasm["kind"],
              wasm["kind"])

        print("\npatching a plain build")
        target = work / "plain.html"
        target.write_text(build_html("", "var x = 1;"))
        report = injector.patch(target, work / "out", dry_run=False)
        patched = (work / "out" / "plain.modded.html").read_text()
        check("the api is in the output", "WebMCModAPI" in patched and "ModAPI" in patched)
        check("it is marked, so a second run can replace it", "<!DOCTYPE html>" in patched)
        check("it went in early (after head)", report["api_placed_after"] == "<head")
        check("the mod loader is there", 'params.getAll("mod")' in patched)
        check("no deep hooks were placed on a plain build", report["hooks_placed"] == 0)
        injected = re.findall(r"<!-- WebMC mod API \(injected\) -->\s*<script>([\s\S]*?)</script>", patched)
        check("exactly one api block was spliced in", len(injected) == 1, str(len(injected)))
        if injected:
            ok, error = node_parses(injected[0], work, "injected.js")
            check("the injected script parses", ok, error)

        print("\nrunning it twice")
        second = injector.patch(work / "out" / "plain.modded.html", work / "out2", dry_run=False)
        twice = (work / "out2" / "plain.modded.modded.html").read_text()
        check("a patched build is not patched twice", twice.count("<!-- WebMC mod API (injected) -->") == 1,
              str(twice.count("<!-- WebMC mod API (injected) -->")))
        check("and it says it replaced the old one", second.get("replaced_previous_patch") is True)

        print("\npatching a build with named game classes")
        deep_target = work / "deep.html"
        deep_target.write_text(build_html(
            "", "class Minecraft { constructor() { this.running = true; } }\n"
                "const game = new net.minecraft.client.Minecraft();\n"
                "net.minecraft.client.Minecraft.prototype.runTick = function() { return 1; };\n"
                "net.minecraft.client.Minecraft.prototype.render = function() {};\n"))
        deep_report = injector.patch(deep_target, work / "out3", dry_run=False)
        deep_patched = (work / "out3" / "deep.modded.html").read_text()
        check("hooks were placed", deep_report["hooks_placed"] >= 1, json.dumps(deep_report["hooks"])[:200])
        check("and they emit into the api", "WebMCModAPI.emit(" in deep_patched)
        check("while the client's own code is still there", "this.running = true" in deep_patched)

        print("\npatching a build that already has a mod api")
        api_target = work / "with-api.html"
        api_target.write_text(build_html("", "globalThis.ModAPI = { displayToChat: function (m) {} };\n"
                                             "// EaglerForge\n"))
        api_report = injector.patch(api_target, work / "out4", dry_run=False)
        api_patched = (work / "out4" / "with-api.modded.html").read_text()
        check("the existing api is left alone in the source",
              "globalThis.ModAPI = { displayToChat" in api_patched)
        check("our api merges instead of replacing", "merged with it instead of replacing it" in api_patched)
        check("and it keeps its own name", "window.WebMCModAPI = ModAPI" in api_patched)
        check("the classifier reported the existing api", "ModAPI" in api_report["existing_mod_api"])

        print("\nthe command line")
        many = work / "many"
        many.mkdir()
        (many / "a.html").write_text(build_html("", "var a = 1;"))
        (many / "b.html").write_text(build_html("", "var b = 2;"))
        finished = subprocess.run([sys.executable, str(MODS / "injector" / "inject_mod_api.py"),
                                   "--all", "--builds-dir", str(many), "--out", str(work / "out5"),
                                   "--dry-run"], capture_output=True, text=True)
        check("--all works over a folder", finished.returncode == 0 and "2 build(s)" in finished.stdout,
              finished.stdout[:200])
        check("a dry run writes nothing", not (work / "out5").exists())
        finished = subprocess.run([sys.executable, str(MODS / "injector" / "inject_mod_api.py"),
                                   "--builds-dir", str(work / "nothing"), "--all"],
                                  capture_output=True, text=True)
        check("an empty folder is reported, not crashed on", finished.returncode == 2
              and "fetch_builds" in finished.stdout, finished.stdout[-160:])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILURES == 0:
        print("injector tests passed")
    else:
        print(f"{FAILURES} injector test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
