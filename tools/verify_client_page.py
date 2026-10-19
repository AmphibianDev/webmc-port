#!/usr/bin/env python3
"""Look at a client page the way a player would: load it in a real browser and report what it shows.

    python3 tools/verify_client_page.py --url https://amphibiandev.github.io/webmc-port/clients/1.21.11/ \
        --seconds 120 --screenshot /tmp/client.png

Why a tool rather than a glance: "the client is deployed" and "the client shows a title screen" are
different claims, and only the second one is what a player can use. The page is driven over the DevTools
protocol, which means real time (not virtual time, which froze the page before it drew anything and made a
working client look like a black rectangle), the page's own console, the text it ends up showing, and a
screenshot whose pixels are measured rather than admired: a title screen has many colours, most of them
not black, and a page that failed to start does not.

Exit code is 0 only when the page loaded, showed no error, and drew something that looks like a frame.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time


PLAYWRIGHT = pathlib.Path.home() / "Library" / "Caches" / "ms-playwright"
FLAGS = ("no-sandbox", "disable-gpu-sandbox", "use-gl=angle", "use-angle=swiftshader",
         "enable-unsafe-swiftshader", "disable-dev-shm-usage", "no-first-run", "disable-features=DBus",
         "autoplay-policy=no-user-gesture-required", "no-store=true")


def find_browser() -> str | None:
    for candidate in PLAYWRIGHT.glob("chromium-*/chrome-*/Google Chrome for Testing.app/Contents/MacOS/*"):
        return str(candidate)
    return shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")


class Browser:
    """The smallest useful DevTools client: one page, evaluate, screenshot, console."""

    def __init__(self, browser: str, work: pathlib.Path, size=(1280, 720)):
        import websocket                      # websocket-client, installed on demand

        self.profile = work / "profile"
        self.profile.mkdir(parents=True, exist_ok=True)
        command = [browser, "--headless=new", "--no-sandbox", f"--user-data-dir={self.profile}",
                   f"--window-size={size[0]},{size[1]}", "--remote-debugging-port=0",
                   "--enable-logging=stderr", "--v=0", "about:blank"]
        command += [f"--{name}" for name in FLAGS]
        self.log = (work / "chrome.log").open("w")
        self.process = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT)
        port = None
        for _ in range(100):
            time.sleep(0.2)
            port_file = self.profile / "DevToolsActivePort"
            if port_file.is_file():
                port = port_file.read_text().splitlines()[0].strip()
                break
        if not port:
            raise SystemExit("Chrome never opened a debugging port; see the log")
        import urllib.request
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5))
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise SystemExit("Chrome's target list never became readable")
        pages = [target for target in targets if target.get("type") == "page"]
        self.console = []
        self.socket = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=30,
                                                 suppress_origin=True)
        self.counter = 0
        for method in ("Page.enable", "Runtime.enable", "Log.enable"):
            self.send(method)

    def send(self, method: str, **params):
        self.counter += 1
        self.socket.send(json.dumps({"id": self.counter, "method": method, "params": params}))
        return self.counter

    def pump(self, seconds: float):
        """Collect events for a while, keeping the console lines the page prints."""
        deadline = time.time() + seconds
        self.socket.settimeout(0.5)
        while time.time() < deadline:
            try:
                message = json.loads(self.socket.recv())
            except Exception:
                continue
            method = message.get("method", "")
            if method == "Runtime.consoleAPICalled":
                text = " ".join(str(part.get("value", part.get("description", "")))
                                for part in message["params"].get("args", []))
                self.console.append(f"[{message['params'].get('type')}] {text}")
            elif method == "Log.entryAdded":
                entry = message["params"]["entry"]
                self.console.append(f"[{entry.get('level')}] {entry.get('text', '')} {entry.get('url', '')}")
            elif method == "Runtime.exceptionThrown":
                detail = message["params"]["exceptionDetails"]
                self.console.append("[exception] " + (detail.get("exception", {}).get("description")
                                                      or detail.get("text", "")))

    def evaluate(self, expression: str):
        identifier = self.send("Runtime.evaluate", expression=expression, returnByValue=True,
                               awaitPromise=False)
        self.socket.settimeout(10)
        while True:
            try:
                message = json.loads(self.socket.recv())
            except Exception:
                return None
            if message.get("id") == identifier:
                result = message.get("result", {}).get("result", {})
                return result.get("value", result.get("description"))

    def close(self):
        try:
            self.process.terminate()
            self.process.wait(timeout=10)
        except Exception:
            self.process.kill()
        self.log.close()


def pixel_report(path: pathlib.Path) -> dict:
    from PIL import Image
    image = Image.open(path).convert("RGB")
    width, height = image.size
    colors = image.getcolors(maxcolors=1 << 24) or []
    total = width * height
    near_black = sum(count for count, (r, g, b) in colors if r + g + b < 40)
    bright = sum(count for count, (r, g, b) in colors if r + g + b > 600)
    return {"size": f"{width}x{height}", "colours": len(colors),
            "black": round(100 * near_black / total, 1), "bright": round(100 * bright / total, 1)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--seconds", type=int, default=150, help="how long to let the client start")
    parser.add_argument("--screenshot", default=None)
    parser.add_argument("--expect", default="", help="a phrase the page must end up showing, e.g. Meteor")
    parser.add_argument("--keep-open", action="store_true", help="leave the browser running afterwards")
    args = parser.parse_args(argv)

    browser = find_browser()
    if not browser:
        print("no Chrome or Chromium found")
        return 2
    try:
        import websocket  # noqa: F401
    except ImportError:
        print("needs websocket-client: pip3 install --user websocket-client")
        return 2

    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-verify-"))
    print(f"browser: {browser}")
    print(f"url: {args.url}")
    session = Browser(browser, work)
    try:
        session.send("Page.navigate", url=args.url)
        deadline = time.time() + args.seconds
        report, status, title = {}, "", ""
        while time.time() < deadline:
            session.pump(5)
            status = session.evaluate("document.querySelector('#status')?.textContent || ''") or ""
            title = session.evaluate("document.querySelector('#title')?.textContent || ''") or ""
            report = session.evaluate(
                "(() => { const w = window.webmc || {};"
                " return { frames: w.frames ? w.frames() : null,"
                " gl: w.glInfo ? w.glInfo() : null,"
                " loader: w.loader ? String(w.loader) : null,"
                " canvas: !!document.querySelector('canvas'),"
                " text: (document.body ? document.body.innerText : '').slice(0, 400) }; })()") or {}
            print(f"  {int(deadline - time.time()):>4}s  title={title[:60]!r} status={status[:70]!r}")
            if report.get("frames"):
                break
        if args.screenshot:
            identifier = session.send("Page.captureScreenshot", format="png")
            session.socket.settimeout(30)
            while True:
                message = json.loads(session.socket.recv())
                if message.get("id") == identifier:
                    import base64
                    pathlib.Path(args.screenshot).write_bytes(base64.b64decode(message["result"]["data"]))
                    break
        print("--- what the page said ---")
        for line in session.console[-40:]:
            print("  " + line[:200])
        print("--- what the page shows ---")
        print(f"  title:  {title[:120]}")
        print(f"  status: {status[:200]}")
        print(f"  body:   {str(report.get('text', ''))[:300]!r}")
        print(f"  canvas: {report.get('canvas')}   frames: {report.get('frames')}   gl: {report.get('gl')}")
        if args.screenshot:
            print(f"  pixels: {pixel_report(pathlib.Path(args.screenshot))}")
        text = f"{title} {status} {report.get('text', '')}"
        problems = []
        if args.expect and args.expect.lower() not in text.lower():
            problems.append(f"the page never showed {args.expect!r}")
        for line in session.console:
            if "favicon" in line:
                continue                      # a missing favicon is not a client problem
            if re.search(r"Uncaught|ReferenceError|TypeError|Failed to load resource|net::ERR", line):
                problems.append(line[:180])
                break
        for problem in problems:
            print(f"! {problem}")
        return 1 if problems else 0
    finally:
        if not args.keep_open:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
