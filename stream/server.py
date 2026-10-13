#!/usr/bin/env python3
"""Stream Minecraft from this machine into any browser. No host, no account, no dependencies.

    python3 stream/server.py                 # then open http://127.0.0.1:8770/host on the host
    python3 stream/server.py --port 8770 --no-input

Why this instead of a hosting provider: streaming is sustained CPU/GPU and sustained bandwidth, and
every free host meters exactly that - Vercel paused a Hobby team for a looping background video, and
Cloudflare's terms route video through paid products. Running it on the machine that already has the
game costs nothing and breaks nobody's terms. This machine is the host.

How it works: the host page captures a window with the browser's own screen capture, and WebRTC sends
it to the viewers - peer to peer, direct, with the video never passing through a server. All this
process does is:

  * serve the two pages
  * carry the WebRTC handshake (offers, answers, ICE candidates) as WebSocket messages
  * inject the viewers' keyboard and mouse into this machine's session, so the game actually responds

Video never touches it. Only the handshake and input do, so its CPU cost is irrelevant next to the
encoder's.

Permissions it will ask for, once, the first time it matters:

  * Screen Recording, for the browser that captures the window
  * Accessibility, for this process to move the mouse and press keys (macOS will prompt you)
"""

from __future__ import annotations

import argparse
import base64
import os
import hashlib
import json
import pathlib
import secrets
import socket
import struct
import sys
import threading
import time
import weakref
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Sockets that stopped being HTTP requests and became WebSockets. The set holds them weakly so a
# finished connection is forgotten as soon as both sides are done with it.
UPGRADED: "weakref.WeakSet" = weakref.WeakSet()

# macOS virtual key codes, because that is what the injector needs and the browser knows the physical
# key. Sending `event.code` and translating here keeps the mapping in one place.
MAC_KEYCODES = {
    "KeyA": 0, "KeyS": 1, "KeyD": 2, "KeyF": 3, "KeyH": 4, "KeyG": 5, "KeyZ": 6, "KeyX": 7,
    "KeyC": 8, "KeyV": 9, "KeyB": 11, "KeyQ": 12, "KeyW": 13, "KeyE": 14, "KeyR": 15, "KeyY": 16,
    "KeyT": 17, "Digit1": 18, "Digit2": 19, "Digit3": 20, "Digit4": 21, "Digit6": 22, "Digit5": 23,
    "Equal": 24, "Digit9": 25, "Digit7": 26, "Minus": 27, "Digit8": 28, "Digit0": 29,
    "BracketRight": 30, "KeyO": 31, "KeyU": 32, "BracketLeft": 33, "KeyI": 34, "KeyP": 35,
    "Enter": 36, "KeyL": 37, "KeyJ": 38, "Quote": 39, "KeyK": 40, "Semicolon": 41, "Backslash": 42,
    "Comma": 43, "Slash": 44, "KeyN": 45, "KeyM": 46, "Period": 47, "Tab": 48, "Space": 49,
    "Backquote": 50, "Backspace": 51, "Escape": 53, "MetaLeft": 55, "ShiftLeft": 56, "CapsLock": 57,
    "AltLeft": 58, "ControlLeft": 59, "ShiftRight": 60, "AltRight": 61, "ControlRight": 62,
    "F17": 64, "NumpadDecimal": 65, "NumpadMultiply": 67, "NumpadAdd": 69, "NumLock": 71,
    "NumpadDivide": 75, "NumpadEnter": 76, "NumpadSubtract": 78, "F18": 79, "F19": 80,
    "NumpadEqual": 81, "Numpad0": 82, "Numpad1": 83, "Numpad2": 84, "Numpad3": 85, "Numpad4": 86,
    "Numpad5": 87, "Numpad6": 88, "Numpad7": 89, "F20": 90, "Numpad8": 91, "Numpad9": 92,
    "F5": 96, "F6": 97, "F7": 98, "F3": 99, "F8": 100, "F9": 101, "F11": 103, "F13": 105,
    "F16": 106, "F14": 107, "F10": 109, "F12": 111, "F15": 113, "Home": 115, "PageUp": 116,
    "Delete": 117, "F4": 118, "End": 119, "F2": 120, "PageDown": 121, "F1": 122, "ArrowLeft": 123,
    "ArrowRight": 124, "ArrowDown": 125, "ArrowUp": 126,
}

MOUSE_BUTTONS = {0: "left", 1: "middle", 2: "right"}


class Injector:
    """Turns the viewers' input events into real events on this machine."""

    def __init__(self, enabled: bool = True, dry_run: bool = False):
        self.enabled = enabled
        self.dry_run = dry_run
        self.seen: list[dict] = []
        self._quartz = None
        if enabled and not dry_run:
            try:
                import Quartz  # noqa: PLC0415 - only needed on macOS, only when injecting
                self._quartz = Quartz
            except Exception as error:  # noqa: BLE001
                print(f"  ! input injection unavailable ({error}); viewers can watch but not play")
                self.enabled = False

    def handle(self, event: dict) -> None:
        self.seen.append(event)
        if len(self.seen) > 200:
            del self.seen[:100]
        if not self.enabled:
            return
        kind = event.get("t")
        if kind == "key":
            self._key(event)
        elif kind == "move":
            self._mouse("move", event.get("x", 0), event.get("y", 0))
        elif kind == "button":
            self._mouse("down" if event.get("down") else "up", event.get("x", 0), event.get("y", 0),
                        event.get("button", 0))
        elif kind == "scroll":
            self._scroll(event.get("dx", 0), event.get("dy", 0))

    def _key(self, event: dict) -> None:
        code = MAC_KEYCODES.get(str(event.get("code")))
        if code is None:
            return
        quartz = self._quartz
        press = bool(event.get("down"))
        if quartz is not None:
            event_ref = quartz.CGEventCreateKeyboardEvent(None, code, press)
            quartz.CGEventPost(quartz.kCGHIDEventTap, event_ref)
        elif self.dry_run:
            pass

    def _mouse(self, action: str, x: float, y: float, button: int = 0) -> None:
        quartz = self._quartz
        if quartz is None:
            return
        name = MOUSE_BUTTONS.get(int(button), "left")
        event_type = {
            "move": quartz.kCGEventMouseMoved,
            "down": quartz.kCGEventLeftMouseDown if name == "left" else quartz.kCGEventRightMouseDown,
            "up": quartz.kCGEventLeftMouseUp if name == "left" else quartz.kCGEventRightMouseUp,
        }[action]
        event_ref = quartz.CGEventCreateMouseEvent(None, event_type, (float(x), float(y)),
                                                   quartz.kCGMouseButtonLeft if name == "left"
                                                   else quartz.kCGMouseButtonRight)
        quartz.CGEventPost(quartz.kCGHIDEventTap, event_ref)

    def _scroll(self, dx: float, dy: float) -> None:
        quartz = self._quartz
        if quartz is None or not dy:
            return
        event_ref = quartz.CGEventCreateScrollWheelEvent(None, quartz.kCGScrollEventUnitLine, 1, int(dy))
        quartz.CGEventPost(quartz.kCGHIDEventTap, event_ref)


class Server(ThreadingHTTPServer):
    """An HTTP server that lets a socket go when it has become a WebSocket.

    socketserver closes every request socket once the handler returns, which is right for HTTP and
    fatal for a WebSocket: the connection was handed to another thread, and the close showed up as
    "Bad file descriptor" the moment either end spoke. A socket that was upgraded is marked, and these
    two hooks leave it alone.
    """

    def shutdown_request(self, request) -> None:  # noqa: D102 - see the class docstring
        if request in UPGRADED:
            return
        super().shutdown_request(request)

    def close_request(self, request) -> None:  # noqa: D102
        if request in UPGRADED:
            return
        super().close_request(request)


class Hub:
    """Who is connected, and who a message is for."""

    def __init__(self, injector: Injector):
        self.injector = injector
        self.host: "Connection | None" = None
        self.viewers: dict[int, "Connection"] = {}
        self.next_id = 1
        self.lock = threading.Lock()

    def describe(self) -> dict:
        with self.lock:
            return {
                "host": self.host is not None,
                "viewers": list(self.viewers),
                "injected": len(self.injector.seen),
                "input": self.injector.enabled,
            }

    def attach(self, connection: "Connection") -> None:
        with self.lock:
            if connection.role == "host":
                self.host = connection
                print(f"  host connected from {connection.peer}")
            else:
                connection.viewer_id = self.next_id
                self.next_id += 1
                self.viewers[connection.viewer_id] = connection
                print(f"  viewer {connection.viewer_id} connected from {connection.peer}")
        if connection.role != "host":
            self.to_host({"t": "viewer", "id": connection.viewer_id})

    def detach(self, connection: "Connection") -> None:
        with self.lock:
            if connection.role == "host" and self.host is connection:
                self.host = None
                viewers = list(self.viewers.values())
            else:
                self.viewers.pop(connection.viewer_id, None)
                viewers = []
        for viewer in viewers:
            viewer.send({"t": "host-gone"})
        if connection.role != "host":
            self.to_host({"t": "viewer-left", "id": connection.viewer_id})
            print(f"  viewer {connection.viewer_id} left")

    def to_host(self, message: dict) -> None:
        with self.lock:
            host = self.host
        if host is not None:
            host.send(message)

    def to_viewer(self, viewer_id: int, message: dict) -> None:
        with self.lock:
            viewer = self.viewers.get(int(viewer_id))
        if viewer is not None:
            viewer.send(message)

    def from_message(self, connection: "Connection", message: dict) -> None:
        kind = message.get("t")
        # Who sent it decides where it goes: both sides send candidates, so routing by type alone
        # sent the viewer's candidates to the viewer and dropped them.
        if kind == "offer":
            self.to_viewer(message.get("to"), message)
        elif kind == "answer":
            self.to_host({**message, "from": connection.viewer_id})
        elif kind == "candidate":
            if connection.role == "host":
                self.to_viewer(message.get("to"), message)
            else:
                self.to_host({**message, "from": connection.viewer_id})
        elif kind == "input":
            self.injector.handle(message.get("event") or {})
        elif kind == "ping":
            connection.send({"t": "pong", "at": message.get("at")})


class Connection:
    """One WebSocket connection, spoken by hand so that nothing has to be installed."""

    def __init__(self, sock: socket.socket, address, hub: Hub, role: str):
        self.sock = sock
        self.peer = f"{address[0]}:{address[1]}"
        self.hub = hub
        self.role = role
        self.viewer_id = 0
        self.alive = True
        self.send_lock = threading.Lock()

    # ---- framing -----------------------------------------------------------------------------

    def read_exactly(self, count: int) -> bytes:
        chunks = b""
        while len(chunks) < count:
            piece = self.sock.recv(count - len(chunks))
            if not piece:
                raise ConnectionError("closed")
            chunks += piece
        return chunks

    def read_frame(self) -> tuple[int, bytes]:
        header = self.read_exactly(2)
        first, second = header[0], header[1]
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", self.read_exactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self.read_exactly(8))[0]
        mask = self.read_exactly(4) if masked else b""
        payload = self.read_exactly(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < (1 << 16):
            header.append(126)
            header += struct.pack(">H", length)
        else:
            header.append(127)
            header += struct.pack(">Q", length)
        with self.send_lock:
            self.sock.sendall(bytes(header) + payload)

    def send(self, message: dict) -> None:
        try:
            self.send_frame(1, json.dumps(message).encode("utf-8"))
        except Exception:  # noqa: BLE001 - a dead viewer must not break the host's stream
            self.alive = False

    # ---- the conversation ---------------------------------------------------------------------

    def run(self) -> None:
        self.hub.attach(self)
        buffer = b""
        try:
            while self.alive:
                opcode, payload = self.read_frame()
                if opcode == 8:
                    break
                if opcode == 9:
                    self.send_frame(10, payload)
                    continue
                if opcode == 10:
                    continue
                if opcode == 0:
                    buffer += payload
                    continue
                buffer = payload
                if not buffer:
                    continue
                try:
                    self.hub.from_message(self, json.loads(buffer.decode("utf-8")))
                except Exception as error:  # noqa: BLE001
                    print(f"  ! bad message from {self.peer}: {error}")
        except ConnectionError:
            pass                      # the other end went away: normal
        except Exception as error:  # noqa: BLE001
            print(f"  ! {self.role} {self.peer}: {type(error).__name__}: {error}")
        finally:
            self.alive = False
            try:
                self.sock.close()
            except Exception:  # noqa: BLE001
                pass
            self.hub.detach(self)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hub: Hub = None            # set by serve()
    streams: pathlib.Path = HERE

    def log_message(self, fmt: str, *args) -> None:  # quieter than the default
        if self.path.startswith("/health"):
            return
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
        path = self.path.split("?")[0]
        if path == "/health":
            return self.reply_json(self.hub.describe())
        if path in ("/", "/watch", "/watch.html"):
            return self.reply_file("watch.html", "text/html; charset=utf-8")
        if path in ("/host", "/host.html"):
            return self.reply_file("host.html", "text/html; charset=utf-8")
        if path == "/signal" and self.headers.get("Upgrade", "").lower() == "websocket":
            return self.upgrade()
        self.reply_json({"error": "not found", "try": ["/", "/host", "/health"]}, status=404)

    def reply_json(self, document: dict, status: int = 200) -> None:
        body = json.dumps(document, indent=1).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def reply_file(self, name: str, content_type: str) -> None:
        target = self.streams / name
        if not target.is_file():
            return self.reply_json({"error": f"{name} is missing"}, status=404)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def upgrade(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        role = "host" if "role=host" in self.path else "viewer"
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        # The socket now belongs to the WebSocket thread. BaseHTTPRequestHandler.finish() would close
        # the file wrappers - and closing a socket's makefile() wrapper closes the socket itself - so
        # finish() is told to leave it alone (see below), and close_connection stops the request loop.
        self._upgraded = True
        # a socket object does not accept new attributes, so upgraded ones are remembered here
        UPGRADED.add(self.connection)
        connection = Connection(self.connection, self.client_address, self.hub, role)
        threading.Thread(target=connection.run, daemon=True).start()
        self.close_connection = True

    def finish(self) -> None:
        if getattr(self, "_upgraded", False):
            try:
                self.wfile.flush()
            except Exception:  # noqa: BLE001 - the websocket may already be gone
                pass
            return
        super().finish()


def local_addresses() -> list[str]:
    """The addresses a friend on the same network (or the same tailnet) could use."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found:
                found.append(address)
    except Exception:  # noqa: BLE001
        pass
    return found


def serve(port: int, host: str, injector: Injector) -> ThreadingHTTPServer:
    Handler.hub = Hub(injector)
    server = Server((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--host", default="0.0.0.0", help="bind address; 127.0.0.1 keeps it local")
    parser.add_argument("--no-input", action="store_true", help="viewers watch but cannot play")
    parser.add_argument("--dry-run-input", action="store_true", help="record input, inject nothing")
    parser.add_argument("--seconds", type=int, default=0, help="stop after this long (for tests)")
    args = parser.parse_args(argv)

    injector = Injector(enabled=not args.no_input, dry_run=args.dry_run_input)
    server = serve(args.port, args.host, injector)
    host_page = f"http://127.0.0.1:{args.port}/host"
    watch_page = f"http://127.0.0.1:{args.port}/"
    print(f"  host page : {host_page}   (open this here, then share the window)")
    for address in local_addresses():
        print(f"  for friends: http://{address}:{args.port}/")
    print(f"  health    : http://127.0.0.1:{args.port}/health")
    print(f"  input     : {'viewers can play' if injector.enabled else 'watch only'}")
    print("  video is peer to peer; this server only carries the handshake and input")
    if args.seconds:
        time.sleep(args.seconds)
        server.shutdown()
        return 0
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n  stopping")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
