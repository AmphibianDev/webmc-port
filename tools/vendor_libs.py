#!/usr/bin/env python3
"""Vendor a version's own libraries, so its client can actually start.

    python3 tools/vendor_libs.py --version 1.12.2 --out site/libs --report
    python3 tools/vendor_libs.py --versions 1.12.2,1.8.9,1.7.10 --out site/libs

Why this exists: 1.12.2 declares 37 libraries and the launch page was handing the JVM two of them
(hand-picked LWJGL jars), so every version died with NoClassDefFoundError before it could draw
anything. The classpath of a Minecraft version is not a guess - it is written in the version's own
metadata - so this reads it and fetches exactly those jars.

What is deliberately not fetched: native jars for Windows/macOS/Linux. CheerpJ provides the natives
(the browser JVM has no OS libraries to load), so only the Java-side jars are needed.

Files are stored under site/libs/ keyed by the sha1 Mojang publishes, which means two versions that
share a jar share the file: the pool grows much more slowly than the number of versions. Nothing here
is a game file and nothing is committed: site/libs is git-ignored and only ever exists in the deployed
site, exactly like the CheerpJ platform pieces.
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


def fetch(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+libvendor)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def jar_name(coordinate: str, path: str) -> str:
    """A stable, readable filename for a library coordinate."""
    return pathlib.PurePosixPath(path).name or coordinate.replace(":", "-") + ".jar"


def needed_jars(document: dict) -> list[dict]:
    """Every library the classpath needs, in metadata order, deduplicated by path."""
    seen: set[str] = set()
    jars = []
    for library in document.get("libraries") or []:
        artifact = ((library.get("downloads") or {}).get("artifact")) or {}
        path = artifact.get("path")
        url = artifact.get("url")
        # natives are picked up by the JVM, not the classpath, and CheerpJ supplies them
        if not path or not url or artifact.get("classifier"):
            continue
        name = str(library.get("name") or "")
        if name.endswith(":natives") or "natives-" in str(path):
            continue
        if path in seen:
            continue
        seen.add(path)
        jars.append({"path": path, "url": url, "sha1": artifact.get("sha1"), "bytes": artifact.get("size"),
                     "name": name, "file": jar_name(name, path)})
    return jars


def vendor(version_id: str, out: pathlib.Path, manifest: dict | None = None,
           fetch_fn=fetch, log=print) -> dict:
    """Download one version's libraries into out, returning what the page needs to build a classpath."""
    manifest = manifest or version_tools.load_manifest(version_tools.MANIFEST_URL)
    entry = version_tools.resolve(manifest, version_id)
    document = json.loads(fetch_fn(entry["url"]).decode("utf-8"))
    out.mkdir(parents=True, exist_ok=True)

    classpath, downloaded, reused = [], 0, 0
    for jar in needed_jars(document):
        digest = jar.get("sha1") or ""
        # keyed by content hash: two versions sharing a jar share the file
        target = out / f"{digest[:12]}-{jar['file']}" if digest else out / jar["file"]
        if target.is_file() and target.stat().st_size > 0:
            reused += 1
        else:
            data = fetch_fn(jar["url"])
            if digest and hashlib.sha1(data).hexdigest() != digest:
                raise ValueError(f"{jar['name']}: downloaded bytes do not match the published sha1")
            target.write_bytes(data)
            downloaded += 1
        classpath.append(target.relative_to(out.parent).as_posix())

    index = {
        "version": version_id,
        "main_class": document.get("mainClass"),
        "asset_index": (document.get("assetIndex") or {}).get("id"),
        "java": (document.get("javaVersion") or {}).get("majorVersion") or 8,
        "classpath": classpath,
        "libraries": len(classpath),
        "bytes": sum((out / pathlib.PurePosixPath(c).name).stat().st_size for c in classpath),
        "source": document.get("id"),
    }
    (out / f"{version_id}.json").write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    log(f"  {version_id}: {len(classpath)} jars in the classpath "
        f"({downloaded} downloaded, {reused} already present, "
        f"{index['bytes'] / 1048576:.1f} MB on disk)")
    return index


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="one version to vendor")
    parser.add_argument("--versions", help="comma separated list")
    parser.add_argument("--limit", type=int, default=8, help="how many to do when given a list")
    parser.add_argument("--out", default=str(ROOT / "site" / "libs"))
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--from-manifest", help="vendor whatever the catalog says is launchable and not yet done")
    parser.add_argument("--budget-mb", type=int, default=700,
                        help="stop growing the pool past this size (GitHub Pages allows 1 GB)")
    args = parser.parse_args(argv)

    wanted = []
    if args.version:
        wanted.append(args.version)
    if args.versions:
        wanted.extend(v.strip() for v in args.versions.split(",") if v.strip())
    if not wanted and args.from_manifest:
        # every version the launcher can start as real Java whose libraries are not deployed yet.
        # Releases first, newest first, then snapshots newest first: the versions a person tries are
        # the ones they know, and the pool is deduplicated by sha1, so later versions reuse jars.
        document = json.loads(pathlib.Path(args.from_manifest).read_text())
        launchable = [v for v in document.get("versions", []) if v.get("jar_url")]
        wanted = [v["id"] for v in sorted(launchable, key=lambda v: v.get("released") or "", reverse=True)
                  if v.get("type") == "release" and not v.get("libs_ready")]
        wanted += [v["id"] for v in sorted(launchable, key=lambda v: v.get("released") or "", reverse=True)
                   if v.get("type") != "release" and not v.get("libs_ready")]
    if not wanted:
        # the versions a person is most likely to try first, newest first
        wanted = ["1.12.2", "1.11.2", "1.10.2", "1.8.9", "1.7.10", "1.6.4", "1.5.2", "1.2.5"]
    wanted = wanted[: args.limit]


    # GitHub Pages publishes at most 1 GB. Refusing to grow past a budget is better than a deploy that
    # silently fails, so the pool stops growing and says why.
    pool = pathlib.Path(args.out)
    if pool.is_dir():
        used = sum(f.stat().st_size for f in pool.glob("*.jar"))
        if used > args.budget_mb * 1048576:
            print(f"  the library pool is {used / 1048576:.0f} MB, over the {args.budget_mb} MB budget: "
                  f"not vendoring more this run")
            return 0

    out = pathlib.Path(args.out)
    done = []
    for version_id in wanted:
        try:
            done.append(vendor(version_id, out))
        except Exception as error:  # noqa: BLE001 - one version must not stop the rest
            print(f"  ! {version_id}: {error}", file=sys.stderr)
    total = sum(index["bytes"] for index in done)
    print(f"vendored {len(done)} version(s), {total / 1048576:.1f} MB")
    if args.report:
        print(json.dumps(done, indent=1)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
