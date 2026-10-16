#!/usr/bin/env python3
"""Tests for tools/debrand.py - the pass that takes the engine's chrome off a client page.

    python3 tests/test_debrand.py

Hermetic and offline: the pages it works on are built here, with the shapes a compiled engine client
actually has - a header and a footer that are only branding, a boot overlay whose id the engine's own
script looks up, a status console and a performance readout, a `<title>` that names somebody else's
product, and the compiled code itself (which must not be touched).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import debrand  # noqa: E402

FAILURES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition or not detail else f" — {detail}"))
    if not condition:
        FAILURES += 1


# The engine's own script: it looks up its boot overlay by id while it boots, which is what makes that one
# element wired and the header and footer not.
ENGINE_SCRIPT = """
  var screen = document.getElementById("boot-screen");
  screen.style.display = "block";
  var console = document.getElementById("status");
  console.textContent = "Gaius client 26.2";
  var hud = document.getElementById("perf-hud");
  hud.textContent = "60 fps";
"""

COMPILED_BLOB = ("var eaglercraftx = { brand: 'Gaius', banner: 'Eaglercraft 26.2' };\n"
                 + "x" * 400 + "\n")


def engine_page() -> str:
    """A page with the engine's chrome on it, exactly as a compiled client ships it."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Gaius Client 26.2</title>
<style>#boot-screen {{ position: fixed; }}</style>
</head>
<body>
<div id="gaius-shell-header"><h1>GAIUS CLIENT</h1></div>
<div id="boot-screen">
  <div id="boot-brand"><span>GAIUS CLIENT</span></div>
  <div id="boot-bar"><div id="boot-bar-fill"></div></div>
  <div id="boot-card">Loading…</div>
</div>
<div id="profile-kicker">PROFILE</div>
<div id="profile-subtitle">pick a name</div>
<div id="profile-legal">not affiliated with Mojang Studios</div>
<div id="status"></div>
<div id="perf-hud"></div>
<div id="log"></div>
<div id="gaius-shell-footer"><p>Built with Gaius</p></div>
<canvas id="game" width="854" height="480"></canvas>
<script>{ENGINE_SCRIPT}</script>
<script>{COMPILED_BLOB}</script>
</body>
</html>
"""


def main() -> int:
    html = engine_page()

    print("what it finds, without changing anything")
    findings = debrand.analyse(html)
    actions = {marker["selector"]: marker["action"] for marker in findings["chrome"]}
    check("the brand block is found", "#boot-brand" in actions)
    check("the boot overlay is found", "#boot-screen" in actions)
    check("the status console is found", "#status" in actions)
    check("the performance readout is found", "#perf-hud" in actions)
    check("an element its script looks up is marked wired",
          all(marker["wired"] for marker in findings["chrome"] if marker["selector"] == "#boot-screen"))
    check("and that one is hidden rather than removed", actions["#boot-screen"] == "hide", str(actions))
    check("the header is not wired, so it can go", actions["#gaius-shell-header"] == "remove")
    check("the page is not clean to begin with", findings["already_clean"] is False)
    check("the title that is not ours is reported", "Gaius Client 26.2" in findings["branded_headings"][0]
          if findings["branded_headings"] else False)

    print("\nthe pass itself")
    cleaned, report = debrand.debrand(html, log=lambda *_: None)
    check("the brand block is gone", 'id="boot-brand"' not in cleaned)
    check("the header is gone", 'id="gaius-shell-header"' not in cleaned)
    check("the footer is gone", 'id="gaius-shell-footer"' not in cleaned)
    check("the profile kicker is gone", 'id="profile-kicker"' not in cleaned)
    check("the wired boot overlay is still in the document", 'id="boot-screen"' in cleaned,
          "removing it would be a null the engine's script stops on")
    check("and it is hidden by our stylesheet", 'style id="webmc-debrand"' in cleaned
          and "#boot-screen { display: none !important; }" in cleaned)
    check("hidden means display:none, not opacity", "opacity: 0" not in cleaned.split("webmc-debrand")[1][:400])
    check("the console is hidden rather than removed", 'id="status"' in cleaned and "display: none !important" in cleaned)
    check("the performance readout too", 'id="perf-hud"' in cleaned)
    check("the engine's runtime marker is hidden, so anything it builds later goes with it",
          "[data-gaius-shell] { display: none !important; }" in cleaned)
    check("the title no longer names the engine", "<title></title>" in cleaned)
    check("and neither do the headings", "GAIUS CLIENT" not in cleaned)
    check("the game's own canvas is untouched", 'id="game"' in cleaned)
    check("the compiled code is untouched", "var eaglercraftx" in cleaned and "'Gaius'" in cleaned,
          "a brand token inside compiled code is an identifier: rewriting it breaks the client")
    check("the engine's own script is untouched", 'getElementById("boot-screen")' in cleaned)
    check("the report says what was removed", len(report["removed"]) >= 5, str(len(report["removed"])))
    check("and what was only hidden", len(report["hidden"]) >= 4, str(len(report["hidden"])))
    check("the report is json-serialisable", json.dumps(report) != "")
    check("it says the result is clean", report["clean"] is True)

    print("\nit is idempotent")
    twice, second = debrand.debrand(cleaned, log=lambda *_: None)
    check("running it twice is the same as running it once", twice == cleaned)
    check("the stylesheet is not stacked", twice.count(f'id="{debrand.STYLE_ID}"') == 1)
    check("and the second pass has almost nothing to do",
          len(second["removed"]) == 0 and second["titles_cleared"] == [], json.dumps(second["removed"]))

    print("\nan id belonging to the client's wiring is never removed")
    wired_page = ('<html><head><title>x</title></head><body>'
                  '<div id="eagler26-offline-meta">meta</div>'
                  '<div id="gaius-shell-header">chrome</div>'
                  "<script>var gate = document.getElementById('gaius-shell-header');</script>"
                  "</body></html>")
    wired_clean, wired_report = debrand.debrand(wired_page, log=lambda *_: None)
    check("the client's own meta element survives", 'id="eagler26-offline-meta"' in wired_clean)
    check("a chrome element its script looks up is hidden, not removed",
          'id="gaius-shell-header"' in wired_clean and "#gaius-shell-header { display: none !important; }" in wired_clean)
    check("and the report says why", any(item["selector"] == "#gaius-shell-header" for item in wired_report["hidden"]))

    print("\nbytes, not just text")
    raw = html.encode("utf-8")
    cleaned_bytes, byte_report = debrand.debrand_bytes(raw, log=lambda *_: None)
    check("the bytes are still a page", b"<!DOCTYPE html>" in cleaned_bytes[:64])
    check("and the pass costs a client nothing measurable in size",
          len(cleaned_bytes) - len(raw) < 1024, f"{len(raw)} -> {len(cleaned_bytes)}")
    check("and the report counts the bytes", byte_report["bytes_before"] == len(raw)
          and byte_report["bytes_after"] == len(cleaned_bytes))
    # a client carries bytes that are not valid utf-8 in its compressed payloads; a pass that mangled them
    # would corrupt the game, so the round trip has to be exact
    odd = b"<html><head></head><body>" + bytes(range(128, 256)) + b"</body></html>"
    odd_out, _ = debrand.debrand_bytes(odd, log=lambda *_: None)
    check("bytes that are not utf-8 survive the round trip", bytes(range(128, 256)) in odd_out)

    print("\na page with no engine chrome is left alone")
    plain = "<html><head><title>Minecraft</title></head><body><canvas id='game'></canvas></body></html>"
    plain_out, plain_report = debrand.debrand(plain, log=lambda *_: None)
    check("nothing is removed from it", plain_report["removed"] == [])
    check("nothing is hidden in it", plain_report["css_hidden"] == [], str(plain_report["css_hidden"]))
    check("and it is byte-identical", plain_out == plain)

    print("\nthe command line")
    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-debrand-"))
    try:
        target = work / "client.html"
        target.write_text(html)
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "debrand.py"), "--client", str(target),
                               "--check"], capture_output=True, text=True)
        check("--check exits 0", done.returncode == 0, done.stderr[:200])
        check("--check names the brand block", "#boot-brand" in done.stdout, done.stdout[:200])
        check("--check changes nothing", target.read_text() == html)

        out = work / "clean.html"
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "debrand.py"), "--client", str(target),
                               "--out", str(out)], capture_output=True, text=True)
        check("--out writes a page", done.returncode == 0 and out.is_file(), done.stderr[:200])
        check("and the page it writes is clean", 'id="boot-brand"' not in out.read_text())
        check("the input is untouched", target.read_text() == html)

        done = subprocess.run([sys.executable, str(ROOT / "tools" / "debrand.py"), "--client", str(target),
                               "--json"], capture_output=True, text=True)
        check("--json prints a report", done.returncode == 0 and json.loads(done.stdout)["removed"],
              done.stdout[:120])
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "debrand.py"), "--client", str(target),
                               "--in-place"], capture_output=True, text=True)
        check("--in-place writes the client", done.returncode == 0 and 'id="boot-brand"' not in target.read_text())
    finally:
        for stale in work.iterdir():
            stale.unlink()
        work.rmdir()

    print(failures_statement())
    return 1 if FAILURES else 0


def failures_statement() -> str:
    return "\ndebrand tests passed" if not FAILURES else f"\n{FAILURES} debrand test(s) FAILED"


if __name__ == "__main__":
    sys.exit(main())
