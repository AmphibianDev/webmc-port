#!/usr/bin/env python3
"""Fetch a runnable client for a Linux box: the jar, its libraries, and its Linux natives.

    python3 fetch-client.py --version 1.12.2 --dir /opt/webmc/client

This is the VM-side equivalent of tools/vendor_libs.py, with one difference that only matters when the
game runs natively instead of in a browser: the native libraries are needed too, and for Linux, not the
platform this script happens to run on.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request
import zipfile

MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
USER_AGENT = {"User-Agent": "webmc-port/1.0 (+stream-vm)"}


def fetch(url: str, timeout: int = 120) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=USER_AGENT), timeout=timeout) as response:
        return response.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.12.2")
    parser.add_argument("--dir", default="/opt/webmc/client")
    parser.add_argument("--print-main", action="store_true")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.dir)
    (root / "libraries").mkdir(parents=True, exist_ok=True)
    (root / "natives").mkdir(parents=True, exist_ok=True)

    manifest = json.loads(fetch(MANIFEST))
    entry = next(v for v in manifest["versions"] if v["id"] == args.version)
    document = json.loads(fetch(entry["url"]))
    print(f"  {args.version}: mainClass {document.get('mainClass')}")

    jar = root / f"client-{args.version}.jar"
    if not jar.is_file():
        url = (document.get("downloads") or {}).get("client", {}).get("url")
        jar.write_bytes(fetch(url))
    print(f"  client jar: {jar} ({jar.stat().st_size / 1048576:.1f} MB)")

    classpath = []
    natives = []
    for library in document.get("libraries") or []:
        downloads = library.get("downloads") or {}
        artifact = downloads.get("artifact")
        if artifact and artifact.get("url"):
            target = root / "libraries" / pathlib.PurePosixPath(artifact["path"]).name
            if not target.is_file():
                target.write_bytes(fetch(artifact["url"]))
            classpath.append(str(target))
        classifiers = downloads.get("classifiers") or {}
        linux = classifiers.get("natives-linux")
        if linux and linux.get("url"):
            target = root / "natives" / pathlib.PurePosixPath(linux["path"]).name
            if not target.is_file():
                target.write_bytes(fetch(linux["url"]))
            natives.append(target)
    for archive in natives:
        with zipfile.ZipFile(archive) as zipped:
            for name in zipped.namelist():
                if name.endswith(".so") and "/" not in name.strip("/"):
                    (root / "natives" / pathlib.PurePosixPath(name).name).write_bytes(zipped.read(name))
    print(f"  libraries: {len(classpath)}, natives: {len(natives)}")

    (root / "classpath.txt").write_text(":".join([str(jar)] + classpath) + "\n")
    (root / "version.json").write_text(json.dumps(document, indent=1))
    (root / "run.txt").write_text(
        f"{document.get('mainClass')}\n"
        f"{(document.get('assetIndex') or {}).get('id') or args.version}\n")
    if args.print_main:
        print(document.get("mainClass"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
