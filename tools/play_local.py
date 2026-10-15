#!/usr/bin/env python3
"""Play a downloaded single-file client locally, with multiplayer wired up.

A single-file offline client works when you open it: that is the point of it. What it does not have is a
gateway, so its multiplayer is switched off until something tells it where to tunnel - and a browser cannot
open a TCP socket, which is why there has to be a gateway at all (MULTIPLAYER.md).

This runs the two halves that make it work on one machine:

  * our gateway (`gateway/server.js`) on a loopback port, which is what a typed server address goes through;
  * a small static server that serves the client with the gateway already set inside it, so the address box
    in the game works the way it does in any other client.

Nothing here is a service: both processes are children of this one and stop with it. `--gateway` points at a
gateway somewhere else if you already have one, and `--server` opens the client straight into a server.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "gateway" / "server.js"
GATEWAY_MARK = "data-webmc-gateway"
DOWNLOADS = pathlib.Path.home() / "Downloads"


def find_client(explicit: str | None) -> pathlib.Path:
    """The client to serve: what was asked for, or the newest single-file offline build in Downloads."""
    if explicit:
        path = pathlib.Path(explicit).expanduser()
        if not path.is_file():
            raise SystemExit(f"no such client: {path}")
        return path
    candidates = sorted(
        (path for path in DOWNLOADS.glob("*.html") if "eagler" in path.name.lower()),
        key=lambda path: path.stat().st_mtime, reverse=True,
    ) if DOWNLOADS.is_dir() else []
    if not candidates:
        raise SystemExit("no single-file client found in ~/Downloads: pass one with --client")
    return candidates[0]


# The two lineages take their multiplayer setting differently, and the difference is not cosmetic:
#   * the Eagler26 lineage (goldproxy) tunnels: it needs a *gateway* that dials a normal Java server for it;
#   * the EaglercraftX lineage connects straight to an Eaglercraft *server* over wss, so what it wants is that
#     server's address, not a gateway. Handing one the other's value is exactly how a client ends up unable to
#     connect, which is the complaint this whole exercise started from.
EAGLERX_MARKERS = ("eaglercraftXOpts", "assets.epk")


def is_eaglercraftx(data: bytes) -> bool:
    head = data[:4000].decode("utf-8", "replace")
    return any(marker in head for marker in EAGLERX_MARKERS)


def eaglercraftx_script(server: str | None) -> str:
    """The options object the EaglercraftX lineage reads at boot. No gateway: it speaks to a server itself."""
    options = {"server": server} if server else {}
    return ('<script data-webmc-server="' + (server or "") + '">/* WebMC: what this client should open with. The '
            "EaglercraftX lineage connects to an Eaglercraft server over wss itself, so this is an address, not a "
            "gateway. */\nwindow.eaglercraftXOpts = Object.assign(" + json.dumps(options)
            + ", window.eaglercraftXOpts || {});\n</script>\n")


def gateway_script(url: str) -> str:
    if not re.match(r"^wss?://[^\s\"']+$", url):
        raise SystemExit(f"a gateway address starts with ws:// or wss://, not {url!r}")
    return (
        f'<script {GATEWAY_MARK}="{url}">/* WebMC: the gateway this page should use. A value a player has saved '
        "in this browser still wins, which is the client's own rule. */\n"
        "globalThis.__eaglerConfig = Object.assign({ goldProxy: { gateway: " + json.dumps(url) + " } }, "
        "globalThis.__eaglerConfig || {});\n"
        "try { localStorage.setItem('eagler26.goldproxy', JSON.stringify({ gateway: " + json.dumps(url)
        + " })); } catch (e) {}\n</script>\n")


def patch_client(data: bytes, gateway: str, server: str | None = None) -> bytes:
    """Put the multiplayer setting into the client before any of its code runs."""
    text = data.decode("utf-8", errors="surrogateescape")
    if GATEWAY_MARK in text or 'data-webmc-server="' in text:
        return data
    if is_eaglercraftx(data):
        insertion = eaglercraftx_script(server)
    elif "__eaglerConfig" in text or "eagler26-offline-meta" in text:
        insertion = gateway_script(gateway)
    elif "__gaius" in text or "gaius" in text[:200000].lower():
        # the profile engine's own tunnel dialect: a relay node URL, and it appends /tunnel itself
        insertion = ('<script data-webmc-bridge="' + gateway + '">/* WebMC: the tunnel this build should use. */\n'
                     "globalThis.__gaiusBridgeUrl = " + json.dumps(gateway.rstrip("/") + "/tunnel") + ";\n"
                     "globalThis.__gaiusBridgeUrls = [globalThis.__gaiusBridgeUrl];\n</script>\n")
    else:
        raise SystemExit("this build has nowhere to put a multiplayer setting: it is neither an Eagler26 "
                         "(goldproxy) build nor an EaglercraftX one, so nothing here can point it at a server")
    # No extra newline, no reformatting: removing the tag has to give back the file byte for byte, because
    # "the client is otherwise untouched" is a claim a 200 MB payload should be able to pass. (A leftover
    # `insertion = gateway_script(...)` here overwrote the lineage choice and made the EaglercraftX path dead
    # code - the sort of line that only a test on the other lineage finds.)
    if "<head>" in text:
        patched = text.replace("<head>", "<head>" + insertion, 1)
    elif "<html" in text:
        patched = text.replace(">", ">" + insertion, 1)
    else:
        patched = insertion + text
    return patched.encode("utf-8", errors="surrogateescape")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for_health(port: int, attempts: int = 60) -> dict | None:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.1)
    return None


class Handler(http.server.BaseHTTPRequestHandler):
    client: bytes = b""
    gateway: str = ""
    gateway_port: int = 0

    def log_message(self, *args) -> None:  # a page load is not worth a line of noise per request
        pass

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/client.html"):
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(self.client)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(self.client)
            return
        if path == "/health":
            # the gateway's own status, so one URL answers "is the tunnel up" for whoever is watching
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.gateway_port}/health", timeout=3) as response:
                    body = response.read()
            except Exception as error:  # noqa: BLE001 - the reason is the answer
                body = json.dumps({"ok": False, "error": str(error)}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/gateway":
            body = json.dumps({"gateway": self.gateway}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", default=None, help="the single-file client to serve (.html)")
    parser.add_argument("--gateway", default=None,
                        help="a gateway to use instead of starting one (wss://…/v1/connect or ws://127.0.0.1:PORT/v1/connect)")
    parser.add_argument("--server", default=None,
                        help="join this server on load (host[:port]; wss://host for an EaglercraftX build)")
    parser.add_argument("--port", type=int, default=0, help="the port to serve the page on (default: any free one)")
    parser.add_argument("--gateway-port", type=int, default=0, help="the port for the gateway (default: any free one)")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    parser.add_argument("--allow-lan", action="store_true",
                        help="let the gateway reach private addresses too (LAN play; off by default)")
    args = parser.parse_args(argv)

    if not shutil.which("node"):
        print("node is needed for the gateway: https://nodejs.org", file=sys.stderr)
        return 2
    client_path = find_client(args.client)
    page_port = args.port or free_port()

    gateway_child = None
    if args.gateway:
        gateway_url = args.gateway
        gateway_port = 0
        print(f"using the gateway you gave me: {gateway_url}")
    else:
        gateway_port = args.gateway_port or free_port()
        gateway_url = f"ws://127.0.0.1:{gateway_port}/v1/connect"
        env = dict(os.environ)
        env.update({
            "GATEWAY_PORT": str(gateway_port),
            "GATEWAY_HOST": "127.0.0.1",
            "GATEWAY_PUBLIC_URL": "",
            "GATEWAY_ALLOW_PRIVATE_TARGETS": "1" if args.allow_lan else "0",
            "GATEWAY_ALLOWED_ORIGINS": f"http://127.0.0.1:{page_port},http://localhost:{page_port}",
        })
        gateway_child = subprocess.Popen([shutil.which("node"), str(GATEWAY)], env=env,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        health = wait_for_health(gateway_port)
        if not health:
            print("the gateway did not start:", file=sys.stderr)
            if gateway_child.stdout:
                print(gateway_child.stdout.read()[-2000:], file=sys.stderr)
            gateway_child.kill()
            return 1
        print(f"gateway on ws://127.0.0.1:{gateway_port}/v1/connect "
              f"({health.get('availableConnections')} session(s) free, private targets "
              f"{'allowed' if health.get('allowPrivateTargets') else 'refused'})")

    raw_client = client_path.read_bytes()
    lineage = "eaglercraftx" if is_eaglercraftx(raw_client) else "eagler26"
    page = patch_client(raw_client, gateway_url, args.server)
    Handler.client = page
    Handler.gateway = gateway_url
    Handler.gateway_port = gateway_port or 0

    server = http.server.ThreadingHTTPServer(("127.0.0.1", page_port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{page_port}/"
    if args.server:
        host, _, port = args.server.partition(":")
        url += f"?server={host}&port={port or 25565}"
    # flushed: whoever started this is watching a pipe, and a page that opened while the instructions sit in a
    # buffer is a page nobody knows the address of
    print(f"\n  {client_path.name}\n  open {url}\n", flush=True)
    if lineage == "eaglercraftx":
        print("  This is an EaglercraftX build: it talks to an Eaglercraft server over wss itself, so its address",
              flush=True)
        print("  box takes an Eaglercraft server (wss://host) rather than a Java server. --server sets it here.",
              flush=True)
    else:
        print("  The address box takes a host and an optional port (mc.example.com, or host:25565): this build",
              flush=True)
        print("  tunnels through the gateway above.", flush=True)
    print("  Type Ctrl-C here to stop everything this started.", flush=True)
    if not args.no_open:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        if shutil.which(opener):
            subprocess.Popen([opener, url])

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.shutdown()
        if gateway_child:
            gateway_child.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
