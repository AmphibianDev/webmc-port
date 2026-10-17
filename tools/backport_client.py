#!/usr/bin/env python3
"""Fetch a version's client, backport it to Java 17, and stage it and its libraries for the site.

    python3 tools/backport_client.py --version 1.21.11 --site site

Why this exists: no browser JVM runs Java 21, and Minecraft 1.21.11 is Java 21. The client this stages is
Mojang's own client with 127 Java-21-only call sites rewritten and its class files lowered (see
tools/backport/J21Backport.java) - so the page can hand a real JVM a client it can load.

What it writes, and why each layout detail is what it is:

    site/clients/<version>.jar    the backported client, fetched from Mojang first
    site/libs/<name>.jar          the version's libraries, with the real LWJGL left out: the shim *is*
                                  org.lwjgl on this row, and two copies of it on one classpath means the
                                  real one loads native methods and the launch dies
    site/libs/<version>.json      the classpath, as paths relative to the site root; the page writes each
                                  jar into a flat filesystem, so it uses the basename for that

Nothing here is committed: site/libs and site/clients are in .gitignore, because these are Mojang's bytes.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def fetch(url: str, target: pathlib.Path, label: str) -> None:
    if target.is_file() and target.stat().st_size > 0:
        print(f"  {label}: already here ({target.stat().st_size // 1024} KB)")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"  {label}: fetching {url[:80]}…")
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    print(f"  {label}: {target.stat().st_size // 1024} KB")


def version_document(version: str) -> dict:
    with urllib.request.urlopen(VERSION_MANIFEST, timeout=60) as response:
        manifest = json.load(response)
    entry = next((item for item in manifest["versions"] if item["id"] == version), None)
    if not entry:
        raise SystemExit(f"{version} is not in Mojang's version list")
    with urllib.request.urlopen(entry["url"], timeout=60) as response:
        return json.load(response)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="1.21.11")
    parser.add_argument("--site", default=str(ROOT / "site"))
    args = parser.parse_args(argv)

    site = pathlib.Path(args.site)
    version = args.version
    work = ROOT / "work" / "client"
    work.mkdir(parents=True, exist_ok=True)

    document = version_document(version)
    (work / f"v{version}.json").write_text(json.dumps(document))
    print(f"Minecraft {version}: java {document.get('javaVersion', {}).get('majorVersion')}, "
          f"{len(document.get('libraries', []))} libraries")

    # the client, from Mojang
    official = work / f"{version}.jar"
    fetch(document["downloads"]["client"]["url"], official, "official client")

    # its libraries, minus the ones the shim provides
    libs_dir = work / "libs" / version
    libs_dir.mkdir(parents=True, exist_ok=True)
    wanted = []
    for library in document.get("libraries", []):
        artifact = library.get("downloads", {}).get("artifact")
        if not artifact:
            continue
        name = artifact["path"].split("/")[-1]
        if name.startswith("lwjgl-"):
            print(f"  skipping {name} (the shim is org.lwjgl on this row)")
            continue
        fetch(artifact["url"], libs_dir / name, name)
        wanted.append(name)
    print(f"  {len(wanted)} libraries staged")

    # the backport, which is the whole point of this tool
    backported = work / f"{version}-j17.jar"
    print("  backporting the client to Java 17…")
    subprocess.run([sys.executable, str(HERE / "backport17.py"),
                    "--input", str(official), "--output", str(backported)], check=True)

    # into the site: the client, the libraries, and the classpath index the page reads
    (site / "clients").mkdir(parents=True, exist_ok=True)
    shutil.copy2(backported, site / "clients" / f"{version}.jar")
    (site / "libs").mkdir(parents=True, exist_ok=True)
    entries = []
    for name in wanted:
        shutil.copy2(libs_dir / name, site / "libs" / name)
        entries.append(f"libs/{name}")
    # any library that was itself Java 21 was backported too, with -j17 in its name
    extra = work / "libs" / f"{version}-j17"
    if extra.is_dir():
        for jar in sorted(extra.glob("*.jar")):
            shutil.copy2(jar, site / "libs" / jar.name)
            entries.append(f"libs/{jar.name}")
    (site / "libs" / f"{version}.json").write_text(json.dumps(
        {"classpath": entries, "count": len(entries),
         "note": "Mojang's own libraries, with org.lwjgl left out: the JavaScript shim provides it"},
        indent=1) + "\n")

    meta = site / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    shutil.copy2(work / f"v{version}.json", meta / f"{version}.json")
    print(f"staged {version}: clients/{version}.jar and {len(entries)} libraries in {site}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
