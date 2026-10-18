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
import io
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


def collector(directory: pathlib.Path, port: int, reports: dict, isolate: dict):
    """A file server that keeps the last POSTed report, and can set the headers CheerpJ asks for.

    Cross-origin isolation is offered because it is what makes SharedArrayBuffer exist, and a browser
    JVM uses threads. A page that does not need it is unaffected.
    """

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def end_headers(self):
            # Off unless asked for. cross-origin isolation is what SharedArrayBuffer needs, but CheerpJ's
            # own loader and runtime come from its CDN without Cross-Origin-Resource-Policy, and a page
            # that sets these headers around it hung in cheerpjRunMain for fifteen minutes without a word
            # - while the identical page served without them ran Java in ninety seconds. So: opt in, and
            # only for a page that needs the isolation for its own reasons.
            if isolate["on"]:
                self.send_header("cross-origin-opener-policy", "same-origin")
                self.send_header("cross-origin-embedder-policy", "credentialless")
                self.send_header("cross-origin-resource-policy", "cross-origin")
            # no-store, always: the profile that makes CheerpJ's runtime cheap to reuse would otherwise
            # also let Chrome serve yesterday's page from its cache - which it did, running the previous
            # test's Java program and reporting its result as this one's
            self.send_header("cache-control", "no-store")
            super().end_headers()

        def send_head(self):
            """Serve ranges, because CheerpJ streams a jar in pieces and refuses one that cannot.

            Its words, from the console: "Network error for .../t.jar: HTTP server does not support the
            'Range' header. CheerpJ cannot run." A 27 MB client jar is exactly the case where it matters.
            """
            header = self.headers.get("range")
            if not header or not header.startswith("bytes="):
                return super().send_head()
            path = self.translate_path(self.path)
            if not pathlib.Path(path).is_file():
                return super().send_head()
            size = pathlib.Path(path).stat().st_size
            first, _, last = header[len("bytes="):].partition("-")
            try:
                start = int(first)
                end = int(last) if last else size - 1
            except ValueError:
                return super().send_head()
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("content-range", f"bytes */{size}")
                self.end_headers()
                return None
            handle = pathlib.Path(path).open("rb")
            handle.seek(start)
            body = handle.read(end - start + 1)
            handle.close()
            self.send_response(206)
            self.send_header("content-type", self.guess_type(path))
            self.send_header("content-range", f"bytes {start}-{end}/{size}")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)

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
            # streamed as it arrives: a launch that takes twenty minutes in a browser JVM is otherwise
            # twenty minutes of not knowing whether it is progressing or wedged
            stage = reports["last"].get("stage")
            if stage:
                print(f"  [{time.strftime('%H:%M:%S')}] {str(stage)[:160]}", flush=True)
                for line in (reports["last"].get("log") or [])[-3:]:
                    print(f"          {str(line)[:160]}", flush=True)
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
    parser.add_argument("--main", default="webmc.BootTest",
                        help="the Java class to run (with --source, any program against the shim)")
    parser.add_argument("--source", action="append", default=[],
                        help="extra Java source files to compile with the shim")
    parser.add_argument("--page", default=None,
                        help="serve this directory (with index.html) instead of building the boot test")
    parser.add_argument("--path", default="/index.html", help="which page to open when serving --page")
    parser.add_argument("--query", default="")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--timeout", type=int, default=900,
                        help="seconds to wait for a report: a cold CheerpJ downloads a JVM first")
    parser.add_argument("--browser", default=None)
    parser.add_argument("--keep", action="store_true", help="keep the work directory (the browser's log)")
    parser.add_argument("--console", action="store_true", help="print every console line, not a summary")
    parser.add_argument("--fresh-profile", action="store_true",
                        help="wipe the profile first: a half-downloaded CheerpJ runtime in it hangs startup")
    parser.add_argument("--isolate", action="store_true",
                        help="serve with cross-origin isolation (SharedArrayBuffer); see the note in the handler")
    parser.add_argument("--resolve-extra", default="",
                        help="comma separated hosts to also map through 1.1.1.1 (the client's own hosts)")
    parser.add_argument("--profile", default="/tmp/webmc-browser-profile",
                        help="a browser profile that survives runs: CheerpJ downloads its JVM once, and a "
                             "fresh profile per run means paying that download every time (minutes)")
    args = parser.parse_args(argv)

    if args.fresh_profile:
        shutil.rmtree(args.profile, ignore_errors=True)
    # A clone of the warm profile, per run: the profile is what makes CheerpJ's runtime cheap to reuse (it
    # is a download), and it is also what a killed browser leaves locked. Cloning gets both - and `cp -c`
    # clones on APFS, so it costs nothing.
    warm = pathlib.Path(args.profile)
    profile = pathlib.Path(f"{warm}-run-{os.getpid()}")
    shutil.rmtree(profile, ignore_errors=True)
    if warm.is_dir():
        subprocess.run(["cp", "-c", "-R", str(warm), str(profile)], capture_output=True)
    profile.mkdir(parents=True, exist_ok=True)
    browser = args.browser or find_browser()
    if not browser:
        print("no browser found: install Chrome, or let Playwright install one")
        return 2

    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-boot-"))
    reports: dict = {}
    server = None
    try:
        if args.page:
            # a directory or a single file: a client page lives in the site, not alone
            directory = pathlib.Path(args.page).resolve()
            url = None
        else:
            print("compiling the shim and the boot test…")
            sources = [pathlib.Path(source) for source in args.source] or \
                [ROOT / "lwjgl3-webgl" / "test" / "BootTest.java"]
            jar = compile_java(work, extra_sources=sources)
            # the page runs one class by name: rewrite that one line rather than keep a second copy of it
            page = (ROOT / "lwjgl3-webgl" / "test" / "boot.html").read_text()
            page = page.replace('cheerpjRunMain("webmc.BootTest"', f'cheerpjRunMain("{args.main}"')
            (work / "index.html").write_text(page)
            shutil.copytree(ROOT / "lwjgl3-webgl" / "natives", work / "natives", dirs_exist_ok=True)
            print(f"  app.jar: {jar.stat().st_size} bytes")
            directory = work
            url = None

        # a previous run that was killed leaves the port bound, and the error that produces ("Address already
        # in use") looks like a broken tool rather than a stale server
        server = None
        for attempt in range(6):
            try:
                server = collector(directory, args.port + attempt, reports, {"on": args.isolate})
                if attempt:
                    print(f"port {args.port} was busy; using {args.port + attempt}")
                args.port = args.port + attempt
                break
            except OSError:
                continue
        if server is None:
            print(f"no free port between {args.port} and {args.port + 5}")
            return 2
        if url is None:
            url = f"http://127.0.0.1:{args.port}{args.path}{args.query}"
        print(f"serving {directory} on {args.port}")
        print(f"browser: {browser}")

        flags = [browser, "--headless=new", "--no-sandbox", "--window-size=400,400",
                 f"--user-data-dir={profile}", "--enable-logging=stderr", "--v=0"]
        flags += [f"--{name}" for name in BUILTIN]
        # only the host the loader and runtime actually come from: mapping more of them cost a five minute
        # hang that this narrowing fixed, and the extra ones can be asked for by name
        hosts = ["cjrtnc.leaningtech.com"]
        hosts += [host for host in args.resolve_extra.split(",") if host]
        rules = host_resolver_rules(hosts)
        if rules:
            flags.append(f"--host-resolver-rules={rules}")
            print(f"resolver overrides: {rules}")
        flags.append(url)
        log = work / "chrome.log"
        with log.open("w") as handle:
            process = subprocess.Popen(flags, stdout=handle, stderr=subprocess.STDOUT)
        streamed = 0
        try:
            deadline = time.time() + args.timeout
            while time.time() < deadline:
                # The browser's log is the only sign of life for a run that takes minutes - a cold JVM
                # loading a client under Fabric prints as it goes, and waiting for the summary to see it
                # turns a two-minute diagnosis into a twenty-minute one.
                if log.exists():
                    lines = log.read_text(errors="replace").splitlines()
                    fresh = [line for line in lines[streamed:]
                             if any(word in line for word in ("[jvm]", "LAUNCH", "ERROR", "Uncaught", "Exception"))]
                    streamed = len(lines)
                    for line in fresh:
                        text = line.split("CONSOLE:", 1)[1] if "CONSOLE:" in line else line
                        print("  " + text.strip()[:240], flush=True)
                report = reports.get("last")
                if report and report.get("code") in (0, 1, 99):
                    break
                if report and "code" not in report:
                    break
                time.sleep(3)
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
