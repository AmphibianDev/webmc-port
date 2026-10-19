#!/usr/bin/env python3
"""Minecraft version discovery: what exists, what kind it is, and what a browser port needs.

Everything here works on a *manifest document* (Mojang's own version list, or a fixture), so
the logic is testable without touching the network. The network call lives in one function at
the bottom, and the updater is the only caller of it.

The distinction that matters for this project:

  release / snapshot   ordinary versions, one client jar each
  old_beta / old_alpha versions that predate the launcher's asset system; their ports are
                       community work (Eaglercraft-style) rather than a straight compile

and for every version we can say which *engine* (a port project) can compile it, from
ports.json - or that it needs a port, which is the honest answer for a version nobody has
written the browser platform layer for yet.
"""

from __future__ import annotations

import json
import pathlib
import re
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
USER_AGENT = "webmc-port/1.0 (+https://github.com)"


def load_manifest(source: str | pathlib.Path | dict) -> dict:
    """A manifest from a dict, a local file, or the network (the only network call here)."""
    if isinstance(source, dict):
        return source
    text = None
    path = pathlib.Path(str(source))
    if isinstance(source, pathlib.Path) or (isinstance(source, str) and path.is_file()):
        text = path.read_text()
    else:
        request = urllib.request.Request(str(source), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read().decode("utf-8")
    return json.loads(text)


def versions(manifest: dict) -> list[dict]:
    return list(manifest.get("versions") or [])


def kind_of(version: dict) -> str:
    return str(version.get("type") or "unknown")


def is_full_client(version: dict) -> bool:
    """Releases and snapshots ship a client jar the launcher downloads; old ones do too, but
    their asset layout and Java level are different enough to be their own problem."""
    return kind_of(version) in ("release", "snapshot")


def version_key(version: str) -> list:
    """Sortable form of a version id: 26.2 > 1.21.11 > 1.20.4 > 1.8.9, snapshots first.

    Snapshots ('24w45a', '1.21.5-pre1') keep their position by release date when sorting a
    manifest, so this is only a tie breaker for ids of the same age.
    """
    parts = re.findall(r"\d+", version)
    numbers = [int(part) for part in parts[:4]] if parts else [0]
    while len(numbers) < 4:
        numbers.append(0)
    suffix = 0
    match = re.search(r"(pre|rc)(\d+)$", version)
    if match:
        # pre/rc sit just under the release
        suffix = -int(match.group(2))
    elif re.search(r"\d+w\d+[a-z]?$", version):
        suffix = -1000
    return numbers + [suffix]


def newest(manifest: dict, kinds=("release",), limit: int | None = None) -> list[dict]:
    """The newest versions of the given kinds, newest first.

    Mojang's own list is already newest first, but a fixture, a mirror or a hand-built list
    need not be - and 'newest first' is what every caller here means, so sort by the release
    time and fall back to the document order only when the times are missing.
    """
    found = [v for v in versions(manifest) if kind_of(v) in kinds]
    if any(v.get("releaseTime") for v in found):
        found.sort(key=lambda v: (v.get("releaseTime") or "", version_key(v.get("id") or "")), reverse=True)
    return found[:limit] if limit else found


def changed_since(manifest: dict, known: set[str], kinds=("release", "snapshot"),
                  limit: int | None = None) -> list[dict]:
    """Versions in the manifest that nobody has seen yet."""
    new = [v for v in versions(manifest)
           if kind_of(v) in kinds and v.get("id") not in known]
    return new[:limit] if limit else new


def resolve(manifest: dict, version_id: str) -> dict:
    """The version's own document url, so the caller knows what to fetch next."""
    for version in versions(manifest):
        if version.get("id") == version_id:
            if not version.get("url"):
                raise LookupError(f"{version_id} has no version document url in the manifest")
            return version
    raise LookupError(f"{version_id} is not in this manifest")


def download_targets(version_document: dict) -> dict:
    """What a port needs out of a version document: the client jar, the asset index, Java.

    This is deliberately read-only: it says where things are and what they are; fetching
    them is acquire.py's job, and the repository never keeps them.
    """
    downloads = version_document.get("downloads") or {}
    client = downloads.get("client") or {}
    asset_index = (version_document.get("assetIndex") or {})
    java_version = version_document.get("javaVersion") or {}
    return {
        "id": version_document.get("id"),
        "client_url": client.get("url"),
        "client_sha1": client.get("sha1"),
        "client_size": client.get("size"),
        "asset_index_url": asset_index.get("url"),
        "asset_index_id": asset_index.get("id"),
        "assets": version_document.get("assets"),
        "java_major": java_version.get("majorVersion"),
        "main_class": (version_document.get("mainClass") or "").split(".")[-1] or None,
        "libraries": len((version_document.get("libraries") or [])),
        "type": version_document.get("type"),
        "release_time": version_document.get("releaseTime"),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Look at Minecraft's version list")
    parser.add_argument("--manifest", default=MANIFEST_URL)
    parser.add_argument("--kinds", default="release")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    document = load_manifest(args.manifest)
    for entry in newest(document, tuple(args.kinds.split(",")), args.limit):
        print(f"{entry['id']:<18} {kind_of(entry):<9} {entry.get('releaseTime', '')}")
