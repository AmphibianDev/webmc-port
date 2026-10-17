#!/usr/bin/env python3
"""Run the shim's boot test in a real browser, locally.

    python3 tools/browser_boot_test.py                 # the shim's own BootTest
    python3 tools/browser_boot_test.py --page site/play.html --query "?version=1.21.11"

The same thing .github/workflows/lwjgl3.yml does, without a runner: compile the shim and the Java
program that exercises it, serve them with a collector, and drive a headless Chrome that has software
WebGL. The page reports by POSTing, because a headless browser's console is not readable from a shell
and because the Java program's own output is what matters when it fails.

Why this exists: CheerpJ only runs in a browser, so "does the shim work" cannot be answered by javac.
Without a browser in the loop a change to the shim is a guess, and the guesses are what this project
kept losing days to.

The browser is found where Playwright puts it, then in PATH - a machine that has run a Playwright test
has one, and a runner with google-chrome installed has one, and either is fine.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import pathlib
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
PLAYWRIGHT = pathlib.Path.home() / "Library" / "Caches" / "ms-playwright"

BUILTIN = ("disable-gpu-sandbox", "use-gl=angle", "use-angle=swiftshader", "enable-unsafe-swiftshader",
           "disable-dev-shm-usage", "no-first-run", "disable-features=DBus")


def find_browser() -> str | None:
    """Chrome, from Playwright's cache or from PATH."""
    for candidate in PLAYWRIGHT.glob("chromium-*/chrome-*/Google Chrome for Testing.app/Contents/MacOS/*"):
        return str(candidate)
    return shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")


# Hosts a launch needs that this network's own resolver answers with 0.0.0.0 (it does that for shopping
# sites, forums, and - less conveniently - CheerpJ's CDN). Asking 1.1.1.1 directly works, so the browser
# is told where they are instead of being left to a resolver that lies.
NEEDED_HOSTS = ("cjrtnc.leaningtech.com", "cdn.leaningtech.com", "cheerpj.com", "meta.fabricmc.net",
                "maven.fabricmc.net", "piston-data.mojang.com", "piston-meta.mojang.com",
                "libraries.minecraft.net", "api.minecraftservices.com", "sessionserver.mojang.com")


def host_resolver_rules(hosts=NEEDED_HOSTS) -> str:
    rules = []
    for host in hosts:
        try:
            out = subprocess.run(["dig", "+short", "@1.1.1.1", host], capture_output=True, text=True).stdout
        except OSError:
            return ""
        addresses = [line.strip() for line in out.splitlines() if re.fullmatch(r"[0-9.]+", line.strip())]
        if addresses:
            rules.append(f"MAP {host} {addresses[0]}")
    return ",".join(rules)


def collector(directory: pathlib.Path, port: int, reports: dict):
    """A file server that keeps the last POSTed report, and can set the headers CheerpJ asks for.

    Cross-origin isolation is offered because it is what makes SharedArrayBuffer exist, and a browser
    JVM uses threads. A page that does not need it is unaffected.
    """

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def end_headers(self):
            # credentialless, not require-corp: the loader comes from CheerpJ's CDN, which serves no
            # Cross-Origin-Resource-Policy header, and require-corp would block it - the page then fails
            # with "cheerpjInit is not defined", which reads like a broken page rather than a blocked
            # script. credentialless still gives a cross-origin isolated context, so threads work.
            self.send_header("cross-origin-opener-policy", "same-origin")
            self.send_header("cross-origin-embedder-policy", "credentialless")
            self.send_header("cross-origin-resource-policy", "cross-origin")
            super().end_headers()

        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length)
            try:
                reports["last"] = json.loads(body)
            except ValueError:
                reports["last"] = {"raw": body.decode("utf-8", "replace")}
            reports.setdefault("all", []).append(reports["last"])
            self.send_response(204)
            self.send_header("content-length", "0")
            self.end_headers()

    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def compile_java(work: pathlib.Path, extra_sources=()) -> pathlib.Path:
    """The shim, plus a program that uses it, into one jar - the form CheerpJ is documented to load."""
    classes = work / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    sources = sorted(str(path) for path in (ROOT / "lwjgl3-webgl" / "src" / "main" / "java").rglob("*.java"))
    sources += [str(path) for path in extra_sources]
    listing = work / "sources.txt"
    listing.write_text("\n".join(sources))
    java = os.environ.get("JAVA_HOME", "")
    javac = (pathlib.Path(java) / "bin" / "javac") if java else pathlib.Path("javac")
    run([str(javac), "--release", "17", "-nowarn", "-d", str(classes), f"@{listing}"])
    jar = work / "app.jar"
    run(["jar", "--create", "--file", str(jar), "-C", str(classes), "."])
    return jar


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(command)}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--page", default=None,
                        help="serve this directory (with index.html) instead of building the boot test")
    parser.add_argument("--query", default="")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--timeout", type=int, default=600, help="seconds to wait for a report")
    parser.add_argument("--browser", default=None)
    parser.add_argument("--keep", action="store_true", help="keep the work directory (the browser's log)")
    parser.add_argument("--console", action="store_true", help="print every console line, not a summary")
    args = parser.parse_args(argv)

    browser = args.browser or find_browser()
    if not browser:
        print("no browser found: install Chrome, or let Playwright install one")
        return 2

    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-boot-"))
    reports: dict = {}
    server = None
    try:
        if args.page:
            directory = pathlib.Path(args.page).resolve()
            url = f"http://127.0.0.1:{args.port}/index.html{args.query}"
        else:
            print("compiling the shim and the boot test…")
            jar = compile_java(work, extra_sources=[ROOT / "lwjgl3-webgl" / "test" / "BootTest.java"])
            (work / "index.html").write_text((ROOT / "lwjgl3-webgl" / "test" / "boot.html").read_text())
            shutil.copytree(ROOT / "lwjgl3-webgl" / "natives", work / "natives", dirs_exist_ok=True)
            print(f"  app.jar: {jar.stat().st_size} bytes")
            directory = work
            url = f"http://127.0.0.1:{args.port}/index.html"

        server = collector(directory, args.port, reports)
        print(f"serving {directory} on {args.port}")
        print(f"browser: {browser}")

        flags = [browser, "--headless=new", "--no-sandbox", "--window-size=400,400",
                 f"--user-data-dir={work / 'profile'}", "--enable-logging=stderr", "--v=0"]
        flags += [f"--{name}" for name in BUILTIN]
        rules = host_resolver_rules()
        if rules:
            flags.append(f"--host-resolver-rules={rules}")
            print(f"resolver overrides: {rules}")
        flags.append(url)
        log = work / "chrome.log"
        with log.open("w") as handle:
            process = subprocess.Popen(flags, stdout=handle, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + args.timeout
            while time.time() < deadline:
                report = reports.get("last")
                if report and report.get("code") in (0, 1, 99):
                    break
                if report and "code" not in report:
                    break
                time.sleep(2)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

        report = reports.get("last")
        print("\n--- what the page reported ---")
        if not report:
            print("(nothing was posted)")
        else:
            for line in report.get("log") or []:
                print("  " + line)
            print("  " + json.dumps({k: v for k, v in report.items() if k != "log"})[:800])
            for stage in (reports.get("all") or [])[:-1]:
                if stage.get("stage"):
                    print(f"  (earlier: {stage['stage']})")

        print("\n--- the browser's console, last lines ---")
        text = log.read_text(errors="replace") if log.exists() else ""
        console = [line.split("CONSOLE:", 1)[1] if "CONSOLE:" in line else line
                   for line in text.splitlines()
                   if any(word in line for word in ("CONSOLE", "ERROR", "error", "Uncaught"))]
        shown = console if args.console else console[-30:]
        for line in shown:
            print("  " + line.strip()[:300])
        if args.keep:
            print(f"\n(kept: {work})")

        if not report:
            print("\nFAILED: the page never reported")
            return 1
        code = report.get("code")
        frames = report.get("frames", 0)
        if code == 0 and frames:
            print(f"\nthe boot test drew {frames} frame(s) in a real browser")
            return 0
        print(f"\nthe boot test did not finish (code={code}, frames={frames})")
        return 1
    finally:
        if server:
            server.shutdown()
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
