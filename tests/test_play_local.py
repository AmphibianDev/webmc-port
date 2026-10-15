#!/usr/bin/env python3
"""Play a downloaded client locally: the gateway really is started, and the client really is patched.

The point of `tools/play_local.py` is that a player types a server address and it works. So the test starts it
for real — a real gateway process, a real HTTP server — and checks the three things that make that true: the page
it serves carries the gateway, the gateway answers its own status, and the address it prints is the one it
serves. Nothing is mocked, because a mocked gateway cannot answer a question about multiplayer.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import play_local  # noqa: E402

FAILURES = 0
CHECKS = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES, CHECKS
    CHECKS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


FAKE_CLIENT = """<!doctype html><html><head><title>Eagler26 26.3 (offline)</title></head><body>
<script id="eagler26-offline-meta" type="application/json">{"build": {"minecraftVersion": "26.3"}}</script>
</body></html>
"""


def fetch(url: str, attempts: int = 40) -> tuple[int, bytes]:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return 0, b""


def test_patch() -> None:
    print("\npatching the client, which is the whole trick")
    patched = play_local.patch_client(FAKE_CLIENT.encode(), "ws://127.0.0.1:9/v1/connect").decode()
    check("the gateway is set before the client's own config module can read it",
          patched.index("__eaglerConfig") < patched.index("eagler26-offline-meta"),
          "the client merges its defaults *under* what is already there, so this has to come first")
    check("and removing the script gives the client back byte for byte",
          patched.replace(play_local.gateway_script("ws://127.0.0.1:9/v1/connect"), "") == FAKE_CLIENT,
          "a 200 MB payload should not be reformatted to add one script tag")
    check("a gateway address without a scheme is refused rather than baked in",
          "starts with ws:// or wss://" in _error_of(lambda: play_local.patch_client(FAKE_CLIENT.encode(), "host")) )
    check("a build with nowhere to put a gateway says so",
          "not an Eagler26 client" in _error_of(
              lambda: play_local.patch_client(b"<!doctype html><html><head></head><body></body></html>",
                                              "ws://127.0.0.1:9/v1/connect")))
    check("patching twice does not double the script",
          play_local.patch_client(patched.encode(), "ws://127.0.0.1:9/v1/connect").decode().count("data-webmc-gateway") == 1)


def _error_of(call) -> str:
    try:
        call()
    except SystemExit as error:
        return str(error)
    return ""


def test_running(tmp: pathlib.Path) -> None:
    print("\nrunning it, with a real gateway process")
    if not shutil.which("node"):
        check("node is installed (the gateway is a node program)", False, "install node to run this part")
        return
    client = tmp / "Eagler26-26.3-Full.html"
    client.write_text(FAKE_CLIENT)
    on_disk = client.read_bytes()
    page_port, gateway_port = play_local.free_port(), play_local.free_port()
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "play_local.py"), "--client", str(client), "--no-open",
         "--port", str(page_port), "--gateway-port", str(gateway_port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        status, body = fetch(f"http://127.0.0.1:{page_port}/")
        check("the page is served", status == 200 and b"eagler26-offline-meta" in body, f"status {status}")
        check("and it carries a gateway that points at the one this run started",
              f"ws://127.0.0.1:{gateway_port}/v1/connect".encode() in body)
        script = play_local.gateway_script(f"ws://127.0.0.1:{gateway_port}/v1/connect").encode()
        check("with the rest of the client byte-identical, checked on what was actually served",
              body.replace(script, b"") == on_disk,
              "the payload is 200 MB of somebody else's build; a patch that reformats it is a patch that can "
              + "break it")
        status, health_body = fetch(f"http://127.0.0.1:{page_port}/health")
        health = json.loads(health_body) if health_body else {}
        check("the gateway is really running behind it",
              status == 200 and health.get("ok") is True and health.get("subprotocol") == "goldproxy.v1",
              str(health)[:200])
        check("and it is the one that refuses private targets by default",
              health.get("allowPrivateTargets") is False,
              "a gateway that dials 127.0.0.1 and 10.x is a way into the network it runs on")
        status, gateway_body = fetch(f"http://127.0.0.1:{page_port}/gateway")
        check("the page can be asked which gateway it is using",
              json.loads(gateway_body).get("gateway") == f"ws://127.0.0.1:{gateway_port}/v1/connect")
        check("an unknown path is a 404, not the client by accident", fetch(f"http://127.0.0.1:{page_port}/nope")[0] == 404)
    finally:
        process.terminate()
        try:
            output = process.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            process.kill()
            output = ""
        check("and it prints what a player needs to know",
              "open http://127.0.0.1:" in output and "address box" in output, output[-500:])


def main() -> int:
    test_patch()
    with tempfile.TemporaryDirectory(prefix="webmc-local-") as tmp:
        test_running(pathlib.Path(tmp))
    print(f"\n{CHECKS - FAILURES}/{CHECKS} local play checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
