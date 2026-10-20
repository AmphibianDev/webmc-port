#!/usr/bin/env python3
"""Download the browser builds this project catalogs, from GitHub, and check what arrives.

    python3 tools/fetch_builds.py --dry-run          # the whole list, with sizes, nothing fetched
    python3 tools/fetch_builds.py                    # fetch what is missing
    python3 tools/fetch_builds.py --only 26.2        # one version
    python3 tools/fetch_builds.py --verify           # re-check what is already here

Every entry in builds.json is a GitHub address: a release asset or repository files (some modern
builds are several files - an index.html, a classes.js and a wasm payload). Downloads land in
work/builds/<version>/, which is git-ignored: this repository keeps the *list*, never the clients.

What it does per file:

  * fetches it, resuming nothing, into a .part file and renames on success
  * records size and sha256
  * sniffs it - an HTML head, or for a companion file a sane size - and says whether it looks like
    a browser client at all, so a 404 page or an error message cannot pass as build
  * writes work/builds/manifest.json with everything above, which is what the catalog and the
    launcher page can quote

`--verify` re-hashes what is on disk, so a file that changed upstream or was truncated shows up.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DESTINATION = ROOT / "work" / "builds"
USER_AGENT = "webmc-port/1.0 (+https://github.com)"


def load_builds(path: pathlib.Path | None = None) -> dict:
    return json.loads((path or (ROOT / "builds.json")).read_text())


def looks_like_a_client(head: bytes, name: str) -> tuple[bool, str]:
    """A first judgement, from the first bytes and the filename."""
    text = head[:4096].decode("utf-8", "replace").lower()
    if head[:4] == b"\x1f\x8b":
        return True, "gzip payload"
    if head[:4] == b"PK\x03\x04":
        return True, "zip archive"
    if b"\x00asm" in head[:16]:
        return True, "webassembly module"
    if "<!doctype html" in text or "<html" in text:
        for marker, label in (("eaglercraft", "an Eaglercraft page"), ("minecraft", "a Minecraft page"),
                              ("canvas", "a page with a canvas")):
            if marker in text:
                return True, label
        return True, "an HTML page (no Minecraft marker in the head)"
    if name.endswith(".js"):
        return True, "a JavaScript payload"
    if name.endswith(".epk"):
        return True, "an asset pack"
    return False, f"does not look like a client ({head[:8]!r})"


def fetch(url: str, target: pathlib.Path, expected: int | None, dry_run: bool) -> dict:
    """Fetch one file, unless it is already there and the size matches."""
    entry = {"file": target.name, "url": url, "expected_bytes": expected}
    if target.is_file() and expected and target.stat().st_size == expected:
        data = target.read_bytes()
        entry.update({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "state": "already-here"})
        entry["looks_like"], entry["marker"] = looks_like_a_client(data[:4096], target.name)
        return entry
    if dry_run:
        entry.update({"state": "would-fetch", "bytes": expected})
        return entry

    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=600) as response, open(part, "wb") as out:
            head = response.read(4096)
            out.write(head)
            digest.update(head)
            received = len(head)
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                received += len(chunk)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
        part.unlink(missing_ok=True)
        entry.update({"state": "failed", "error": str(error)})
        return entry
    part.replace(target)
    entry.update({"bytes": received, "sha256": digest.hexdigest()})
    entry["looks_like"], entry["marker"] = looks_like_a_client(head, target.name)
    if not entry["looks_like"]:
        # what it is on disk, but not a browser client: a 404 page or an error message. This is
        # the case the check exists for, so it counts against the run.
        entry["state"] = "not-a-client"
    elif expected and received != expected:
        entry["state"] = "fetched-but-different-size"
    else:
        entry["state"] = "fetched"
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch the catalogued browser builds from GitHub")
    parser.add_argument("--builds", default=str(ROOT / "builds.json"))
    parser.add_argument("--dest", default=str(DESTINATION))
    parser.add_argument("--only", default=None, help="one version id (or a comma-separated list)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true", help="re-hash what is already on disk")
    args = parser.parse_args()

    builds = load_builds(pathlib.Path(args.builds))
    destination = pathlib.Path(args.dest)
    wanted = {v.strip() for v in args.only.split(",")} if args.only else None

    manifest: dict = {"note": builds.get("note", ""), "versions": {}}
    total_expected = 0
    fetched = already = failed = suspect = 0

    for group in builds["versions"]:
        version = group["version"]
        if wanted and version not in wanted:
            continue
        entries = []
        for item in group["files"]:
            target = destination / group["folder"] / item["name"]
            expected = item.get("bytes")
            if expected:
                total_expected += expected
            entry = fetch(item["url"], target, expected, args.dry_run)
            entry["role"] = item.get("role", "client")
            entry["source_repo"] = item.get("source_repo") or group.get("source_repo")
            entries.append(entry)
            state = entry["state"]
            fetched += state.startswith("fetched")
            already += state == "already-here"
            failed += state == "failed"
            suspect += state == "not-a-client"
            if args.dry_run:
                size = f"{(expected or 0) / 1048576:.1f} MB" if expected else "size unknown"
                print(f"  {version:<12} {item['name']:<48} {size:<12} {item['url'][:70]}")
            else:
                mark = {"fetched": "✓", "already-here": "=", "failed": "✗",
                        "not-a-client": "?"}.get(state, "?")
                print(f"  {mark} {version:<12} {item['name']:<48} {entry.get('bytes', 0) / 1048576:>6.1f} MB "
                      f"{entry.get('marker', entry.get('error', ''))}")
        manifest["versions"][version] = {
            "label": group.get("label", version),
            "era": group.get("era"),
            "notes": group.get("notes"),
            "files": entries,
        }

    manifest["summary"] = {"fetched": fetched, "already_here": already, "failed": failed,
                           "not_a_client": suspect,
                           "total_bytes": sum(e.get("bytes") or 0
                                              for v in manifest["versions"].values() for e in v["files"])}
    if args.dry_run:
        print(f"\n{len(manifest['versions'])} version(s), about {total_expected / 1048576:.0f} MB to fetch")
        print("--dry-run: nothing was downloaded")
        return 0

    manifest_path = destination / "manifest.json"
    if args.verify:
        for version in manifest["versions"].values():
            for entry in version["files"]:
                target = destination / entry.get("folder", "") / entry["file"]
                if target.is_file():
                    data = target.read_bytes()
                    entry["bytes"] = len(data)
                    entry["sha256"] = hashlib.sha256(data).hexdigest()
                    entry["state"] = "verified"
        print(f"\nverified {sum(len(v['files']) for v in manifest['versions'].values())} file(s)")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nfetched {fetched}, already here {already}, failed {failed}, not a client {suspect}")
    print(f"manifest: {manifest_path}")
    return 1 if (failed or suspect) else 0


if __name__ == "__main__":
    sys.exit(main())
