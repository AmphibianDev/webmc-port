#!/usr/bin/env python3
"""Turn a compiled client into a page a player can open, on a static host.

    python3 tools/pack_client.py --client dist/26.2.html --out site/clients/26.2 --title "Minecraft 26.2"

Why this exists: a compiled client is one enormous HTML file (26.2 is 115 MB), and no static host will
serve it as a page - GitHub Pages refuses any file over 100 MB, Cloudflare Pages caps at 25 MB. So the
client is split into chunks, listed with a hash each, and the page this writes fetches them, verifies
them, puts them back into one blob and hands that to an iframe. The visitor gets a tab that runs the
game; the host gets files it is willing to serve.

What the page does, in order:

  1. shows our boot screen (the same file every other row uses) while the client streams in;
  2. fetches `manifest.json`, then each chunk, checking its sha256 - a host that corrupts or truncates a
     chunk is caught here rather than inside the game;
  3. reassembles the blob and puts it in an iframe that may go fullscreen, because a game wants the
     keyboard and the whole screen;
  4. hands over: the boot screen fades when the client's own document loads.

This is what makes a compiled build playable *in a page* rather than downloadable, and it is also what
makes it cacheable: the chunks are immutable, so the second visit costs nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SHELL = ROOT / "site" / "boot" / "shell.html"
DEFAULT_CHUNK_MB = 20          # under every host's per-file limit, including Cloudflare Pages' 25 MB


def shell_parts() -> tuple[str, str, str]:
    """The boot screen's style, markup and script, so this page and every other row share one."""
    text = SHELL.read_text()
    style = re.search(r"<style>([\s\S]*?)</style>", text)
    body = re.search(r"<body>\s*([\s\S]*?)\s*</body>", text)
    script = re.search(r"<script>([\s\S]*?)</script>", text)
    if not (style and body and script):
        raise SystemExit(f"{SHELL} is not the shape this tool expects (style, body, script)")
    markup = body.group(1).replace('<div id="x11-wasm-root"></div>', "").strip()
    return style.group(1), markup, script.group(1)


def start_page(title: str, manifest_name: str = "manifest.json", mods: list[str] | None = None,
               gateway: str | None = None) -> str:
    """The page that fetches, verifies and runs the client.

    The build's own facts go in as meta tags as well as in the manifest: a page that says what it is in its own
    source can be checked from the outside (tools/verify_client.py does exactly that) without running it, and
    "which mods are in this build" is a question worth being able to answer with curl.
    """
    css, markup, script = shell_parts()
    facts = "".join([
        f'\n<meta name="webmc-mods" content="{",".join(mods)}" />' if mods else "",
        f'\n<meta name="webmc-gateway" content="{gateway}" />' if gateway else "",
    ])
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>{title}</title>{facts}
<!-- Written by tools/pack_client.py. The client itself is the chunks next to this page. -->
<style>
{css}
  html, body {{ height: 100%; }}
  #frame {{ position: fixed; inset: 0; width: 100%; height: 100%; border: 0; background: #000; }}
  #start {{ position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
            background: #000; color: #ddd; font: 14px/1.6 ui-monospace, Menlo, monospace; text-align: center; }}
  #start button {{ font: inherit; font-size: 17px; padding: 14px 26px; background: #2a5c3a; color: #fff;
                   border: 1px solid #3d7d51; border-radius: 5px; cursor: pointer; }}
  #start p {{ max-width: 38rem; color: #888; }}
</style>
</head>
<body>
<div id="start">
  <div>
    <h1 style="font-size:20px;margin:0 0 10px">{title}</h1>
    <p id="why">The client is {{size}} MB and loads once; after that it comes from this browser's cache.</p>
    <button id="go">▶ Play</button>
    <p id="note">Compiled from the official client by this project's build job. Not an official Minecraft product. Not approved by or associated with Mojang or Microsoft.</p>
  </div>
</div>
{markup}
<iframe id="frame" allow="fullscreen; gamepad" style="display:none"></iframe>
<script>
{script}
</script>
<script>
/* Start the client: fetch the chunks, check them, hand the blob to an iframe. */
(function () {{
  "use strict";
  var manifestName = {json.dumps(manifest_name)};
  var frame = document.getElementById("frame");
  var start = document.getElementById("start");
  var go = document.getElementById("go");
  var why = document.getElementById("why");

  function hex(bytes) {{
    return Array.prototype.map.call(new Uint8Array(bytes), function (b) {{
      return b.toString(16).padStart(2, "0");
    }}).join("");
  }}

  async function digest(bytes) {{
    if (!window.crypto || !crypto.subtle) {{ return null; }}   // an insecure origin has no crypto
    return hex(await crypto.subtle.digest("SHA-256", bytes));
  }}

  async function fetchChecked(url, expected, label) {{
    var response = await fetch(url, {{ cache: "force-cache" }});
    if (!response.ok) {{ throw new Error(label + ": HTTP " + response.status); }}
    var bytes = new Uint8Array(await response.arrayBuffer());
    var got = await digest(bytes);
    if (expected && got && got !== expected) {{
      throw new Error(label + ": the bytes do not match their hash - the transfer was corrupted");
    }}
    return bytes;
  }}

  async function play() {{
    go.disabled = true;
    try {{
      var manifest = await (await fetch(manifestName, {{ cache: "force-cache" }})).json();
      var parts = new Array(manifest.chunks.length);
      var seen = 0;
      if (window.webmcBoot) {{
        window.webmcBoot.version(manifest.title + (manifest.mods && manifest.mods.length
          ? " — with " + manifest.mods.join(", ") + " baked in" : ""));
        window.webmcBoot.progress(0, "Loading " + manifest.title);
      }}
      /* Every chunk at once. They are independent files with a hash each, and fetching them in sequence
       * made the wait the sum of the pieces when it can be the largest one: six chunks of ~18 MB take as
       * long as one on a decent link. Progress is aggregated so the bar still moves smoothly. */
      await Promise.all(manifest.chunks.map(async function (chunk, index) {{
        var bytes = await fetchChecked(chunk.path, chunk.sha256, chunk.path);
        parts[index] = bytes;
        seen += bytes.length;
        if (window.webmcBoot) {{
          window.webmcBoot.progress(seen / manifest.total, "Loading " + manifest.title);
        }}
        why.textContent = (seen / 1048576).toFixed(1) + " / " + (manifest.total / 1048576).toFixed(1) + " MB";
      }}));
      var blob = new Blob(parts, {{ type: "text/html" }});
      /* A build that had a mod set baked in says so before the client opens, because that is the difference
       * between a modded build and a build that silently ignored the mods. */
      if (manifest.mods && manifest.mods.length && window.webmcBoot) {{
        window.webmcBoot.progress(1, "Baked in: " + manifest.mods.join(", "));
      }}
      if (await digest(await blob.arrayBuffer()) !== manifest.sha256 && manifest.sha256) {{
        throw new Error("the reassembled client does not match its hash");
      }}
      frame.src = URL.createObjectURL(blob);
      frame.style.display = "block";
      start.style.display = "none";
      frame.focus();
      /* The client draws its own loading screen inside the iframe; our boot screen is already behind it
       * and has faded by the time the frame paints, so the hand-over is invisible. */
      if (window.webmcBoot) {{ window.webmcBoot.ready(); }}
    }} catch (error) {{
      go.disabled = false;
      why.textContent = "could not start: " + (error && error.message ? error.message : error);
      why.className = "err";
    }}
  }}

  go.addEventListener("click", play);
  /* Autostart when the browser already has every chunk cached: no click needed, no download. */
  if (typeof caches !== "undefined") {{
    caches.open("webmc-clients-v2").then(function () {{ return play(); }}).catch(function () {{}});
  }}
}})();
</script>
</body>
</html>
'''


# A gateway is what makes a typed server address work at all (see MULTIPLAYER.md). A browser cannot open a
# TCP socket, so the client has to be told where to tunnel; a packed client that nobody configured is a client
# whose multiplayer is silently off, which is the one failure a player cannot diagnose from the game.
GATEWAY_MARK = "data-webmc-gateway"


def gateway_script(url: str) -> str:
    if not re.match(r"^wss?://[^\s\"']+$", url):
        raise SystemExit(f"a gateway address has to start with ws:// or wss://, not {url!r}")
    return (
        f'<script {GATEWAY_MARK}="{url}">/* WebMC: the gateway this build should use. The client reads '
        "its own config first, so a player can still override it - nothing here is a key or a secret. */\n"
        f"globalThis.__eaglerConfig = Object.assign({{ goldProxy: {{ gateway: {json.dumps(url)} }} }}, "
        "globalThis.__eaglerConfig || {});\n</script>\n")


def inject_gateway(html: bytes, url: str) -> bytes:
    """Put the gateway default into an Eagler26 build, using the client's own configuration merge.

    `eagler-config.js` does `Object.assign({...defaults}, globalThis.__eaglerConfig || {})`, so a value set
    before its module runs survives - and a value in `localStorage["eagler26.goldproxy"]` still wins over it.
    """
    text = html.decode("utf-8", errors="surrogateescape")
    if GATEWAY_MARK in text:
        return html  # already set by an earlier pass
    if "__eaglerConfig" not in text and "eagler26-offline-meta" not in text:
        raise SystemExit("this build does not look like an Eagler26 client: it has no client config to set "
                         "a gateway in, so the address would be ignored")
    marker = "<head>"
    insertion = marker + "\n" + gateway_script(url)
    if marker in text:
        text = text.replace(marker, insertion, 1)
    else:
        text = gateway_script(url) + text
    return text.encode("utf-8", errors="surrogateescape")


def pack(client: pathlib.Path, out: pathlib.Path, title: str, chunk_mb: int, gateway: str | None = None,
         mods: list[str] | None = None, log=print) -> dict:
    """Split the client, write the chunks, the manifest and the page. Returns the manifest."""
    data = client.read_bytes()
    head = data[:512].decode("utf-8", errors="replace").lower()
    if "<!doctype html" not in head and "<html" not in head:
        raise SystemExit(f"{client} is not an HTML client (its first bytes are not a page)")
    if gateway:
        data = inject_gateway(data, gateway)
        log(f"  set the gateway default to {gateway} inside the client")

    chunk_bytes = chunk_mb * 1024 * 1024
    out.mkdir(parents=True, exist_ok=True)
    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for stale in data_dir.glob("*"):
        stale.unlink()

    chunks = []
    for index in range(0, len(data), chunk_bytes) or [0]:
        piece = data[index:index + chunk_bytes]
        name = f"client.{index // chunk_bytes:04d}"
        (data_dir / name).write_bytes(piece)
        chunks.append({"path": f"data/{name}", "size": len(piece),
                       "sha256": hashlib.sha256(piece).hexdigest()})

    manifest = {
        "note": "Written by tools/pack_client.py. Chunks of one compiled client; immutable once published.",
        "title": title,
        "client": client.name,
        "gateway": gateway or None,
        # A build that had mods baked into it should say so where a player can see it, or "modded" is a word in
        # a catalogue and nothing on the screen. The list comes from the bake report, not from a guess.
        "mods": list(mods or []),
        "bytes": len(data),
        "total": len(data),
        "chunk_bytes": chunk_bytes,
        "sha256": hashlib.sha256(data).hexdigest(),
        "chunks": chunks,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (out / "index.html").write_text(start_page(title, mods=mods, gateway=gateway))
    log(f"  packed {client.name}: {len(data) / 1048576:.1f} MB in {len(chunks)} chunk(s) of "
        f"{chunk_mb} MB under {out}")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, help="the compiled client HTML")
    parser.add_argument("--out", required=True, help="a directory to write the page, manifest and chunks")
    parser.add_argument("--title", default="Minecraft")
    parser.add_argument("--chunk-mb", type=int, default=DEFAULT_CHUNK_MB)
    parser.add_argument("--gateway", default=None,
                        help="wss://…/v1/connect to bake in as the multiplayer default (Eagler26 clients)")
    parser.add_argument("--mods", default="", help="comma-separated ids baked into this build, for the page")
    args = parser.parse_args(argv)

    client = pathlib.Path(args.client)
    if not client.is_file():
        print(f"no such client: {client}", file=sys.stderr)
        return 2
    manifest = pack(client, pathlib.Path(args.out), args.title, args.chunk_mb, args.gateway,
                    [m.strip() for m in args.mods.split(",") if m.strip()])
    hmm = [chunk for chunk in manifest["chunks"] if chunk["size"] > 100 * 1024 * 1024]
    if hmm:
        print("  ! a chunk is over 100 MB: GitHub Pages would refuse it", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
