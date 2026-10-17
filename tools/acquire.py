#!/usr/bin/env python3
"""Fetch what a build needs: the official client jar and its asset index.

    python3 tools/acquire.py --version 26.2 --dry-run          # say what it would fetch
    python3 tools/acquire.py --version 26.2 --dest work/26.2    # do it (on your machine or CI)

Two rules, both deliberate:

  * the game is fetched, never committed. work/ and dist/ are git-ignored, and no jar or
    compiled client is ever part of this repository.
  * --dry-run works with a fixture manifest, so the logic is testable (and CI can show what a
    build would download) without touching Mojang at all.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import versions as version_tools  # noqa: E402

USER_AGENT = "webmc-port/1.0 (+https://github.com)"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_file(url: str, dest: pathlib.Path, sha1: str | None = None) -> pathlib.Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha1()
    with urllib.request.urlopen(request, timeout=3600) as response, open(dest, "wb") as out:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
    if sha1 and digest.hexdigest() != sha1:
        dest.unlink(missing_ok=True)
        raise SystemExit(f"checksum mismatch for {dest.name}: {digest.hexdigest()} != {sha1}")
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a client jar and its assets for a build")
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest", default=version_tools.MANIFEST_URL)
    parser.add_argument("--dest", default=None, help="where to put them (default work/<version>)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--assets", action="store_true", help="also download the asset index")
    args = parser.parse_args()

    manifest = version_tools.load_manifest(args.manifest)
    entry = version_tools.resolve(manifest, args.version)
    document = fetch_json(entry["url"]) if not args.dry_run else {
        "id": args.version,
        "downloads": {"client": {"url": "https://piston-data.mojang.com/…/client.jar"}},
        "assetIndex": {"url": "https://piston-meta.mojang.com/…/assets.json", "id": args.version},
        "javaVersion": {"majorVersion": 25},
        "mainClass": "net.minecraft.client.main.Main",
        "libraries": [{"name": "…"}],
        "type": entry.get("type"),
        "releaseTime": entry.get("releaseTime"),
    }
    targets = version_tools.download_targets(document)
    dest = pathlib.Path(args.dest or (ROOT / "work" / args.version))

    print(f"version:   {targets['id']} ({targets['type']}, {targets['release_time']})")
    print(f"java:      {targets['java_major']}")
    print(f"main:      {targets['main_class']}")
    print(f"libraries: {targets['libraries']}")
    print(f"client:    {targets['client_url']}")
    print(f"assets:    {targets['asset_index_url']}")
    print(f"dest:      {dest}")

    if args.dry_run:
        print("\n--dry-run: nothing downloaded. Remove it on a machine that may talk to Mojang.")
        return 0

    client = fetch_file(targets["client_url"], dest / "client.jar", targets["client_sha1"])
    print(f"client jar: {client} ({client.stat().st_size / 1048576:.1f} MB, checksum ok)")
    if args.assets and targets["asset_index_url"]:
        index = fetch_file(targets["asset_index_url"], dest / "assets-index.json")
        print(f"asset index: {index}")
    print("\nremember: this lives in work/, which is never committed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
