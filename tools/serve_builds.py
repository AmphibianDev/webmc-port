#!/usr/bin/env python3
"""Serve the builds you have on disk, with a launcher page, at a URL that works right now.

    python3 tools/serve_builds.py                 # patched builds, if there are any
    python3 tools/serve_builds.py --plain         # the unpatched ones
    python3 tools/serve_builds.py --port 8099 --open

It writes (and refreshes) an index.html in the folder it serves, listing every build it finds with
its size and a Play link, then serves that folder over http://127.0.0.1:<port>/. Nothing leaves the
machine: this is the local equivalent of a published launcher, which is the only kind that hosts no
game files anywhere but your disk.

The page also carries the two lines a mod needs, so a `.js` mod can be tried immediately:

    http://127.0.0.1:8099/<folder>/<file>.html?mod=http://127.0.0.1:8099/mods/my-mod.js
"""

from __future__ import annotations

import argparse
import functools
import html
import http.server
import pathlib
import socketserver
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_PORT = 8099

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>WebMC — builds on this machine</title>
<style>
  :root {{ color-scheme: dark; --panel: #1c1d20; --panel-2: #24262a; --line: #34373c;
           --text: #e9ecef; --muted: #9aa3ad; --green-lit: #57a83b; --dirt-a: #4a3524; --dirt-b: #3b2a1c; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #101113; color: var(--text);
    font: 14px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  header {{ display: flex; align-items: center; gap: 12px; padding: 18px 22px;
    border-bottom: 1px solid var(--line); }}
  .logo {{ width: 42px; height: 42px; display: grid; place-items: center; font-size: 22px; border-radius: 8px;
    border: 1px solid #57402a;
    background: repeating-linear-gradient(45deg, var(--dirt-a) 0 6px, var(--dirt-b) 6px 12px); }}
  h1 {{ font-size: 17px; margin: 0; }}
  .muted {{ color: var(--muted); }}
  main {{ padding: 20px 22px 40px; }}
  .grid {{ display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }}
  .v {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px; padding: 12px; }}
  .v b {{ font-size: 15px; }}
  .v .meta {{ color: var(--muted); font-size: 12px; margin: 3px 0 8px; }}
  a.play {{ display: inline-flex; align-items: center; gap: 8px; padding: 9px 13px; border-radius: 7px;
    background: linear-gradient(180deg, var(--green-lit), #3c8527); border: 1px solid #2c6419;
    color: #f3fff0; font-weight: 700; text-decoration: none; text-transform: uppercase;
    letter-spacing: .05em; font-size: 12.5px; }}
  code {{ background: #0f1620; border: 1px solid var(--line); border-radius: 6px; padding: 1px 5px; }}
  pre {{ background: #0f1620; border: 1px solid var(--line); border-radius: 10px; padding: 12px;
    overflow: auto; font-size: 12.5px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 14px; margin-bottom: 16px; }}
  details summary {{ cursor: pointer; color: var(--muted); }}
</style>
</head>
<body>
<header>
  <div class="logo">⛏</div>
  <div><h1>WebMC — builds on this machine</h1>
    <div class="muted small">{count} build(s) in <code>{root}</code> · served from <code>{base}</code></div></div>
</header>
<main>
  <div class="card">
    <b>Mods</b>
    <p class="muted small" style="margin: 6px 0 8px">
      Every build here already carries the WebMC mod API (page tier: HUD overlay, keybinds, chat,
      input, tick/render events). Add a mod by appending its url:
    </p>
    <pre>{example}</pre>
    <p class="muted small" style="margin: 8px 0 0">
      Repeat <code>&amp;mod=</code> for several; put <code>.js</code> files in <code>{root}/mods/</code>
      and they are served from there. A mod looks like:
    </p>
    <pre>if (globalThis.WebMCModAPI) {{
  WebMCModAPI.on("load", () => WebMCModAPI.displayToChat("my-mod loaded"));
  WebMCModAPI.registerKeybind("Toggle HUD", "h", () => toggle());
}}</pre>
  </div>
  <div class="grid">{cards}</div>
</main>
</body>
</html>
"""

CARD = """    <div class="v">
      <b>{label}</b>
      <div class="meta">{size:.1f} MB{extra}</div>
      <a class="play" href="{href}" target="_blank" rel="noopener">▶ Play</a>
    </div>"""


def find_builds(root: pathlib.Path) -> list[dict]:
    """Every HTML build under root, biggest-version-first, ignoring the launcher itself."""
    builds = []
    for path in sorted(root.rglob("*.html")):
        if path == root / "index.html":
            continue          # the launcher's own page, not a build

        relative = path.relative_to(root)
        builds.append({
            "folder": "" if str(relative.parent) == "." else urllib.parse.quote(str(relative.parent)),
            "file": urllib.parse.quote(path.name),
            "label": f"{relative.parent.name or 'root'} — {path.stem}".strip(" —"),
            "size_mb": path.stat().st_size / 1048576,
            "modded": ".modded." in path.name,
        })
    return sorted(builds, key=lambda b: (-b["size_mb"]))


def page(root: pathlib.Path, base: str) -> str:
    builds = find_builds(root)
    cards = "\n".join(
        CARD.format(label=html.escape(build["label"]), size=build["size_mb"],
                    extra=" · mod API injected" if build["modded"] else " · no mod API",
                    href=f"{base}/{build['folder']}/{build['file']}")
        for build in builds)
    example = f"{base}/<folder>/<file>.html?mod={base}/mods/my-mod.js"
    return PAGE.format(count=len(builds), root=html.escape(str(root)), base=html.escape(base),
                       cards=cards, example=html.escape(example))


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter: one line per request
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the builds on this machine, with a launcher page")
    parser.add_argument("--root", default=None, help="patched builds by default, --plain for the originals")
    parser.add_argument("--plain", action="store_true", help="serve work/builds instead of work/builds-modded")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="open the page in the default browser")
    args = parser.parse_args()

    root = pathlib.Path(args.root) if args.root else ROOT / "work" / ("builds" if args.plain else "builds-modded")
    if not root.is_dir():
        print(f"{root} does not exist yet:")
        print("  python3 tools/fetch_builds.py                  # download the builds")
        print("  python3 mods/injector/inject_mod_api.py --all  # add the mod API")
        return 2

    (root / "mods").mkdir(exist_ok=True)
    base = f"http://{args.host}:{args.port}"
    (root / "index.html").write_text(page(root, base))

    with Server((args.host, args.port), functools.partial(Handler, directory=str(root))) as httpd:
        builds = find_builds(root)
        print(f"\n  {len(builds)} build(s) in {root}")
        print(f"  open: {base}/")
        print(f"  first three:")
        for build in builds[:3]:
            link = f"{base}/{build['folder']}/{build['file']}".replace("//", "/").replace(":/", "://")
            print(f"    {build['label'][:44]:<46} {build['size_mb']:>6.1f} MB  {link}")
        print(f"\n  mods go in {root}/mods/  ->  append ?mod={base}/mods/your-mod.js")
        print("  Ctrl-C to stop\n")
        if args.open:
            import webbrowser
            webbrowser.open(f"{base}/")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
