#!/usr/bin/env python3
"""Take the engine's own chrome off a client page, and say exactly what came off.

    python3 tools/debrand.py --client site/clients/1.21.11/data/client.0000 --check
    python3 tools/debrand.py --client dist/26.2.html --out dist/26.2-clean.html
    python3 tools/debrand.py --client x.html --json

Why this is one tool and not four regexes in four places: a compiled client is somebody else's page with
our game inside it, and the engine that made it puts its own header, footer, boot overlay, profile screens,
status console and performance readout on top. Every one of those has to go - a player should see Minecraft,
not the pipeline that produced it - and the way they have to go differs from case to case.

Two rules, and the difference between them is the whole tool:

  * something that is only branding is **removed** - a header, a footer, a logo block, a title whose text
    names somebody else's product;
  * something the client's own script looks up by id is **hidden** instead (`display: none !important`,
    under one stylesheet this tool owns), because a script that finds no element stops on a null and the
    player gets a black canvas. That failure mode is real and has been seen: the engine's boot overlay
    and its runtime-built shell are looked up while it boots.

"Looks it up" is not a guess: an element is treated as wired when its id (or the marker attribute the
engine sets on what it builds later) appears **inside a <script> body** of the same page. That is a fact
about the file, so the decision is reproducible and can be re-checked from the outside.

What this deliberately does not do: rewrite the compiled JavaScript, the WebAssembly, or the embedded
resource pack. A brand token in somebody else's compiled code is a class name, an id in a data table or
part of a URL, and blind replacement breaks the client - those occurrences stay, and CREDITS.md is what
attributes them. This tool only ever edits the *page*: elements, their text, and one stylesheet.

Idempotent: running it twice leaves the same bytes as running it once. The stylesheet it adds is found by
its own id and replaced, never stacked, and an element it has already hidden counts as handled - so
`--check` on a published client answers "is this one still clean?" rather than re-reporting the same work.

A page that carries none of this chrome - a client from another lineage, or a client this pass has
already cleaned - is returned byte for byte as it arrived.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

# Names that belong to the engines we borrow rather than to this project. Credited in CREDITS.md, and kept
# out of the page a player reads (not out of the compiled code - see above and in strip_brand's account).
BRANDS = ("eaglercraft", "eagler", "lax1dude", "gaius", "typetheory", "browsercraft", "eaglerforge",
          "resent", "koneclient")

# Ids that contain a brand word because they are the client's own wiring, not chrome. The distinction
# matters: chrome is branding a player reads and can go, wiring is what the client's bootstrap looks up -
# removing one of these is a client that does not start, which is worse than a word in a page.
WIRED_IDS = ("eagler26-offline-meta", "eagler26-build", "eagler26-offline-cache",
             "eagler26-offline-storage", "eagler-config")

# The engine's chrome, by the ids it actually uses. `remove` is the intent; whether it is removed or hidden
# is decided per page by looking at the scripts (see `is_wired`). `why` is what the report prints, because
# "which of these is the big logo" is exactly the question this list exists to answer.
CHROME: tuple[dict, ...] = (
    {"selector": "#boot-brand", "remove": True, "why": "the engine's brand block over its boot screen"},
    {"selector": "#boot-screen", "remove": False, "why": "the engine's boot overlay: ours is the loading screen"},
    {"selector": "#boot-bar", "remove": False, "why": "the engine's boot progress bar"},
    {"selector": "#boot-card", "remove": False, "why": "the engine's boot card"},
    {"selector": "#boot-bar-fill", "remove": False, "why": "the engine's boot bar fill"},
    {"selector": "#gaius-shell-header", "remove": True, "why": "the engine's launcher header"},
    {"selector": "#gaius-shell-footer", "remove": True, "why": "the engine's launcher footer"},
    {"selector": "#gaius-error-details", "remove": False, "why": "the engine's error panel"},
    {"selector": "#profile-kicker", "remove": True, "why": "the engine's profile screen kicker"},
    {"selector": "#profile-subtitle", "remove": True, "why": "the engine's profile screen subtitle"},
    {"selector": "#profile-legal", "remove": True, "why": "the engine's profile screen legal line"},
    {"selector": "#status", "remove": False, "why": "the engine's status console, across the top of the game"},
    {"selector": "#perf-hud", "remove": False, "why": "the engine's performance readout in the corner"},
    {"selector": "#log", "remove": False, "why": "the engine's log panel"},
)

# What the engine marks on anything it builds after load. Not an element this tool can find in the file, so
# it is hidden by attribute and left alone otherwise - which is also what makes it safe.
RUNTIME_MARKER = "data-gaius-shell"

STYLE_ID = "webmc-debrand"
STYLE_MARK = f'<style id="{STYLE_ID}">'
BRANDS_RE = re.compile("|".join(re.escape(brand) for brand in BRANDS), re.I)
SCRIPT_BODY_RE = re.compile(r"<script\b[^>]*>([\s\S]*?)</script>", re.I)


def script_bodies(html: str) -> list[str]:
    """Every inline script body: the only place that can look an element up."""
    return SCRIPT_BODY_RE.findall(html)


def is_wired(html: str, selector: str) -> bool:
    """Does the page's own script refer to this element?

    The test is the id (or the marker attribute) appearing inside a script body. It is deliberately crude -
    a string in a script is not proof that the script *needs* the element - but it errs in the safe
    direction: a false "wired" hides something that could have been removed, and a false "not wired" removes
    something a script needs, which is a client that stops.
    """
    identifier = selector.lstrip("#")
    if identifier == RUNTIME_MARKER:
        needle = RUNTIME_MARKER
    else:
        needle = f'"{identifier}"'
    plain = identifier
    for body in script_bodies(html):
        if needle in body or plain in body:
            return True
    return False


def present(html: str, selector: str) -> bool:
    identifier = selector.lstrip("#")
    if identifier == RUNTIME_MARKER:
        return RUNTIME_MARKER in html
    return re.search(r'\bid\s*=\s*["\']%s["\']' % re.escape(identifier), html, re.I) is not None


def remove_element(html: str, selector: str) -> tuple[str, str]:
    """Remove one element by #id. Returns (html, what happened)."""
    wanted = selector.strip()
    if not wanted.startswith("#"):
        return html, f"skipped {wanted!r}: only #id selectors are supported"
    element_id = wanted[1:]
    paired = re.compile(r"<(\w+)([^>]*\bid\s*=\s*[\"']%s[\"'][^>]*)>[\s\S]*?</\1>\s*" % re.escape(element_id), re.I)
    if paired.search(html):
        return paired.sub("", html, count=1), f"removed {wanted}"
    void = re.compile(r"<\w+[^>]*\bid\s*=\s*[\"']%s[\"'][^>]*/?>\s*" % re.escape(element_id), re.I)
    if void.search(html):
        return void.sub("", html, count=1), f"removed the empty element {wanted}"
    # an element the engine only writes at run time: nothing to remove here, the stylesheet handles it
    return html, f"not in this page: {wanted} (hid it anyway, in case the engine builds it later)"


def clear_brand_text(html: str, report: dict) -> str:
    """Empty a title or heading whose text names somebody else's product; leave the tag in place."""
    def clear_tag(text: str, tag: str) -> str:
        pattern = re.compile(rf"<{tag}([^>]*)>([\s\S]{{0,200}}?)</{tag}>", re.I)

        def replace(match: re.Match) -> str:
            inner = match.group(2)
            if BRANDS_RE.search(inner):
                report["titles_cleared"].append(f"{tag}: {inner.strip()[:60]}")
                return f"<{tag}{match.group(1)}></{tag}>"
            return match.group(0)

        return pattern.sub(replace, text)

    for tag in ("title", "h1", "h2", "h3"):
        html = clear_tag(html, tag)
    return html


def style_block(selectors: list[str]) -> str:
    """One stylesheet, ours, that hides what must stay in the document.

    Not `visibility: hidden` and not `opacity: 0`: a player must not be able to tab into it, and a screen
    reader must not read somebody's brand to them. `display: none !important` is the only one of the three
    that does both.

    One rule per selector, on its own line, for a reason that is not cosmetic: a checker reading a
    published client can look for the exact rule it expects, instead of parsing a selector list.
    """
    if not selectors:
        return ""
    rules = "".join(f"{selector} {{ display: none !important; }}\n" for selector in selectors)
    # Hiding the engine's status console took away the only place a player can read the client's own log -
    # the Java stack trace, the worldgen messages, the reason a world did not finish loading. So the hiding
    # is reversible: `?log=1` on the page brings it back, and the launcher offers that as a link. A console
    # nobody can open is how a bug becomes a guess.
    if selectors:
        shown = ", ".join(f"html.webmc-log {selector}" for selector in selectors)
        rules += (f"{shown} {{ display: revert !important; }}\n"
                  "html.webmc-log #status { position: fixed; z-index: 2147483647; max-height: 45vh; "
                  "overflow: auto; background: #000c; color: #dfe; font: 12px/1.4 ui-monospace, monospace; "
                  "padding: 6px 8px; width: 100%; }\n")
    return (f'{STYLE_MARK}\n'
            f"/* WebMC: the engine's own chrome, hidden rather than removed where its script looks it up. */\n"
            + rules + "</style>\n")


def strip_previous(html: str) -> str:
    """Drop the stylesheet a previous run added, so this tool is idempotent."""
    return re.sub(re.escape(STYLE_MARK) + r"[\s\S]*?</style>\s*", "", html, count=1)


def hidden_by_us(html: str, selector: str) -> bool:
    """Does our own stylesheet already hide this element? The other half of idempotence."""
    return re.search(re.escape(selector) + r"\s*\{[^}]*display:\s*none", html) is not None


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


def brand_pass(html: str, brands: tuple = BRANDS, log=print) -> tuple[str, dict]:
    """Take the borrowed names off the *page*: titles, headings, and elements named after them.

    What this deliberately does not do: rewrite the compiled JavaScript or wasm. A brand token inside
    somebody else's code is often an identifier, a field name or part of a URL, and blind replacement breaks
    the client - so those occurrences stay, and CREDITS.md is what attributes them. The page is what a player
    reads, and that is what can be made ours without lying about anything.
    """
    report = {"titles_cleared": [], "ids": [], "ids_cleared": [], "wired": [], "left_in_code": 0}
    html = clear_brand_text(html, report)

    for name in sorted({name for name in re.findall(r'\bid\s*=\s*["\']([\w.-]+)["\']', html)
                        if any(word in name.lower() for word in brands)}):
        if name in WIRED_IDS:
            continue
        if is_wired(html, "#" + name):
            report["wired"].append(name)
            continue
        html, what = remove_element(html, "#" + name)
        report["ids"].append(name)
        report["ids_cleared"].append({"id": name, "result": what})

    heads = brand_hits(html, brands)
    report["left_in_code"] = len(heads["scripts"]) + len(heads["other"])
    if report["titles_cleared"]:
        log(f"  branding: cleared {len(report['titles_cleared'])} title/heading(s) that were not ours")
    if report["ids"]:
        log(f"  branding: removed {len(report['ids'])} element(s) named after another project: "
            + ", ".join(report["ids"][:4]))
    if heads["other"]:
        log(f"  branding: {len(heads['other'])} name(s) still appear inside compiled code "
            f"({', '.join(heads['other'][:4])}) - credited in CREDITS.md instead of rewritten")
    return html, report


def analyse(html: str) -> dict:
    """What this page carries, without changing it. The shape --check prints."""
    found = []
    for marker in CHROME:
        selector = marker["selector"]
        if not present(html, selector):
            # an element the stylesheet hides but the page never had: the engine builds it while it boots
            if hidden_by_us(html, selector):
                found.append({**marker, "wired": False, "action": "hidden by our stylesheet"})
            continue
        wired = is_wired(html, selector)
        if hidden_by_us(html, selector):
            action = "hidden by our stylesheet"
        elif marker["remove"] and not wired:
            action = "remove"
        else:
            action = "hide"
        found.append({**marker, "wired": wired, "action": action})
    branded_ids = sorted({name for name in re.findall(r'\bid\s*=\s*["\']([\w.-]+)["\']', html)
                          if BRANDS_RE.search(name) and name not in WIRED_IDS})
    classes = sorted({name for chunk in re.findall(r'\bclass\s*=\s*["\']([^"\']*)["\']', html)
                      for name in chunk.split() if BRANDS_RE.search(name)})
    headings = []
    for tag in ("title", "h1", "h2", "h3"):
        for inner in re.findall(rf"<{tag}[^>]*>([\s\S]{{0,200}}?)</{tag}>", html, re.I):
            if BRANDS_RE.search(inner):
                headings.append(f"{tag}: {inner.strip()[:60]}")
    todo = [marker for marker in found if marker["action"] in ("remove", "hide")]
    return {
        "chrome": found,
        "branded_ids": branded_ids,
        "branded_classes": classes,
        "branded_headings": headings,
        "runtime_marker": RUNTIME_MARKER in html,
        "already_clean": not todo and not branded_ids and not headings,
        "bytes": len(html),
    }


def debrand(html: str, log=print, *, assume_engine: bool = False, clear_brands: bool = True,
            brands: tuple = BRANDS) -> tuple[str, dict]:
    """The whole pass: remove the pure chrome, hide the wired chrome, clear brand text.

    `assume_engine` is for a caller that already knows the page came from that engine - the compiler's own
    shell tool does - so the panels the engine builds *after* load are hidden as well, even though nothing in
    the file mentions them yet. Without it, only what the page actually carries is touched.

    Returns (html, report). The report is the point as much as the edit is: "the strip ran" is not a fact
    anybody can check, while "3 elements removed, 6 hidden, 2 headings cleared, 0 branded ids left" is.
    """
    html = strip_previous(html)
    report = {"removed": [], "hidden": [], "titles_cleared": [], "wired": [], "ids_cleared": [],
              "not_in_this_page": []}

    hide: list[str] = []
    for marker in CHROME:
        selector = marker["selector"]
        if not present(html, selector):
            # Not in this page. Hidden anyway only for a page the caller knows is this engine's: it builds
            # several of these panels after load, so an absent element is not evidence it will stay absent.
            if assume_engine:
                hide.append(selector)
                report["hidden"].append({"selector": selector, "why": marker["why"],
                                         "because": "the engine builds it while it boots"})
            else:
                report["not_in_this_page"].append(selector)
            continue
        wired = is_wired(html, selector)
        if marker["remove"] and not wired:
            html, what = remove_element(html, selector)
            report["removed"].append({"selector": selector, "why": marker["why"], "result": what})
            log(f"  removed {selector}: {marker['why']}")
        else:
            hide.append(selector)
            report["hidden"].append({"selector": selector, "why": marker["why"],
                                     "because": "the page's own script refers to it" if wired
                                                else "it is a panel a player reads, not branding"})
            log(f"  hid {selector}: {marker['why']} "
                f"({'its script looks it up' if wired else 'a panel, not branding'})")

    # ids named after a brand: never touched when they are wiring, otherwise removed like any other chrome
    if clear_brands:
        html, brand_report = brand_pass(html, brands, log=log)
        report["ids_cleared"].extend(brand_report["ids_cleared"])
        report["wired"].extend(brand_report["wired"])
        hide.extend("#" + name for name in brand_report["wired"])

    # What the engine builds after load carries its marker attribute, so one selector covers everything it
    # has not drawn yet - but only for a page that is that engine's to begin with. A client from another
    # lineage carries none of this chrome and gets none of this stylesheet.
    engine_page = assume_engine or bool(hide) or bool(report["removed"]) or bool(report["ids_cleared"]) \
        or RUNTIME_MARKER in html
    if engine_page:
        hide.append(f"[{RUNTIME_MARKER}]")
    css = style_block(sorted(set(hide)))
    if css:
        html = html.replace("</head>", css + "</head>", 1) if "</head>" in html else css + html

    report["css_hidden"] = sorted(set(hide))
    report["bytes_after"] = len(html)
    after = analyse(html)
    report["clean"] = after["already_clean"]
    return html, report


def debrand_bytes(data: bytes, log=print, **options) -> tuple[bytes, dict]:
    """The same pass over a client's bytes. A client is one page and can be 100 MB of it, so the text is
    handled with the surrogateescape convention the packer uses: a byte that is not valid UTF-8 round-trips
    exactly, which matters because compressed payloads live inside these pages."""
    text = data.decode("utf-8", errors="surrogateescape")
    cleaned, report = debrand(text, log=log, **options)
    report["bytes_before"] = len(data)
    report["bytes_after"] = len(cleaned.encode("utf-8", errors="surrogateescape"))
    return cleaned.encode("utf-8", errors="surrogateescape"), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Take the engine's chrome off a compiled client page")
    parser.add_argument("--client", required=True, help="the client HTML to read")
    parser.add_argument("--out", default=None, help="where to write the cleaned page (default: print only)")
    parser.add_argument("--in-place", action="store_true", help="write back over --client")
    parser.add_argument("--check", action="store_true", help="report only; change nothing")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    path = pathlib.Path(args.client)
    if not path.is_file():
        print(f"no such client: {path}", file=sys.stderr)
        return 2
    data = path.read_bytes()

    if args.check:
        findings = analyse(data.decode("utf-8", errors="surrogateescape"))
        if args.json:
            print(json.dumps(findings, indent=1))
        else:
            print(f"{path} - {findings['bytes'] / 1048576:.1f} MB")
            for marker in findings["chrome"]:
                print(f"  {marker['action']:<6} {marker['selector']:<24} {marker['why']}"
                      + ("  (its script looks it up)" if marker["wired"] else ""))
            if findings["branded_ids"]:
                print("  branded ids: " + ", ".join(findings["branded_ids"]))
            if findings["branded_classes"]:
                print("  branded classes: " + ", ".join(findings["branded_classes"]))
            if findings["branded_headings"]:
                print("  headings that are not ours: " + "; ".join(findings["branded_headings"]))
            print("  clean already" if findings["already_clean"] else "  needs the pass")
        return 0

    cleaned, report = debrand_bytes(data, log=(lambda *_: None) if args.json else print)
    if args.json:
        print(json.dumps(report, indent=1))
    if args.out or args.in_place:
        target = path if args.in_place else pathlib.Path(args.out)
        target.write_bytes(cleaned)
        if not args.json:
            print(f"  wrote {target} ({len(cleaned) / 1048576:.1f} MB, "
                  f"{len(report['removed'])} removed, {len(report['hidden'])} hidden, "
                  f"{len(report['titles_cleared'])} heading(s) cleared)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
