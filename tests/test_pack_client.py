#!/usr/bin/env python3
"""Tests for packing a compiled client into a page a player can open.

    python3 tests/test_pack_client.py

Offline. A fake "compiled client" is built here (a real HTML file of a few megabytes, so there is more
than one chunk), packed, and then the test does what the page does: reads the manifest, checks every
chunk's hash, and reassembles the original bytes. The page's own contract is checked too - it must use
the shared boot screen, verify hashes before running anything, and never point at another origin.

The size limits are the reason this tool exists, so they are pinned: no chunk may be large enough for a
static host to refuse it (GitHub Pages 100 MB, Cloudflare Pages 25 MB), and the default keeps it under
all of them.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import pack_client  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


def fake_client(path: pathlib.Path, megabytes: int = 5) -> pathlib.Path:
    body = "<script>var game = true;</script>\n" + ("x" * (1024 * 1024)) + "\n"
    text = "<!DOCTYPE html><html><head><title>client</title></head><body>" + body * megabytes + "</body></html>"
    path.write_text(text)
    return path


def _error_of(call) -> str:
    """The message a refusal carries. A test that only asserts 'it failed' cannot show the next person why."""
    try:
        call()
    except SystemExit as error:
        return str(error)
    return ""


def _plain_html(tmp: pathlib.Path) -> pathlib.Path:
    path = tmp / "plain.html"
    path.write_text("<!doctype html><html><head><title>not an eagler26 build</title></head><body></body></html>")
    return path


def test_pack(tmp: pathlib.Path) -> None:
    print("\npacking a compiled client")
    client = fake_client(tmp / "client-26.2.html", megabytes=5)
    out = tmp / "site" / "clients" / "26.2"
    lines: list[str] = []
    manifest = pack_client.pack(client, out, "Minecraft 26.2", chunk_mb=2, log=lines.append)
    original = client.read_bytes()

    check("the page is written", (out / "index.html").is_file())
    check("the manifest is written", (out / "manifest.json").is_file())
    check("no chunk is large enough for a host to refuse it",
          all(chunk["size"] <= 2 * 1024 * 1024 for chunk in manifest["chunks"]),
          str([c["size"] for c in manifest["chunks"]]))
    check("the chunks are listed with a hash each",
          all(len(chunk["sha256"]) == 64 for chunk in manifest["chunks"]))
    check("and there is more than one, which is the point", len(manifest["chunks"]) > 1,
          str(len(manifest["chunks"])))
    check("the manifest knows the whole-client hash too", manifest["sha256"] == hashlib.sha256(original).hexdigest())
    check("and the size the page shows a player", manifest["total"] == len(original))

    print("\nreassembling it the way the page does")
    parts = b""
    for chunk in manifest["chunks"]:
        piece = (out / chunk["path"]).read_bytes()
        check(f"{chunk['path']} matches its hash",
              hashlib.sha256(piece).hexdigest() == chunk["sha256"]) if False else None
        parts += piece
    check("every chunk matches the hash in the manifest",
          all(hashlib.sha256((out / c["path"]).read_bytes()).hexdigest() == c["sha256"]
              for c in manifest["chunks"]))
    check("and the bytes are the client again", parts == original,
          f"{len(parts)} vs {len(original)} bytes")

    print("\nthe page's contract")
    page = (out / "index.html").read_text()
    style, markup, script = pack_client.shell_parts()
    check("it carries the boot screen every other row uses",
          'id="webmc-boot"' in page and "--webmc-bg" in page)
    check("it verifies each chunk against its hash before using it",
          "crypto.subtle.digest" in page and "do not match their hash" in page)
    check("and checks the reassembled client too", "does not match its hash" in page)
    check("it runs the client in a frame that may go fullscreen",
          '<iframe id="frame"' in page and "allow=\"fullscreen" in page)
    check("it never points at another origin", "http://" not in page and "https://" not in page,
          "the page must work from any static host, including an http one")
    check("it is honest about what the game is",
          "not an official minecraft product" in page.lower())
    check("a corrupted transfer is reported, not ignored", "could not start:" in page)
    check("progress is shown while the chunks arrive", "webmcBoot.progress" in page)

    print("\nwhat the page says is inside the build")
    modded = pack_client.pack(client, tmp / "modded", "Minecraft 1.21.11", chunk_mb=2,
                              mods=["hello-fabric"], log=lambda *_: None)
    modded_page = (tmp / "modded" / "index.html").read_text()
    check("the mod set is in the manifest, from the bake report rather than from a guess",
          modded["mods"] == ["hello-fabric"])
    check("and the loading screen names it, so 'modded' is something a player can see",
          "manifest.mods" in modded_page and "baked in" in modded_page)
    check("a build with no mod set says nothing about mods",
          pack_client.pack(client, tmp / "plain", "Minecraft 1.21.11", chunk_mb=2,
                           log=lambda *_: None)["mods"] == [])

    print("\nthe gateway a packed client carries")
    e26 = tmp / "client.html"
    e26.write_text('<!doctype html><html><head><title>x</title></head><body>'
                   '<script id="eagler26-offline-meta" type="application/json">{}</script></body></html>')
    gw_manifest = pack_client.pack(e26, tmp / "gw", "26.3", 4, gateway="wss://relay.example/v1/connect",
                                   log=lambda *_: None)
    packed = (tmp / "gw" / "data" / "client.0000").read_bytes().decode()
    check("the gateway is set before the client's own config module runs",
          packed.index("__eaglerConfig") < packed.index("eagler26-offline-meta"),
          "the client merges its defaults *under* whatever is already set, so this has to come first")
    check("the value is the address the player's client will tunnel through",
          "wss://relay.example/v1/connect" in packed and "goldProxy" in packed)
    check("the manifest records it, so a published page can be traced to its gateway",
          gw_manifest["gateway"] == "wss://relay.example/v1/connect")
    check("a build with no gateway leaves the client untouched",
          pack_client.pack(e26, tmp / "nogw", "26.3", 4, log=lambda *_: None)["gateway"] is None)

    print("\nthe profile engine's own tunnel dialect")
    gaius_page = ('<!doctype html><html><head><title>Minecraft 1.21.11</title></head><body>'
                  '<script>var gaiusBridge = "__gaiusNettyBridge";</script></body></html>')
    gaius_path = tmp / "gaius.html"
    gaius_path.write_text(gaius_page)
    packed_gaius = pack_client.pack(gaius_path, tmp / "gw-gaius", "Minecraft 1.21.11", 4,
                                    gateway="wss://relay.example/v1/connect", log=lambda *_: None)
    body = (tmp / "gw-gaius" / "data" / "client.0000").read_bytes().decode()
    check("a profile-engine client is given the tunnel the way it asks for one",
          "__gaiusBridgeUrls" in body and "wss://relay.example/v1/connect/tunnel" in body, body[:160])
    check("and it is told before its own code runs, because it reads the setting at boot",
          body.index("__gaiusBridgeUrls") < body.index("__gaiusNettyBridge"))
    check("the manifest records it like any other gateway",
          packed_gaius["gateway"] == "wss://relay.example/v1/connect")
    check("and a client that has no place to put one is refused rather than packed unconfigured",
          "nowhere to put a gateway" in _error_of(
              lambda: pack_client.pack(_plain_html(tmp), tmp / "bad", "x", 4, gateway="wss://a.example/v1")))
    check("the chunks are fetched in parallel, not one after another",
          "Promise.all(manifest.chunks.map" in page, "sequential fetches make the wait the sum of the parts")
    check("each chunk is still verified before it is used",
          "fetchChecked(chunk.path, chunk.sha256" in page)
    check("and the version being loaded is named on the boot screen",
          "webmcBoot.version(manifest.title" in page)
    check("the bar cannot go backwards", "if (next > value) { value = next; }" in
          (ROOT / "site" / "boot" / "shell.html").read_text(),
          "a bar that jumps back is two progress reports on different scales")
    check("and the size comes from the manifest, not from a hardcoded number",
          "manifest.total" in page)

    print("\npacking again")
    before = sorted(path.name for path in (out / "data").glob("*"))
    again = pack_client.pack(client, out, "Minecraft 26.2", chunk_mb=2, log=lambda *_: None)
    after = sorted(path.name for path in (out / "data").glob("*"))
    check("the chunk set is stable, so a repack cannot leave orphans behind", before == after,
          f"{before} -> {after}")
    check("and the manifest is identical", again == manifest)
    stale = out / "data" / "client.9999"
    stale.write_bytes(b"leftover")
    pack_client.pack(client, out, "Minecraft 26.2", chunk_mb=2, log=lambda *_: None)
    check("a stale chunk from an older build is removed", not stale.exists())

    print("\nwhat it refuses")
    not_a_page = tmp / "not-a-page.bin"
    not_a_page.write_bytes(b"\x00\x01\x02" * 1000)
    try:
        pack_client.pack(not_a_page, tmp / "out", "x", chunk_mb=2, log=lambda *_: None)
        check("a file that is not a page is refused", False, "no error raised")
    except SystemExit as error:
        check("a file that is not a page is refused", "not an HTML client" in str(error), str(error))
    check("and the default chunk size is under every host's limit",
          0 < pack_client.DEFAULT_CHUNK_MB <= 25, str(pack_client.DEFAULT_CHUNK_MB))


def test_command_line(tmp: pathlib.Path) -> None:
    print("\nthe command")
    client = fake_client(tmp / "c.html", megabytes=2)
    out = tmp / "packed"
    code = pack_client.main(["--client", str(client), "--out", str(out), "--title", "Minecraft 1.2.5",
                             "--chunk-mb", "1"])
    check("it exits zero", code == 0, str(code))
    check("and names the version in the page title", "Minecraft 1.2.5" in (out / "index.html").read_text())
    check("a missing client is an error, not an empty page",
          pack_client.main(["--client", str(tmp / "nope.html"), "--out", str(out)]) == 2)


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        tmp = pathlib.Path(scratch)
        test_pack(tmp)
        test_command_line(tmp)
    print(f"\n{FAILURES} pack test(s) FAILED" if FAILURES else "\npack tests passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
