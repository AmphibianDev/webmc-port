#!/usr/bin/env python3
"""Tests for the self-hosted streamer: the pages, the handshake relay, and safe input handling.

    python3 stream/test_stream.py

Real server, real WebSocket frames, hand-written client - no browser needed for any of it. Input
injection is off in every test, so running the suite can never move the mouse.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
STREAM = ROOT / "stream"
sys.path.insert(0, str(STREAM))

import server as streamer  # noqa: E402

FAILURES = 0
PORT = int(os.environ.get("WEBMC_TEST_STREAM_PORT", "8799"))


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


class Client:
    """The smallest WebSocket client that can test a WebSocket server."""

    def __init__(self, host: str, port: int, role: str):
        self.sock = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        path = "/signal" + ("?role=host" if role == "host" else "")
        request = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(request.encode())
        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            self.buffer += self.sock.recv(4096)
        head, _, rest = self.buffer.partition(b"\r\n\r\n")
        self.buffer = rest
        assert b"101" in head.split(b"\r\n")[0], head[:80]

    def send(self, message: dict) -> None:
        payload = json.dumps(message).encode()
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        else:
            header.append(0x80 | 126)
            header += struct.pack(">H", len(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _fill(self, count: int) -> None:
        while len(self.buffer) < count:
            piece = self.sock.recv(4096)
            if not piece:
                raise ConnectionError("closed")
            self.buffer += piece

    def receive(self, timeout: float = 5.0) -> dict:
        self.sock.settimeout(timeout)
        self._fill(2)
        second = self.buffer[1]
        length = second & 0x7F
        offset = 2
        if length == 126:
            self._fill(4)
            length = struct.unpack(">H", self.buffer[2:4])[0]
            offset = 4
        self._fill(offset + length)
        payload = self.buffer[offset:offset + length]
        self.buffer = self.buffer[offset + length:]
        return json.loads(payload.decode())

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


def get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, ""
    except Exception:  # noqa: BLE001 - not up yet is a status, not a crash
        return 0, ""


def main() -> int:
    print("the pages the host and a viewer get")
    log_path = pathlib.Path(tempfile.gettempdir()) / "webmc-stream-test.log"
    log_file = log_path.open("wb")
    process = subprocess.Popen([sys.executable, "-u", str(STREAM / "server.py"), "--port", str(PORT),
                                "--host", "127.0.0.1", "--no-input"],
                               stdout=log_file, stderr=subprocess.STDOUT)
    try:
        for _ in range(40):
            status, _body = get("/health")
            if status == 200:
                break
            time.sleep(0.25)

        status, health = get("/health")
        if status != 200:
            print("  the server did not start; it said:")
            print("   ", log_path.read_text().strip()[-800:] or "(nothing)")
        check("the server answers /health", status == 200, str(status))
        check("and says input is off in this run", json.loads(health)["input"] is False)

        status, host_page = get("/host")
        check("the host page is served", status == 200 and "Share a window" in host_page)
        check("it captures with the browser's own screen capture",
              "getDisplayMedia" in host_page and "contentHint" in host_page)
        check("it sets a bitrate per viewer", "maxBitrate" in host_page and "setParameters" in host_page)

        status, watch_page = get("/")
        check("the viewer page is served at the root", status == 200 and "Take control" in watch_page)
        check("it sends input only while control is held",
              "if (!control)" in watch_page and 't: "key"' in watch_page)
        check("Esc in the page hands control back",
              'event.code === "Escape" && control' in watch_page)

        print()
        print("the handshake, relayed between two browsers")
        host = Client("127.0.0.1", PORT, "host")
        viewer = Client("127.0.0.1", PORT, "viewer")
        viewer.send({"t": "join"})
        notice = host.receive()
        check("the host is told a viewer arrived", notice.get("t") == "viewer" and "id" in notice, str(notice))
        viewer_id = notice["id"]

        host.send({"t": "offer", "to": viewer_id, "sdp": {"type": "offer", "sdp": "v=0 test"}})
        offer = viewer.receive()
        check("the offer reaches that viewer", offer.get("sdp", {}).get("sdp") == "v=0 test", str(offer)[:80])

        viewer.send({"t": "answer", "sdp": {"type": "answer", "sdp": "v=0 answer"}})
        answer = host.receive()
        check("the answer comes back tagged with the viewer", answer.get("sdp", {}).get("sdp") == "v=0 answer"
              and answer.get("from") == viewer_id, str(answer)[:80])

        viewer.send({"t": "candidate", "candidate": {"candidate": "candidate:1 1 udp 1 1.2.3.4 1 typ host"}})
        candidate = host.receive()
        check("ICE candidates are relayed too", "candidate:1" in candidate.get("candidate", {}).get("candidate", ""))

        print()
        print("input never reaches the other browser, and does not move anything here")
        viewer.send({"t": "input", "event": {"t": "move", "x": 10, "y": 10}})
        viewer.send({"t": "input", "event": {"t": "key", "code": "KeyW", "down": True}})
        time.sleep(0.4)
        state = json.loads(get("/health")[1])
        check("the server says it injected nothing (input disabled)",
              state["injected"] >= 2 and state["input"] is False, str(state))

        print()
        print("a viewer leaving is noticed")
        viewer.close()
        left = host.receive()
        check("the host is told the viewer left", left.get("t") == "viewer-left", str(left))
        host.close()

        print()
        print("the switch it must never flip in this repository")
        workflow_text = "\n".join(path.read_text().lower()
                                  for path in (ROOT / ".github" / "workflows").glob("*.yml"))
        check("no workflow starts this streamer", "stream/server.py" not in workflow_text
              and "host.sh" not in workflow_text)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log_file.close()

    print()
    if FAILURES == 0:
        print("stream tests passed")
    else:
        print(f"{FAILURES} stream test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
