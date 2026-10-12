#!/usr/bin/env python3
"""Are the links the launcher offers actually playable?

    python3 tools/check_links.py                    # report on builds.json and the catalog
    python3 tools/check_links.py --fix-builds       # prefer a live Pages deployment over a raw file

Two failures this exists for, both of which reached a person before they reached CI:

  * a repository that hosted ports was deleted, so two "play" links were 404 and the versions looked
    broken rather than unlinked;
  * a client served from raw.githubusercontent.com arrives as text/plain, so clicking play shows
    seventy-five megabytes of HTML source instead of a game.

A raw file is therefore not a play link: it is a download. A GitHub Pages deployment
(<owner>.github.io/<repo>/) serves text/html, so this prefers one when it exists - that is the same
bytes, served as a page - and records what each link actually returns.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

# Whatever a link answers, it is recorded; only these decide "playable".
PLAYABLE_TYPES = ("text/html", "application/xhtml+xml")


def probe(url: str, timeout: int = 25, method: str = "GET") -> dict:
    """What a link answers: status, content type, and how big it claims to be."""
    request = urllib.request.Request(url, method=method, headers={"User-Agent": "webmc-port/1.0 (+linkcheck)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2048) if method == "GET" else b""
            return {
                "status": response.status,
                "content_type": (response.headers.get("content-type") or "").split(";")[0].strip(),
                "bytes": int(response.headers.get("content-length") or len(body) or 0),
                "ok": True,
            }
    except urllib.error.HTTPError as error:
        return {"status": error.code, "content_type": "", "bytes": 0, "ok": False, "error": str(error)}
    except Exception as error:  # noqa: BLE001 - a dead host must not stop the sweep
        return {"status": 0, "content_type": "", "bytes": 0, "ok": False, "error": str(error)}


def is_playable(report: dict) -> bool:
    return bool(report.get("ok")) and report.get("content_type", "").startswith(PLAYABLE_TYPES)


def pages_url(raw_url: str) -> str | None:
    """The Pages deployment for a github raw/blob link, if the shape allows guessing one."""
    for marker in ("raw.githubusercontent.com/", "github.com/"):
        if marker not in raw_url:
            continue
        tail = raw_url.split(marker, 1)[1]
        parts = [p for p in tail.split("/") if p]
        if len(parts) < 2:
            return None
        owner, repo = parts[0], parts[1].replace(".git", "")
        return f"https://{owner.lower()}.github.io/{repo}/"
    return None


def refresh(urls: list[str], probe_fn=probe) -> dict:
    report = {}
    for url in urls:
        if not url:
            continue
        report[url] = {**probe_fn(url), "checked": datetime.datetime.now(datetime.timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ")}
    return report


def fix_builds(path: pathlib.Path, probe_fn=probe, log=print) -> dict:
    """Prefer a live Pages page over a raw download for every build in builds.json."""
    document = json.loads(path.read_text())
    changed = 0
    touched = 0
    for build in document.get("versions", []):
        for file in build.get("files") or []:
            raw = file.get("url") or ""
            pages = pages_url(raw)
            if not pages:
                continue
            report = probe_fn(pages)
            if is_playable(report):
                if file.get("url") != pages:
                    file["fallback_url"] = raw
                    file["url"] = pages
                    file["served_as"] = "html"
                    changed += 1
                    log(f"  {build['version']}: pages deployment is live, linking it instead of the raw file")
            else:
                # raw.githubusercontent serves text/plain: a download, not a game page
                if file.get("served_as") != "download":
                    touched += 1
                file["served_as"] = "download"
                log(f"  {build['version']}: no pages deployment; the raw file downloads rather than plays")
    if changed or touched:
        path.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")
    return {"changed": changed, "marked": touched, "versions": len(document.get("versions", []))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--builds", default=str(ROOT / "builds.json"))
    parser.add_argument("--fix-builds", action="store_true", help="link live Pages deployments in builds.json")
    parser.add_argument("--state", default=str(ROOT / "state.json"),
                        help="where the catalog keeps what it learned about links")
    parser.add_argument("--write", action="store_true", help="store the report in the state file")
    parser.add_argument("--limit", type=int, default=80)
    args = parser.parse_args(argv)

    if args.fix_builds:
        result = fix_builds(pathlib.Path(args.builds))
        print(f"builds.json: {result['versions']} versions, {result['changed']} link(s) upgraded to a Pages page")
        return 0

    state_path = pathlib.Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    catalogue = ROOT / "site" / "manifest.json"
    urls: list[str] = []
    if catalogue.is_file():
        for entry in json.loads(catalogue.read_text()).get("versions", []):
            urls.append(entry.get("play_url"))
    builds = json.loads(pathlib.Path(args.builds).read_text()) if pathlib.Path(args.builds).is_file() else {}
    for build in builds.get("versions", []):
        for file in build.get("files") or []:
            urls.append(file.get("url"))
    urls = [u for u in dict.fromkeys(urls) if u][: args.limit]

    fresh = refresh(urls)
    links = {**(state.get("links") or {}), **fresh}
    playable = sum(1 for url in fresh if is_playable(fresh[url]))
    dead = [url for url in fresh if not fresh[url].get("ok")]
    print(f"checked {len(fresh)} link(s): {playable} playable, {len(dead)} dead")
    for url in dead:
        print(f"  DEAD: {url}")
    if args.write:
        state["links"] = links
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        print(f"wrote {state_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
