#!/usr/bin/env python3
"""Tests for the boot screen: the shell itself, and the tool that puts it in front of a client.

    python3 tests/test_aot_shell.py

Offline, no browser. The shell is the first thing a player sees and the last thing anybody looks at,
so the properties that make it *vanilla* rather than "a web page" are pinned here: black background,
centred progress text, the game's 200x2 bar, the game's GUI scale, a pixel-crisp shadowed font, no
external requests, and a hand-over that cannot happen before the game is drawing.

The tool's half is a contract with the engines: its edit must be idempotent, must leave the client's
own script alone, must report what it removed (including when it removed nothing), and must refuse a
file that is not an HTML page rather than writing a broken one.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import aot_shell  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


CLIENT = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Minecraft 26.2</title>
<style>body{margin:0}#splash{height:100vh;background:#333}</style></head>
<body>
<div id="splash"><h1>Eaglercraft</h1><p>Loading 26.2&hellip;</p></div>
<div id="boot-overlay" class="hidden"></div>
<div id="boot-screen" aria-hidden="true"></div>
<div id="boot-brand" aria-hidden="true">GAIUS<span>CLIENT</span></div>
<canvas id="game" width="854" height="480"></canvas>
<script>window.clientStarted = true;</script>
</body></html>
"""


def test_shell() -> None:
    print("\nthe boot screen itself")
    shell = aot_shell.shell_text()
    check("it is a real page, not a fragment", shell.lstrip().startswith("<!DOCTYPE html>")
          and "<body>" in shell)
    check("the background is the game's black, not a gradient", "--webmc-bg: #000" in shell)
    check("the progress bar is the size the game draws (200 x 2)",
          "calc(200px * var(--webmc-scale))" in shell and "calc(2px * var(--webmc-scale))" in shell,
          "LoadingOverlay fills a 200x2 bar")
    check("the bar has a track and a fill, and no rounded modern styling",
          ".bar > i" in shell and "border-radius" not in shell)
    check("the text is centred with the game's drop shadow",
          "text-align: center" in shell and "text-shadow" in shell and "1px * var(--webmc-scale)" in shell)
    check("it uses a pixel-crisp monospace stack, not a system UI font",
          "ui-monospace" in shell and "image-rendering: pixelated" in shell)
    check("the GUI scale is computed the way the game computes it",
          "window.innerWidth / 320" in shell and "window.innerHeight / 240" in shell
          and "Math.min(4" in shell, "the game's auto scale: whole multiples of 320x240, 1 to 4")
    check("the creep stops short of the end, as an indeterminate bar should",
          "0.5 - value" in shell, "a bar that reaches 100% before the game is ready is a lie")
    check("only ready() finishes the bar", "api.progress(1)" in shell and "gone" in shell)
    check("it fades out rather than disappearing instantly", "transition: opacity" in shell)
    check("it can be mounted by a wasm X11 runtime", 'id="x11-wasm-root"' in shell)
    check("it requests nothing from the network: no fonts, no images, no CDN",
          "http://" not in shell and "https://" not in shell and "@import" not in shell,
          "a boot screen that waits on a CDN is slower than the thing it covers")
    check("and the dirt texture is the only thing it can be handed",
          "--webmc-dirt" in shell and shell.count("data:") == 0)
    check("the classic dirt screen is available, drawn with CSS",
          "#webmc-boot.dirt" in shell and "repeating-conic-gradient" in shell)
    check("the API is documented where an engine author will look",
          "webmcBoot.progress(" in shell and "webmc:ready" in shell)
    for method in ("progress", "note", "ready", "show"):
        check(f"the api exposes {method}()", f"{method}: function" in shell)

    scripts = re.findall(r"<script>([\s\S]*?)</script>", shell)
    check("it has exactly one script", len(scripts) == 1, str(len(scripts)))
    scratch = pathlib.Path(tempfile.mkdtemp()) / "shell.js"
    scratch.write_text(scripts[0])
    result = subprocess.run(["node", "--check", str(scratch)], capture_output=True, text=True)
    check("that script parses", result.returncode == 0, result.stderr[:200])


def test_check(tmp: pathlib.Path) -> None:
    print("\nreading a client before touching it")
    findings = aot_shell.check(CLIENT, log=lambda *_: None)
    check("it finds the loading UI by id", "#splash" in findings["loading_ui_ids"]
          and "#boot-overlay" in findings["loading_ui_ids"], str(findings["loading_ui_ids"]))
    check("it sees the canvas the game will draw into", findings["has_canvas"])
    check("it knows the shell is not in there yet", not findings["already_has_boot_screen"])
    check("it does not mistake a non-page for a page",
          not aot_shell.check("<html><script>x</script></html>", log=lambda *_: None)["has_body"])


def test_the_engine_name_never_reaches_the_screen() -> None:
    """The shell is JavaScript, so the check is JavaScript: the exact function runs on the exact strings.

    A player read "Starting Gaius Client 1.21.11" on our loading screen, so this is not a hypothetical. The
    function is pulled out of the shell by name and run under node, which is the only way to check the code
    that will run rather than a description of it.
    """
    print("\nthe engine's name on our loading screen")
    shell = (ROOT / "site" / "boot" / "shell.html").read_text()
    names = re.search(r"var ENGINE_NAMES = .*?;", shell, re.S)
    attribution = re.search(r"var ATTRIBUTION = .*?;", shell, re.S)
    function = re.search(r"function clean\(text\) \{.*?\n  \}", shell, re.S)
    check("the shell has a sanitiser for the text the engine reports", bool(names and function))
    if not (names and function and attribution):
        return
    if not shutil.which("node"):
        print("  SKIP  node is not installed")
        return
    script = "\n".join([names.group(0), attribution.group(0), function.group(0), """
    var cases = [["Starting Gaius Client 1.21.11...", "Starting Minecraft Client 1.21.11..."],
                 ["EaglercraftX 1.8.9", "Minecraft 1.8.9"],
                 ["Loading Minecraft 1.21.11", "Loading Minecraft 1.21.11"],
                 ["Gaius is not affiliated with Mojang", ""],
                 ["Eaglercraft is an independent project", ""],
                 ["Compiling eaglerforge patches", "Compiling Minecraft patches"],
                 ["Unpacking 214 MB", "Unpacking 214 MB"]];
    for (var i = 0; i < cases.length; i++) {
      var got = clean(cases[i][0]);
      console.log((got === cases[i][1] ? "PASS " : "FAIL ") + JSON.stringify(cases[i][0])
        + " -> " + JSON.stringify(got));
    }
    """])
    with tempfile.TemporaryDirectory() as tmp:
        probe = pathlib.Path(tmp) / "clean.js"
        probe.write_text(script)
        result = subprocess.run(["node", str(probe)], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        check(line[5:], line.startswith("PASS"), line)
    check("and node actually ran it", bool(result.stdout.strip()), result.stderr[-200:])


def test_inject(tmp: pathlib.Path) -> None:
    print("\nputting the boot screen in front of a client")
    lines: list[str] = []
    html, report = aot_shell.build(CLIENT, "dark", None, ["#splash", "#boot-overlay"], True,
                                   log=lines.append)
    check("the client's splash is gone", 'id="splash"' not in html and "<h1>Eaglercraft</h1>" not in html,
          "the element goes; a leftover CSS rule for it is harmless")
    check("its second overlay is gone too", 'id="boot-overlay"' not in html)
    check("the engine's branding block is removed", 'id="boot-brand"' not in html and "GAIUS" not in html,
          "this is the block that renders GAIUS CLIENT over the loading screen")
    check("and its own boot overlay is hidden rather than removed",
          'id="boot-screen"' in html and "#boot-screen" in html and "display: none !important" in html,
          "the client's script looks that element up; hiding it is the same result without the null")
    check("the boot screen is in, once", html.count('id="webmc-boot"') == 1)
    check("it is inserted after <body>, so it paints first",
          html.index('id="webmc-boot"') > html.index("<body>"))
    check("the client's own script is untouched", "window.clientStarted = true;" in html)
    check("its canvas is still there", 'id="game"' in html)
    check("the style went into the head", html.index("--webmc-bg") < html.index("<body>"))
    check("the hand-over is wired to the first GL draw", "drawElements" in html and "clear" in html)
    check("the engine's own console is hidden as well, because it prints its own name",
          "#status" in html and "#log" in html)
    check("and the two overlays a player actually sees in game are hidden: the status panel and the perf HUD",
          "#perf-hud" in html and "display: none !important" in html,
          "both are fixed, both are visible by default, and both are the engine's, not ours")
    check("its branding block is hidden by our own stylesheet, so the page has one loading screen",
          "#boot-screen" in html and "[data-gaius-shell]" in html and "display: none !important" in html,
          "an element the client's script looks for is hidden rather than removed: a null there is a "
          + "client that stops")
    check("it reports what it removed", any("removed the element with #splash" in line for line in lines),
          str(lines))
    check("and counts the bytes both ways", report["bytes_after"] > report["bytes_before"]
          and report["injected"] is True, str(report))

    print("\nthe edit is idempotent, and honest when there is nothing to remove")
    twice, second = aot_shell.build(html, "dark", None, ["#splash"], True, log=lambda *_: None)
    check("running it again does not add a second boot screen",
          twice.count('id="webmc-boot"') == 1 and second["injected"] is False)
    _, missing = aot_shell.build(CLIENT, "dark", None, ["#not-here"], True, log=lambda *_: None)
    check("its canvas is still there", 'id="game"' in html)
    check("an id that is not in the client is reported, not silently ignored",
          any("not found" in what for what in missing["removed"]), str(missing["removed"]))
    _, bad = aot_shell.build(CLIENT, "dark", None, ["div.splash"], True, log=lambda *_: None)
    check("a selector it cannot do is refused with the reason",
          any("only #id selectors" in what for what in bad["removed"]), str(bad["removed"]))

    print("\nthe classic dirt screen, and a real texture")
    dirt = tmp / "dirt.png"
    dirt.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pretend")
    html_dirt, _ = aot_shell.build(CLIENT, "dirt", dirt, [], True, log=lambda *_: None)
    check("the overlay asks for the dirt style", 'class="dirt"' in html_dirt)
    check("the texture is embedded, not fetched", "data:image/png;base64," in html_dirt)

    print("\na file that is not a page")
    try:
        aot_shell.build("just some bytes", "dark", None, [], True, log=lambda *_: None)
        check("it refuses rather than writing a broken file", False, "no error raised")
    except SystemExit as error:
        check("it refuses rather than writing a broken file", "<body>" in str(error), str(error))


BRANDED = """<!DOCTYPE html><html><head><title>Eaglercraft 26.2</title></head>
<body>
<div id="eaglercraft-splash"><h1>Eaglercraft</h1><p>Powered by Eaglercraft</p></div>
<div id="boot-overlay"></div>
<div id="boot-screen" aria-hidden="true"></div>
<div id="boot-brand" aria-hidden="true">GAIUS<span>CLIENT</span></div>
<canvas id="game"></canvas>
<script>var EaglercraftVersion = "1.0"; // Eaglercraft runtime</script>
</body></html>
"""


def test_branding(tmp: pathlib.Path) -> None:
    print("\nborrowed branding: reported, stripped from the page, credited in the file")
    hits = aot_shell.brand_hits(BRANDED)
    check("the scan finds it in the title and the headings", hits["titles"], str(hits["titles"]))
    check("and in element ids", hits["ids"] or any("eaglercraft" in i for i in hits["ids"]), str(hits["ids"]))
    check("and in the compiled code, where it cannot be safely rewritten", hits["scripts"],
          str(hits["scripts"][:3]))
    check("plain text has nothing to report", aot_shell.brand_hits("<html><body>Loading...</body></html>")
          == {"ids": [], "titles": [], "scripts": [], "other": []})

    lines: list[str] = []
    stripped, report = aot_shell.strip_brand(BRANDED, log=lines.append)
    check("the title no longer names somebody else's product",
          "Eaglercraft" not in stripped.split("</title>")[0], stripped.split("</title>")[0][:80])
    check("and the sentences in the page are not somebody else's either",
          "Powered by Eaglercraft" not in stripped and "Eaglercraft</h1>" not in stripped)
    check("but the id is removed rather than left empty-handed",
          "eaglercraft-splash" not in stripped, "the element should be gone")
    check("the client's own script is untouched: rewriting it would break the client",
          'var EaglercraftVersion = "1.0";' in stripped)
    check("and the run says what it could not do", report["left_in_code"] >= 1, str(report))
    check("with a line for the log", any("credited in CREDITS.md" in line for line in lines), str(lines))

    out = tmp / "stripped.html"
    code = aot_shell.main(["--client", str(tmp / "branded.html"), "--out", str(out), "--remove", "#boot-overlay"])
    check("the command strips branding by default", code == 0 and out.is_file())
    if out.is_file():
        check("and the written file has none of it on the page",
              "Eaglercraft</h1>" not in out.read_text(), out.read_text()[:120])
        check("while the credit ledger is where attribution lives",
              (ROOT / "CREDITS.md").is_file()
              and "Eaglercraft" in (ROOT / "CREDITS.md").read_text())
    check("--no-strip-brand keeps it, for a build that must not be altered",
          aot_shell.main(["--client", str(tmp / "branded.html"), "--out", str(out),
                          "--no-strip-brand"]) == 0
          and "Eaglercraft</h1>" in out.read_text())
    branded = tmp / "branded.html"
    branded.write_text(BRANDED)


def test_shell_template(tmp: pathlib.Path) -> None:
    print("\nthe same file as a wasm-JVM page shell")
    out = tmp / "shell.html"
    code = aot_shell.main(["--shell-template", "--out", str(out), "--dirt", str(tmp / "dirt.png")])
    check("it exits zero", code == 0, str(code))
    text = out.read_text()
    check("the shell is written whole, with the X11 mount point", 'id="x11-wasm-root"' in text)
    check("nothing is injected: this is a template, and the runtime fills it", "webmc-boot" in text
          and "Injected by" not in text)
    check("the dirt texture is in the template too", "data:image/png;base64," in text)


def test_command_line(tmp: pathlib.Path) -> None:
    print("\nthe command")
    client = tmp / "client.html"
    client.write_text(CLIENT)
    out = tmp / "out.html"
    code = aot_shell.main(["--client", str(client), "--out", str(out),
                           "--remove", "#splash,#boot-overlay"])
    check("it exits zero", code == 0, str(code))
    check("it writes the client with the boot screen", 'id="webmc-boot"' in out.read_text())
    check("check mode changes nothing",
          aot_shell.main(["--client", str(client), "--check"]) == 0
          and 'id="webmc-boot"' not in client.read_text())
    check("a missing client is an error, not an empty file",
          aot_shell.main(["--client", str(tmp / "nope.html"), "--out", str(out)]) == 2)


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        tmp = pathlib.Path(scratch)
        test_shell()
        test_the_engine_name_never_reaches_the_screen()
        test_check(tmp)
        test_inject(tmp)
        (tmp / "branded.html").write_text(BRANDED)
        test_branding(tmp)
        test_shell_template(tmp)
        test_command_line(tmp)
    print(f"\n{FAILURES} boot screen test(s) FAILED" if FAILURES else "\nboot screen tests passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
