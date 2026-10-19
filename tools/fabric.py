#!/usr/bin/env python3
"""What Fabric supports, per Minecraft version - both of them.

    python3 tools/fabric.py                      # refresh fixtures/fabric.json from the meta API
    python3 tools/fabric.py --print --version 1.20.4
    python3 tools/fabric.py --legacy-out site/legacy-fabric.json    # Legacy Fabric too

Fabric's own meta API is two cheap calls for the whole catalogue:

    /v2/versions/game     every game version Fabric has ever supported, with a stable flag
    /v2/versions/loader   every loader version, newest first (loaders are not per-game)

so one fetch annotates all ~857 versions instead of one request each. The result tells the launcher
three things it could otherwise only guess: whether Fabric existed for a version at all, which loader
to offer, and where the artefacts live - and maven.fabricmc.net serves its jars with
`access-control-allow-origin: *`, so a browser can actually fetch them.

There are two Fabrics, and the version decides which one a person wants:

    meta.fabricmc.net       Fabric, from 1.14 on
    meta.legacyfabric.net   Legacy Fabric, 1.3 to 1.13.2 - the only Fabric the LWJGL 2 clients have

Legacy Fabric's Maven answers with a 302 to repo.legacyfabric.net, and that host sends
`access-control-allow-origin: *` (verified, not assumed), so the redirect is usable from a page.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
META = "https://meta.fabricmc.net/v2"
MAVEN = "https://maven.fabricmc.net"
LEGACY_META = "https://meta.legacyfabric.net/v2"
LEGACY_MAVEN = "https://maven.legacyfabric.net"

# Fabric's first public release was for 1.14; older versions have never had a loader. Used only as
# prose in the launcher ("Fabric starts at 1.14"), never as a substitute for the API's answer.
FIRST_SUPPORTED = "1.14"


def fetch_json(url: str, timeout: int = 20):
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+catalog)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def loader_artifacts(game: str, loader: str, intermediary: str) -> dict:
    """Every URL a launcher needs to start this game version with Fabric."""
    return {
        "loader": f"{MAVEN}/net/fabricmc/fabric-loader/{loader}/fabric-loader-{loader}.jar",
        "intermediary": f"{MAVEN}/net/fabricmc/intermediary/{intermediary}/intermediary-{intermediary}.jar",
        "profile": f"{META}/versions/loader/{game}/{loader}/profile/json",
        "game": f"{META}/versions/game/{game}",
    }


def build(fetch=fetch_json, meta: str = META, maven: str = MAVEN, first_supported: str = FIRST_SUPPORTED) -> dict:
    """One Fabric annotation block for the whole catalogue."""
    games = fetch(f"{meta}/versions/game")
    loaders = fetch(f"{meta}/versions/loader")
    newest = loaders[0]["version"] if loaders else None

    supported, stable = {}, {}
    for entry in games:
        version = entry.get("version")
        if not version:
            continue
        supported[version] = True
        if entry.get("stable"):
            stable[version] = True

    return {
        "first_supported": first_supported,
        "newest_loader": newest,
        "loader_count": len(loaders),
        "supported": sorted(supported),
        "stable": sorted(stable),
        "source": {"game": f"{meta}/versions/game", "loader": f"{meta}/versions/loader"},
        "maven": maven,
    }


def annotate(version_id: str, data: dict) -> dict | None:
    """The Fabric block for one catalogue version, or None when Fabric never existed for it."""
    if version_id not in set(data.get("supported", [])):
        return None
    loader = data.get("newest_loader")
    return {
        "supported": True,
        "stable": version_id in set(data.get("stable", [])),
        "loader": loader,
        "intermediary": version_id,
        "artifacts": loader_artifacts(version_id, loader, version_id),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "fixtures" / "fabric.json"))
    parser.add_argument("--legacy-out", default=None,
                        help="also refresh Legacy Fabric's list into this file")
    parser.add_argument("--print", action="store_true", help="print what was fetched")
    parser.add_argument("--version", help="with --print, show one version's Fabric block")
    args = parser.parse_args(argv)

    try:
        data = build()
    except Exception as error:  # noqa: BLE001 - the caller decides how loud to be
        print(f"fabric meta unreachable: {error}", file=sys.stderr)
        return 1

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")

    if args.print or args.version:
        print(f"Fabric supports {len(data['supported'])} game versions, newest loader "
              f"{data['newest_loader']} (of {data['loader_count']})")
        if args.version:
            print(json.dumps(annotate(args.version, data), indent=1))
    try:
        shown = out.resolve().relative_to(ROOT)
    except ValueError:
        shown = out
    print(f"wrote {shown}")

    if args.legacy_out:
        # The same two calls against the other meta. Not fatal on its own: the modern list is already
        # written by the time this runs, and the launcher falls back to what it has.
        try:
            legacy = build(meta=LEGACY_META, maven=LEGACY_MAVEN, first_supported="1.3")
        except Exception as error:  # noqa: BLE001
            print(f"legacy fabric meta unreachable: {error}", file=sys.stderr)
            return 1
        legacy_out = pathlib.Path(args.legacy_out)
        legacy_out.parent.mkdir(parents=True, exist_ok=True)
        legacy_out.write_text(json.dumps(legacy, indent=1, sort_keys=True) + "\n")
        print(f"wrote {legacy_out} — Legacy Fabric supports {len(legacy['supported'])} game versions, "
              f"newest loader {legacy['newest_loader']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
