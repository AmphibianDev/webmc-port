#!/usr/bin/env python3
"""Put WebMC's boot screen in front of a compiled client, and take the client's own one out.

    python3 tools/aot_shell.py --client dist/26.4-snapshot-4.html --out dist/26.4-boot.html \\
        --remove '#splash,#boot-overlay,#loading'
    python3 tools/aot_shell.py --client dist/x.html --check
    python3 tools/aot_shell.py --shell-template --out dist/shell.html       # for a wasm-JVM kit

Why: every compiled client (a TeaVM build, a Gaius build, an engine of our own) ships its own loading
UI, and they look nothing like the game - so the first thing a player sees is somebody's web page, and
then it is replaced by Minecraft. `site/boot/shell.html` copies the screen the game itself draws
(black, centred progress text, a 200x2 bar, the game's GUI scale), and this tool puts it in front of a
client and gets it out of the way again.

Two insertion points, because the engines differ:

  * a single-file client HTML -> the shell goes in after <body>, the old UI is removed by id, and a
    small wiring script hands over on the first OpenGL draw call;
  * a wasm-JVM kit (openjdk21-wasm-link) -> the same file is the kit's page shell, with its @SCRIPT@
    placeholder left for the linker: that is what --shell-template writes.

What this deliberately does not do: parse HTML properly, or guess at a client's internal loader. It
injects, removes ids, patches one WebGL entry point, and reports exactly what it did - because a silent
no-op here is a player looking at somebody's splash screen, and a silent wrong edit is a client that
does not boot.
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SHELL = ROOT / "site" / "boot" / "shell.html"

MARKER = 'id="webmc-boot"'
WIRING_MARK = "Injected by tools/aot_shell.py"

# The script injected with the shell: it patches the GL context, so the hand-over happens when the
# game starts drawing, whatever the client is and with no cooperation from it.
WIRING = f"""
<script>
/* {WIRING_MARK} - the hand-over, with no cooperation needed from the client. */
(function () {{
  "use strict";
  if (!window.webmcBoot) {{ return; }}          // the shell did not load: nothing to hand over to
  var handedOver = false;
  function handOver() {{
    if (handedOver) {{ return; }}
    handedOver = true;
    /* The game has drawn. Give it a few frames to finish its first screen, then fade: hiding on the
       very first clear() would show a half-drawn title screen. */
    setTimeout(function () {{ window.webmcBoot.ready(); }}, 400);
  }}
  function patch(proto) {{
    if (!proto) {{ return 0; }}
    var patched = 0;
    ["drawElements", "drawArrays", "clear"].forEach(function (method) {{
      var original = proto[method];
      if (typeof original !== "function") {{ return; }}
      proto[method] = function () {{
        handOver();
        return original.apply(this, arguments);
      }};
      patched++;
    }});
    return patched;
  }}
  var count = patch(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype)
            + patch(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
  document.addEventListener("webmc:ready", function () {{ handedOver = true; }});
  if (!count && window.webmcBootHideOnLoad) {{ window.addEventListener("load", handOver); }}
}})();
</script>
"""


def shell_text() -> str:
    if not SHELL.is_file():
        raise SystemExit(f"{SHELL} is missing: the boot screen is part of this repository")
    return SHELL.read_text()


def style_block(dirt: pathlib.Path | None) -> str:
    """The shell's own CSS, plus the real dirt texture when a build can supply one."""
    block = re.search(r"<style>([\s\S]*?)</style>", shell_text())
    if not block:
        raise SystemExit(f"{SHELL} has no style block")
    css = block.group(1)
    if dirt:
        data = base64.b64encode(dirt.read_bytes()).decode()
        css += (f"\n  /* the real texture, passed in by the build (--dirt {dirt.name}) */\n"
                f"  #webmc-boot.dirt {{ --webmc-dirt: url(\"data:image/png;base64,{data}\"); }}\n")
    return f"<style>{css}</style>\n"


def overlay(style: str, dirt: pathlib.Path | None) -> str:
    """The overlay markup and its script: taken from the shell, so there is one implementation."""
    body = re.search(r"<body>\s*([\s\S]*?)\s*</body>", shell_text())
    if not body:
        raise SystemExit(f"{SHELL} has no body")
    markup = body.group(1).strip()
    # a compiled client does not need the X11 mount point: that is for the wasm-JVM route, which uses
    # the shell as its page template instead of injecting it
    markup = markup.replace('<div id="x11-wasm-root"></div>', "").strip()
    if style == "dirt":
        markup = markup.replace(MARKER, MARKER + ' class="dirt"', 1)
    return markup + "\n"


def remove_element(html: str, selector: str) -> tuple[str, str]:
    """Remove one element by #id. Returns (html, what happened)."""
    wanted = selector.strip()
    if not wanted.startswith("#"):
        return html, f"skipped {wanted!r}: only #id selectors are supported"
    element_id = wanted[1:]
    paired = re.compile(r"<(\w+)([^>]*\bid=\"%s\"[^>]*)>[\s\S]*?</\1>\s*" % re.escape(element_id), re.I)
    if paired.search(html):
        return paired.sub("", html, count=1), f"removed the element with {wanted}"
    void = re.compile(r"<\w+[^>]*\bid=\"%s\"[^>]*/?>\s*" % re.escape(element_id), re.I)
    if void.search(html):
        return void.sub("", html, count=1), f"removed the empty element {wanted}"
    return html, f"not found in this client: {wanted}"


def build(client: str, style: str, dirt: pathlib.Path | None, remove: list[str], hide_on_gl: bool,
          log=print) -> tuple[str, dict]:
    """The whole edit: take the client's loading UI out, put ours in, wire the hand-over."""
    report = {"removed": [], "injected": False, "bytes_before": len(client), "style": style}

    html = client
    # the engine's own chrome first, then whatever the caller asked for
    for selector in tuple(ENGINE_CHROME_REMOVE) + tuple(remove):
        html, what = remove_element(html, selector)
        report["removed"].append(what)
        log(f"  {what}")

    if MARKER in html:
        log("  the boot screen is already in this client: left alone (this tool is idempotent)")
    else:
        open_body = re.search(r"<body[^>]*>", html, re.I)
        if not open_body:
            raise SystemExit("this file has no <body>: it is not an HTML client, so the shell has to "
                             "be inserted by that engine's packager instead")
        markup = overlay(style, dirt)
        html = html[:open_body.end()] + "\n" + markup + html[open_body.end():]
        css = style_block(dirt)
        # the engine's leftover chrome is hidden rather than removed where its own script looks for it, and
        # the selector covers whatever it builds later with its marker attribute
        css += ("<style>/* WebMC: the engine's own launcher chrome is not part of this page */\n"
                + ENGINE_CHROME_SELECTOR + " { display: none !important; }</style>\n")
        html = html.replace("</head>", css + "</head>", 1) if "</head>" in html else css + html
        report["injected"] = True
        log(f"  injected the boot screen ({len(markup)} bytes of markup, {len(css)} bytes of style)")

    if report["injected"] and hide_on_gl and WIRING_MARK not in html:
        html = html.replace("</body>", WIRING + "</body>", 1) if "</body>" in html else html + WIRING
        log("  wired the hand-over to the first OpenGL draw call, so no client has to cooperate")
    elif hide_on_gl is False:
        log("  the hand-over is the client's job: call webmcBoot.ready() or fire 'webmc:ready'")

    report["bytes_after"] = len(html)
    return html, report


# Names that belong to the engines we borrow rather than to this project. Credited in CREDITS.md, and
# stripped from the page (not from the compiled code - see strip_brand for why).
BRANDS = ("eaglercraft", "eagler", "lax1dude", "gaius", "typetheory", "browsercraft", "eaglerforge",
          "resent", "koneclient")

# The engine's own chrome, by the ids it uses. Removed when it is a block of branding, hidden when it is
# something the client's own script needs to find: taking an element out from under a script is a client
# that stops on a null, so hiding is the safe half of the same idea.
ENGINE_CHROME_REMOVE = ("#boot-brand",)          # the "GAIUS CLIENT" / "MOJANG STUDIOS" block
ENGINE_CHROME_HIDE = ("#boot-screen",             # the engine's own boot overlay: ours is the loading screen
                      "#gaius-shell-header", "#gaius-shell-footer", "#gaius-error-details",
                      "#profile-kicker", "#profile-subtitle", "#profile-legal",
                      # the two overlays a player actually looks at in game: a console panel across the top and a
                      # performance readout in the corner, both fixed and both visible by default. Hidden rather
                      # than removed, because the engine's own script writes into them.
                      "#status", "#perf-hud",
                      # the engine's own console: it prints its name and its diagnostics there, and a player
                      # reads every word of it if it is left visible
                      "#status", "#log")
# The hide rule uses the ids and the marker attribute the engine sets on anything it builds at run time.
ENGINE_CHROME_SELECTOR = ", ".join(ENGINE_CHROME_HIDE) + ', [data-gaius-shell]' 


def brand_hits(client: str, brands: tuple = BRANDS) -> dict:
    """Where borrowed branding appears, by context: ids/classes, visible text, and everything else."""
    lower = client.lower()
    hits = {"ids": [], "titles": [], "scripts": [], "other": []}
    for brand in brands:
        if brand not in lower:
            continue
        if f'id="{brand}' in lower or f'class="{brand}' in lower or f'{brand}-' in lower:
            hits["ids"].append(brand)
        for tag in ("title", "h1", "h2", "h3"):
            for match in re.findall(rf"<{tag}[^>]*>([\s\S]{{0,200}}?)</{tag}>", client, re.I):
                if brand in match.lower():
                    hits["titles"].append(f"{tag}: {match.strip()[:60]}")
        if re.search(rf"<script[^>]*>[\s\S]*?{re.escape(brand)}", client, re.I):
            hits["scripts"].append(brand)
        hits["other"].append(brand)
    return {key: sorted(set(value)) for key, value in hits.items()}


def strip_brand(client: str, brands: tuple = BRANDS, log=print) -> tuple[str, dict]:
    """Take borrowed branding out of the *page*: titles, headings, and elements named after it.

    What this deliberately does not do: rewrite the compiled JavaScript or wasm. A brand token inside
    somebody else's code is often an identifier, a field name or part of a URL, and blind replacement
    breaks the client - so those occurrences stay and CREDITS.md is what attributes them. The page is
    what a player sees, and that is what can be made ours without lying about anything.
    """
    report = {"titles_cleared": 0, "ids": [], "left_in_code": 0}

    def clear_tag(text: str, tag: str) -> str:
        """Empty a title/heading whose text names somebody else's product; leave the tag in place."""
        pattern = re.compile(rf"<{tag}([^>]*)>([\s\S]{{0,200}}?)</{tag}>", re.I)

        def replace(match: re.Match) -> str:
            inner = match.group(2)
            if any(brand in inner.lower() for brand in brands):
                report["titles_cleared"] += 1
                return f"<{tag}{match.group(1)}></{tag}>"
            return match.group(0)

        return pattern.sub(replace, text)

    html = client
    for tag in ("title", "h1", "h2", "h3"):
        html = clear_tag(html, tag)
    for brand in brands:
        for element_id in re.findall(rf'id="([\w-]*{brand}[\w-]*)"', html, re.I):
            html, what = remove_element(html, "#" + element_id)
            report["ids"].append(what)
        for class_name in re.findall(rf'class="([^"]*{brand}[^"]*)"', html, re.I):
            report["ids"].append(f"left the class {class_name!r}: removing one safely needs a parser")
    heads = brand_hits(html, brands)
    report["left_in_code"] = len(heads["scripts"]) + len(heads["other"])
    if report["titles_cleared"]:
        log(f"  branding: cleared {report['titles_cleared']} title/heading(s) that were not ours")
    if heads["other"]:
        log(f"  branding: {len(heads['other'])} name(s) still appear inside compiled code "
            f"({', '.join(heads['other'][:4])}) - credited in CREDITS.md instead of rewritten")
    return html, report


def check(client: str, log=print) -> dict:
    """What this client would need, without changing it."""
    ids = re.findall(r'id="([\w-]+)"', client)
    loading = [f"#{i}" for i in ids
               if any(word in i.lower() for word in
                      ("load", "splash", "boot", "progress", "start", "overlay", "menu"))]
    findings = {
        "size_mb": round(len(client) / 1048576, 1),
        "has_body": bool(re.search(r"<body[^>]*>", client, re.I)),
        "has_script": "<script" in client.lower(),
        "has_canvas": "<canvas" in client.lower(),
        "already_has_boot_screen": MARKER in client,
        "loading_ui_ids": loading,
        "ids": ids[:40],
    }
    log(f"  {findings['size_mb']} MB, body={findings['has_body']}, script={findings['has_script']}, "
        f"canvas={findings['has_canvas']}, boot screen already in it={findings['already_has_boot_screen']}")
    log("  ids that look like a loading UI: " + (", ".join(loading) if loading
                                                 else "none found - pass --remove by hand"))
    if not findings["has_body"]:
        log("  ! no <body>: this packager does not produce a plain HTML page, so the shell has to be "
            "inserted by that engine instead")
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", default=None, help="the compiled client HTML")
    parser.add_argument("--out", default=None)
    parser.add_argument("--style", default="dark", choices=("dark", "dirt"),
                        help="dark is the screen 1.13 and newer draws; dirt is the classic one")
    parser.add_argument("--dirt", default=None, help="a dirt texture to tile (with --style dirt)")
    parser.add_argument("--remove", default="", help="comma-separated #ids to take out of the client")
    parser.add_argument("--no-hide-on-gl", action="store_true",
                        help="do not hand over on the first GL draw; the client must say when")
    parser.add_argument("--shell-template", action="store_true",
                        help="write the shell as a page template for a wasm-JVM kit (@SCRIPT@ intact)")
    parser.add_argument("--check", action="store_true", help="report, change nothing")
    parser.add_argument("--scan-brand", action="store_true",
                        help="report which borrowed names are still on the page")
    parser.add_argument("--strip-brand", action="store_true", default=True,
                        help="remove borrowed branding from the page (default; --no-strip-brand to keep)")
    parser.add_argument("--no-strip-brand", dest="strip_brand", action="store_false")
    parser.add_argument("--brands", default="", help="comma-separated extra names to strip")
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)

    if args.shell_template:
        if not args.out:
            parser.error("--shell-template needs --out")
        text = shell_text()
        if args.dirt:
            text = text.replace("</head>", style_block(pathlib.Path(args.dirt)) + "</head>", 1)
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"wrote {out} ({len(text)} bytes), @SCRIPT@ left for the linker to substitute")
        if "@SCRIPT@" not in text:
            print("  note: this shell has no @SCRIPT@ placeholder - an engine that substitutes one "
                  "will need it added, or will load its script itself")
        return 0

    if not args.client:
        parser.error("--client is required (or --shell-template)")
    client_path = pathlib.Path(args.client)
    if not client_path.is_file():
        print(f"no such client: {client_path}", file=sys.stderr)
        return 2
    client = client_path.read_text(errors="replace")

    print(f"{client_path}")
    brands = tuple(BRANDS) + tuple(b.strip().lower() for b in args.brands.split(",") if b.strip())
    if args.check:
        check(client)
        if args.scan_brand:
            hits = brand_hits(client, brands)
            count = sum(len(value) for value in hits.values())
            print(f"borrowed branding on this page: {count} hit(s)")
            for key, value in hits.items():
                if value:
                    print(f"  {key}: {', '.join(str(v)[:60] for v in value[:6])}")
        return 0

    dirt = pathlib.Path(args.dirt) if args.dirt else None
    if dirt and not dirt.is_file():
        print(f"no such texture: {dirt}", file=sys.stderr)
        return 2
    html, report = build(client, args.style, dirt, [s for s in args.remove.split(",") if s.strip()],
                         not args.no_hide_on_gl)
    if args.strip_brand:
        html, branding = strip_brand(html, brands)
        report["branding"] = branding

    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html)
        print(f"  wrote {out}: {report['bytes_before'] / 1048576:.1f} MB -> "
              f"{report['bytes_after'] / 1048576:.1f} MB")
    elif args.print:
        print(html[:2000])
    else:
        print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
