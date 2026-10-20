#!/usr/bin/env python3
"""Recompute a packed client's hashes after its page was edited.

    python3 tools/rehash_client.py --dir site/clients/1.21.11

A packed client verifies every chunk against `manifest.json` before it runs, and the page refuses to start
when a chunk does not match. That is the right behaviour - it is what catches a truncated upload - but it also
means any tool that edits a published page in place (tools/debrand.py, mostly) has to say so in the manifest,
for every chunk it touched and for the client as a whole.

Keeping this in one place is deliberate: the hash of a chunk is computed the same way in four tools, and a
fifth that forgets is a published client that will not boot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib


def rehash(root: pathlib.Path, quiet: bool = False) -> dict:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    changed = []
    for chunk in manifest.get("chunks") or []:
        path = root / chunk["path"]
        if not path.is_file():
            raise SystemExit(f"{path} is missing: the manifest lists a chunk that is not there")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != chunk.get("sha256") or len(data) != chunk.get("size"):
            changed.append(chunk["path"])
        chunk["sha256"], chunk["size"] = digest, len(data)
    whole = hashlib.sha256()
    for chunk in manifest.get("chunks") or []:
        whole.update((root / chunk["path"]).read_bytes())
    manifest["sha256"] = whole.hexdigest()
    manifest["bytes"] = manifest["total"] = sum(chunk["size"] for chunk in manifest.get("chunks") or [])
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    if not quiet:
        print(f"{root.name}: {len(changed)} chunk hash(es) updated ({', '.join(changed) or 'none'}), "
              f"client sha {manifest['sha256'][:12]}…")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", required=True, help="a directory holding manifest.json and its chunks")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.dir)
    if not (root / "manifest.json").is_file():
        print(f"{root}/manifest.json is not there")
        return 2
    rehash(root, args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
