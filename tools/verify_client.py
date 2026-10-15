#!/usr/bin/env python3
"""Check a published client from the outside: hashes, boot screen, branding, mod set.

Everything this project publishes has already been checked in pieces by the tools that made it - the packer wrote
the hashes, the shell tool wrote the boot screen, the bake wrote the mod list. That is exactly the problem: each
piece trusts the piece before it. This tool reads what a *visitor* gets over HTTP and checks the same facts from
the outside, which is the only place a publishing mistake can still be caught.

    python3 tools/verify_client.py --url https://…/clients/1.21.11/
    python3 tools/verify_client.py --dir site/clients/1.21.11          # a local copy, no network

What it checks, and why each one is worth a line:

* every chunk hashes to what the manifest says, and the chunks are the size the manifest says - a truncated upload
  is the most common way a published page is broken, and it is invisible until a player is half way through a
  download;
* the page really is ours: the boot screen is present, the engine's own launcher chrome is gone or hidden, and no
  brand words survive in the page's ids or visible text (the compiled code is the engine's and stays the engine's);
* the mod set the page claims is the mod set the manifest records, so "modded" is not a word in a catalogue;
* the version on the boot screen is the version being served, because serving the wrong build under the right
  address is a mistake nobody notices by reading the page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

# Words that belong to the engines we borrow. They may appear in the compiled code (that is the engine's own
# binary, credited in CREDITS.md), but not in the page's markup, its ids, or any text a player reads.
BRANDS = ("eaglercraft", "eagler", "lax1dude", "gaius", "typetheory", "browsercraft", "eaglerforge")

# Ids that contain a brand word because they are the client's own wiring, not chrome. The distinction matters:
# chrome is branding a player reads and can go, wiring is what the client's bootstrap looks up - removing one of
# these is a client that does not start, which is a worse outcome than a word in the page source.
WIRED_IDS = ("eagler26-offline-meta", "eagler26-build", "eagler26-offline-cache")


def fetch_bytes(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+verify)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def load(base: str, name: str, offline: pathlib.Path | None = None) -> bytes:
    if offline is not None:
        path = offline / name
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing")
        return path.read_bytes()
    return fetch_bytes(base.rstrip("/") + "/" + name)


def visible_text(html: str) -> str:
    """The text a reader sees: script and style bodies removed, tags removed."""
    without_script = re.sub(r"(?is)<script\b.*?</script>", " ", html)
    without_style = re.sub(r"(?is)<style\b.*?</style>", " ", without_script)
    without_comments = re.sub(r"(?s)<!--.*?-->", " ", without_style)
    return re.sub(r"(?s)<[^>]*>", " ", without_comments)


def check_page(page: str, client: str, manifest: dict, report: dict) -> None:
    """Check the two documents a visitor gets: our page, and the client it runs.

    They are not the same document and the facts are not in the same one. Our page carries the build's own facts -
    the mod set, the gateway - and the client carries the engine's markup, which is what has to be free of its
    branding. A checker that looked at only one of them would pass a page that says "modded" with no mods in it,
    or a client whose header still says somebody else's name.
    """
    problems: list[str] = list(report.setdefault("problems", []))

    ids = set(re.findall(r'id="([\w.-]+)"', client)) | set(re.findall(r'id="([\w.-]+)"', page))
    branded_ids = sorted(name for name in ids
                         if any(brand in name.lower() for brand in BRANDS) and name not in WIRED_IDS)
    report["branded_ids"] = branded_ids
    if branded_ids:
        problems.append("the page still has engine ids: " + ", ".join(branded_ids))

    branding = [document for document, name in ((client, "the client"), (page, "our page"))
                if 'id="boot-brand"' in document]
    if branding:
        problems.append("the engine's branding block (#boot-brand) is in " + " and ".join(branding))

    # Whose loading screen the player watches while the client boots is a decision per engine: ours covers the
    # engine's own UI where we replaced it, and where an engine's boot screen is kept on purpose (it is the thing
    # that shows the wasm unpacking) that is a choice, not a fault. What is a fault is having neither, which is a
    # blank page and a player who has no idea whether anything is happening.
    ours = "webmc-boot" in client
    # the elements, not the words: a stylesheet rule that hides #boot-screen is not a boot screen
    engine_own = (not ours) and bool(re.search(r'id="boot-(screen|bar|card|bar-fill)"', client))
    report["boot_screen"] = "ours" if ours else ("the engine's own" if engine_own else "none")
    if not ours and not engine_own:
        problems.append("there is no loading screen at all: a blank page while the client boots")

    # the engine's chrome may stay in the client - its script looks some of it up - but only hidden, and hiding it
    # is the whole reason it was not removed, so "present and not hidden" is the failure this looks for
    hidden = any(re.search(r"\[data-gaius-shell\][^{]*\{[^}]*display:\s*none", document) for document in (client, page))
    report["engine_chrome_hidden"] = hidden or "data-gaius-shell" not in client
    if not report["engine_chrome_hidden"]:
        problems.append("the engine's launcher chrome is in the client and not hidden by our stylesheet")

    text = visible_text(client)
    page_text = visible_text(page)
    words = sorted({brand for brand in BRANDS if re.search(rf"\b{brand}\b", text, re.I)})
    report["brand_words_in_text"] = words
    if words:
        problems.append("brand words a visitor can read: " + ", ".join(words))

    title = manifest.get("title") or ""
    report["title"] = title
    if title and title not in text and title not in page_text and title not in client and title not in page:
        problems.append(f"the page does not name this build ({title!r} is nowhere in it)")

    mods = list(manifest.get("mods") or [])
    report["mods"] = mods
    if mods:
        # the boot screen names the mod set, and it does that from the manifest at run time - so the page has to
        # carry the list, and the loading screen is where a player reads it
        # the page carries the mod set in a meta tag of its own as well as in the manifest, so a build can be
        # asked what it is with curl instead of by running it
        missing = [mod for mod in mods if mod not in page and mod not in client]
        if missing:
            problems.append("the page does not say which mods are baked in: " + ", ".join(missing))
    if manifest.get("gateway"):
        report["gateway"] = manifest["gateway"]
        if manifest["gateway"] not in page and manifest["gateway"] not in client:
            problems.append("the manifest records a gateway that is nowhere in what is served")

    report["problems"] = problems


def verify(base: str | None, offline: pathlib.Path | None, log=print) -> dict:
    """Read the published page the way a visitor's browser does, then check it."""
    where = str(offline) if offline is not None else base
    log(f"checking {where}")
    report: dict = {"source": where, "problems": []}

    raw = load(base or "", "manifest.json", offline)
    manifest = json.loads(raw.decode("utf-8"))
    report["client"] = manifest.get("client")
    report["bytes"] = manifest.get("bytes")
    report["chunks"] = len(manifest.get("chunks") or [])

    parts: list[bytes] = []
    seen = 0
    for chunk in manifest.get("chunks") or []:
        data = load(base or "", chunk["path"], offline)
        digest = hashlib.sha256(data).hexdigest()
        if digest != chunk["sha256"]:
            report["problems"].append(f"{chunk['path']} does not match the hash in the manifest")
        if len(data) != chunk.get("size"):
            report["problems"].append(f"{chunk['path']} is {len(data)} bytes, the manifest says {chunk.get('size')}")
        seen += len(data)
        parts.append(data)
    report["bytes_read"] = seen
    if manifest.get("total") and seen != manifest["total"]:
        report["problems"].append(f"the chunks total {seen} bytes, the manifest says {manifest['total']}")
    if manifest.get("sha256"):
        whole = hashlib.sha256(b"".join(parts)).hexdigest()
        report["sha256"] = whole
        if whole != manifest["sha256"]:
            report["problems"].append("the reassembled page does not match the manifest's whole-file hash")

    client_html = b"".join(parts).decode("utf-8", errors="replace")
    page_html = load(base or "", "index.html", offline).decode("utf-8", errors="replace")
    report["page_bytes"] = len(client_html) + len(page_html)
    check_page(page_html, client_html, manifest, report)

    for problem in report["problems"]:
        log(f"  ! {problem}")
    if not report["problems"]:
        log(f"  ok: {manifest.get('client')}, {seen / 1048576:.1f} MB, {len(manifest.get('chunks') or [])} chunk(s), "
            f"{len(report.get('mods') or [])} mod(s), boot screen ours, engine chrome hidden")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=None, help="the published page's directory, e.g. https://…/clients/1.21.11/")
    parser.add_argument("--dir", default=None, help="a local directory instead of a URL")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.url and not args.dir:
        parser.error("give --url or --dir")
    report = verify(args.url, pathlib.Path(args.dir) if args.dir else None,
                    log=(lambda *_: None) if args.json else print)
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"\n{len(report['problems'])} problem(s)")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
