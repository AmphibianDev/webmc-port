#!/usr/bin/env python3
"""Take a packed client out of a release and put it on the site.

    python3 tools/fetch_packed.py --version 1.21.11
    python3 tools/fetch_packed.py --all --record

Why this exists: the build job's git push is the least reliable part of it (a tag-triggered run's token,
a main that moves every few minutes, a 113 MB commit) while its *release upload* works every single time.
So the division of labour is: the build job compiles, packs and publishes the packed client as a release
asset; this tool - run by the catalog job, which pushes without trouble - unpacks it into `site/clients/`
and records it in `state.json`, which is what the launcher reads.

An asset is a single `.tar.gz` per version so a version is one download rather than a dozen, and the
archive's own `manifest.json` (written by tools/pack_client.py) is left in place: the page reads it to
fetch the chunks.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
RELEASE = "built-clients"
REPO = "AmphibianDev/webmc-port"


def asset_url(stem: str) -> str:
    """`<version>` or `<version>+<mods>` - the stem is whatever the release calls it."""
    return f"https://github.com/{REPO}/releases/download/{RELEASE}/packed-{stem}.tar.gz"


def download(url: str, target: pathlib.Path) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+fetch-packed)"})
    try:
        with urllib.request.urlopen(request, timeout=600) as response, target.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        return True
    except Exception as error:  # noqa: BLE001 - the caller reports and moves on
        print(f"  {version_label(url)}: {error}")
        return False


def version_label(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def unpack(archive: pathlib.Path, version: str, site: pathlib.Path) -> dict | None:
    """Extract the packed client and return its manifest (or None if the archive is not usable)."""
    target = site / "clients" / version
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as bundle:
        members = [member for member in bundle.getmembers() if member.isfile()]
        for member in members:
            # the archive holds one directory tree; strip its leading path so the page lands at the root
            relative = pathlib.PurePosixPath(*pathlib.PurePosixPath(member.name).parts[1:])
            if not relative.parts or ".." in relative.parts:
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                continue
            destination.write_bytes(source.read())
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        return None
    # The engine's chrome is taken off here as well as in the build, and for a reason that cost a published
    # client its fixup once: an asset made before debrand ran in the build still carries the boot overlay, the
    # status console and the performance readout, and this tool is what puts that asset on the site. Editing
    # the site by hand is not enough either - the next catalog run copies the asset over it. So the pass runs
    # where the asset is unpacked, and the manifest is rehashed, because a chunk that does not match its hash
    # is a client that will not start.
    page = target / "data" / "client.0000"
    if page.is_file():
        import subprocess
        result = subprocess.run([str(pathlib.Path(__file__).with_name("debrand.py")), "--client", str(page),
                                 "--in-place"], capture_output=True, text=True)
        if result.returncode == 0 and "clean already" not in result.stdout:
            subprocess.run([str(pathlib.Path(__file__).with_name("rehash_client.py")), "--dir", str(target),
                            "--quiet"], check=False)
            print(f"{version}: the engine's chrome was taken off the unpacked client")
    return json.loads(manifest_path.read_text())


def record(state_path: pathlib.Path, version: str, manifest: dict) -> None:
    """State is the catalogue's memory: the launcher reads the manifest generated from it."""
    state = json.loads(state_path.read_text()) if state_path.is_file() else {"seen": [], "catalog": []}
    entry = (state.setdefault("built", {})).setdefault(version, {})
    entry["packed"] = f"clients/{version}/"
    entry["packed_bytes"] = manifest.get("total")
    entry["packed_chunks"] = len(manifest.get("chunks") or [])
    state_path.write_text(json.dumps(state, indent=2) + "\n")


def versions_with_assets() -> list[str]:
    """Ask the release what packed clients it has, without a token."""
    url = f"https://api.github.com/repos/{REPO}/releases/tags/{RELEASE}"
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+fetch-packed)"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            document = json.loads(response.read().decode("utf-8"))
    except Exception as error:  # noqa: BLE001
        print(f"could not list the release: {error}")
        return []
    # An archive is `packed-<version>.tar.gz` or `packed-<version>+<mods>.tar.gz`, and the modded one is a
    # superset of the vanilla client for that version. Both can exist, so a version with a modded archive is
    # served from it: the extra part of the name is what tells them apart.
    best: dict[str, str] = {}
    for asset in document.get("assets") or []:
        name = str(asset.get("name") or "")
        if not (name.startswith("packed-") and name.endswith(".tar.gz")):
            continue
        stem = name[len("packed-"):-len(".tar.gz")]
        version = stem.split("+", 1)[0]
        if version not in best or "+" in stem:
            best[version] = stem
    return sorted(best)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="append", help="a version to fetch (repeatable)")
    parser.add_argument("--all", action="store_true", help="every packed client the release has")
    parser.add_argument("--site", default=str(ROOT / "site"))
    parser.add_argument("--state", default=str(ROOT / "state.json"))
    parser.add_argument("--record", action="store_true", help="write the packed path into state.json")
    parser.add_argument("--archive", default=None, help="use this archive instead of downloading")
    args = parser.parse_args(argv)

    versions = list(args.version or [])
    if args.all:
        versions += [v for v in versions_with_assets() if v not in versions]
    if not versions:
        print("nothing to fetch: pass --version or --all")
        return 0

    site = pathlib.Path(args.site)
    fetched = 0
    with tempfile.TemporaryDirectory() as scratch:
        for stem in versions:
            # `stem` may carry a mod set (`26.2+hello-fabric`); the page still lands under the plain version.
            version = stem.split("+", 1)[0]
            archive = pathlib.Path(args.archive) if args.archive else pathlib.Path(scratch) / f"{stem}.tar.gz"
            if not args.archive:
                print(f"fetching packed-{stem}.tar.gz")
                if not download(asset_url(stem), archive):
                    continue
            manifest = unpack(archive, version, site)
            if manifest is None:
                print(f"  {version}: the archive has no manifest.json; not a packed client")
                continue
            print(f"  {version}: {len(manifest.get('chunks') or [])} chunk(s), "
                  f"{(manifest.get('total') or 0) / 1048576:.0f} MB, page at site/clients/{version}/")
            if args.record:
                record(pathlib.Path(args.state), version, manifest)
            fetched += 1
    print(f"placed {fetched} packed client(s) on the site")
    return 0 if fetched or not versions else 1


if __name__ == "__main__":
    sys.exit(main())
