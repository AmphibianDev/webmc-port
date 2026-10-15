#!/usr/bin/env python3
"""A published client checked from the outside: the checks have to catch real breakage, not just pass.

Every check in `tools/verify_client.py` is worth having only if it fails when the thing it names is broken, so this
builds a good page, checks it, then breaks one thing at a time and checks that the verifier says so. The fixture is
made with the real packer, so the file layout under test is the one that gets published.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import pack_client  # noqa: E402
import verify_client  # noqa: E402

FAILURES = 0
CHECKS = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES, CHECKS
    CHECKS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


# what the shell tool leaves behind: our boot screen, and the engine's chrome hidden rather than removed
GOOD_CLIENT = """<!doctype html><html><head><title>Minecraft 1.21.11</title>
<style>/* WebMC: the engine's own launcher chrome is not part of this page */
#boot-screen, [data-gaius-shell] { display: none !important; }</style>
</head><body>
<div id="webmc-boot"><p class="status">Loading...</p><div class="bar"><i></i></div></div>
<div id="boot-screen" aria-hidden="true"></div>
<canvas id="game"></canvas>
</body></html>
"""


# an engine whose own boot screen is kept: it is what shows the wasm unpacking, so ours does not cover it
EAGLER_STYLE = """<!doctype html><html><head><title>Minecraft 1.21.11</title></head><body>
<div id="boot-screen"><div id="boot-card"><h1>Eagler26</h1><div id="boot-bar"><i></i></div></div></div>
</body></html>
"""


def make(tmp: pathlib.Path, html: str, name: str = "good", mods: list[str] | None = None,
         gateway: str | None = None) -> pathlib.Path:
    out = tmp / name
    client = tmp / f"{name}.html"
    client.write_text(html)
    pack_client.pack(client, out, "Minecraft 1.21.11", chunk_mb=1, mods=mods, gateway=gateway, log=lambda *_: None)
    return out


def problems_of(out: pathlib.Path) -> list[str]:
    return verify_client.verify(None, out, log=lambda *_: None)["problems"]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="webmc-verify-") as tmp_name:
        tmp = pathlib.Path(tmp_name)

        print("a page that is right")
        good = make(tmp, GOOD_CLIENT, mods=["hello-fabric"])
        report = verify_client.verify(None, good, log=lambda *_: None)
        check("a packed page with our boot screen verifies", report["problems"] == [], str(report["problems"]))
        check("and the mod set it claims is the mod set the manifest records", report["mods"] == ["hello-fabric"])
        check("with the chunks all hashing as the manifest says", report["bytes_read"] == report["bytes"])

        print("\nand the things that have to be caught")
        # one byte in one chunk, in a copy of the good page
        changed = tmp / "changed-bytes"
        shutil.copytree(good, changed)
        chunk = sorted((changed / "data").glob("*"))[0]
        data = bytearray(chunk.read_bytes())
        data[len(data) // 2] ^= 0xFF
        chunk.write_bytes(bytes(data))
        found = problems_of(changed)
        check("a chunk whose bytes changed is caught by its hash",
              any("does not match the hash" in problem for problem in found), str(found))
        check("and the page it refuses to vouch for is the one with the changed byte",
              any("client.0000" in problem or "data/" in problem for problem in found), str(found))

        with_brand = make(tmp, GOOD_CLIENT.replace('<canvas id="game">', '<div id="gaius-shell-header"></div><canvas id="game">'),
                          "brand-id")
        check("an engine id left in the page is caught",
              any("engine ids" in problem for problem in problems_of(with_brand)), str(problems_of(with_brand)))

        with_boot_brand = make(tmp, GOOD_CLIENT.replace('<div id="boot-screen"', '<div id="boot-brand">GAIUS</div><div id="boot-screen"'),
                               "boot-brand")
        found = problems_of(with_boot_brand)
        check("the branding block over the loading screen is caught",
              any("boot-brand" in problem for problem in found), str(found))
        check("and so are the words a visitor would read",
              any("brand words" in problem for problem in found), str(found))

        no_shell = make(tmp, GOOD_CLIENT.replace('<div id="webmc-boot">', "<div>").replace(
            '<div id="boot-screen" aria-hidden="true"></div>', ""), "no-shell")
        found = problems_of(no_shell)
        check("a client with no loading screen at all is caught",
              any("no loading screen at all" in problem for problem in found), str(found))
        kept = verify_client.verify(None, make(tmp, EAGLER_STYLE, "engine-boot"), log=lambda *_: None)
        check("a client that keeps its own boot screen on purpose is reported, not failed",
              kept["problems"] == [] and kept["boot_screen"] == "the engine's own",
              f"{kept['boot_screen']} {kept['problems']}")

        visible = make(tmp, GOOD_CLIENT.replace('<canvas id="game">', '<p>made with Gaius</p><canvas id="game">'),
                       "visible-brand")
        check("a brand word in the text a visitor reads is caught",
              any("brand words" in problem for problem in problems_of(visible)), str(problems_of(visible)))

        wrong_title = make(tmp, GOOD_CLIENT, "wrong-title")
        manifest = json.loads((wrong_title / "manifest.json").read_text())
        manifest["title"] = "Minecraft 1.99"
        (wrong_title / "manifest.json").write_text(json.dumps(manifest))
        check("a manifest that names a different build than the page says is caught",
              any("does not name this build" in problem for problem in problems_of(wrong_title)),
              str(problems_of(wrong_title)))

        claimed_mods = make(tmp, GOOD_CLIENT, "claimed-mods", mods=["hello-fabric"])
        manifest = json.loads((claimed_mods / "manifest.json").read_text())
        manifest["mods"] = ["hello-fabric", "meteor-client"]
        (claimed_mods / "manifest.json").write_text(json.dumps(manifest))
        check("a build that claims a mod the page does not mention is caught",
              any("does not say which mods" in problem for problem in problems_of(claimed_mods)),
              str(problems_of(claimed_mods)))

        # a gateway is only settable in a client that has somewhere to put one, which is the engine's own config
        eagler_page = GOOD_CLIENT.replace("<title>", '<script id="eagler26-offline-meta" type="application/json">{}</script><title>')
        gateway_page = make(tmp, eagler_page, "gateway", gateway="wss://relay.example/v1/connect")
        check("a page carrying its gateway verifies", problems_of(gateway_page) == [], str(problems_of(gateway_page)))
        manifest = json.loads((gateway_page / "manifest.json").read_text())
        manifest["gateway"] = "wss://other.example/v1/connect"
        (gateway_page / "manifest.json").write_text(json.dumps(manifest))
        check("a manifest whose gateway is not the one in the page is caught",
              any("records a gateway" in problem for problem in problems_of(gateway_page)),
              str(problems_of(gateway_page)))

    print(f"\n{CHECKS - FAILURES}/{CHECKS} verification checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
