#!/usr/bin/env python3
"""Which versions a browser JVM can actually run, decided from Mojang's own version documents.

    python3 tools/jvm_route.py --state state.json --write            # classify what is cached
    python3 tools/jvm_route.py --state state.json --limit 80 --write  # fetch more, politely
    python3 tools/jvm_route.py --document fixtures/1.12.2.json --print

The launcher's "real Java" route is not a matter of taste: a version runs under CheerpJ if, and only
if, its own metadata says it needs an old enough Java and an old enough LWJGL.

    LWJGL 2.9.x + Java 8 or older   -> the browser JVM can run it (this is 1.6.1 through 1.12.2,
                                       and every snapshot in that window - hundreds of versions)
    LWJGL 3                         -> needs a JavaScript LWJGL 3 that nobody has written
    Java 21 or newer                -> needs a JVM the browser runtime does not have
    pre-shader (alpha/beta)         -> needs fixed-function OpenGL, which WebGL does not provide

Versions are classified from one small request each, cached in state.json, and only for versions
that could plausibly qualify (releases and snapshots from the LWJGL 2 era), so a run costs a handful
of requests and the catalog converges over several runs instead of hammering Mojang with 857.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

# 1.6.1 (2013-07) shipped LWJGL 2.9.3; 1.13 (2018-07) shipped LWJGL 3. Snapshots in between are the
# ones worth asking about. The date filter is only about *asking*: the verdict always comes from the
# document, never from the date.
LWJGL2_FIRST = "2013-06-01"
LWJGL2_LAST = "2017-12-01"
NEWEST_JAVA_FOR_CHEERPJ = 17  # CheerpJ's ceiling; 21+ is a different runtime entirely


def fetch_document(url: str, timeout: int = 60) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+catalog)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def lwjgl_major(document: dict) -> int | None:
    """2 or 3, straight out of the library list; None when the document does not say."""
    for library in document.get("libraries") or []:
        name = str(library.get("name") or "")
        if name.startswith("org.lwjgl.lwjgl:lwjgl:"):
            return 2
        if name.startswith("org.lwjgl:lwjgl:"):
            return 3
    return None


def lwjgl_version(document: dict) -> str | None:
    for library in document.get("libraries") or []:
        name = str(library.get("name") or "")
        if name.startswith("org.lwjgl.lwjgl:lwjgl:") or name.startswith("org.lwjgl:lwjgl:"):
            return name.split(":")[2]
    return None


def classify(document: dict) -> dict:
    """The verdict for one version document, with the reason in words.

    Order matters, because more than one wall can apply to the same version. The Java version is
    checked first: 1.20.6 has both a Java 21 requirement and LWJGL 3, and a JavaScript LWJGL 3 would
    not save it, so saying "needs Java 21" is the useful answer.
    """
    java = ((document.get("javaVersion") or {}).get("majorVersion"))
    major = lwjgl_major(document)
    version = lwjgl_version(document)
    kind = str(document.get("type") or "")

    if java and java > NEWEST_JAVA_FOR_CHEERPJ:
        return {"route": "needs-jvm",
                "why": f"needs Java {java}; the browser JVM stops at {NEWEST_JAVA_FOR_CHEERPJ}",
                "lwjgl": version, "java": java}
    if major == 3:
        return {"route": "needs-lwjgl3",
                "why": f"LWJGL {version}: needs a JavaScript LWJGL 3, which does not exist yet",
                "lwjgl": version, "java": java}
    if kind in ("old_beta", "old_alpha"):
        return {"route": "cheerpj-old",
                "why": "alpha/beta era: LWJGL 2 but pre-shader, so it draws with fixed-function "
                       "OpenGL. CheerpJ's GL bridge may not cover that - unverified.",
                "lwjgl": version, "java": java}
    if major == 2 and str(version or "").startswith("2.9"):
        return {"route": "cheerpj",
                "why": f"LWJGL {version} with Java {java or 8}: the browser JVM can run this",
                "lwjgl": version, "java": java}
    if major == 2:
        return {"route": "cheerpj-old",
                "why": f"LWJGL {version} (older than 2.9): the browser JVM ships 2.9.3 natives, so "
                       f"this is an attempt rather than a promise",
                "lwjgl": version, "java": java}
    return {"route": "unknown", "why": "the metadata names no LWJGL: cannot say yet",
            "lwjgl": version, "java": java}


# The three walls, by the date Minecraft crossed them. These are used only until a version's own
# document has been read; then the document wins, because it is the authority.
LWJGL3_FIRST = "2017-11-01"   # 17w43a, the first 1.13 snapshot
JAVA21_FIRST = "2024-04-23"   # 1.20.5
JAVA25_FIRST = "2026-01-01"   # the 26.x line


EARA_WALLS = (
    (JAVA25_FIRST, "needs-jvm", "this is a 26.x version: it needs Java 21 or newer, and the browser JVM stops at 17"),
    (JAVA21_FIRST, "needs-jvm", "this needs Java 21 or newer; the browser JVM stops at 17"),
    (LWJGL3_FIRST, "needs-lwjgl3", "this is 1.13 or newer: it needs a JavaScript LWJGL 3, which does not exist yet"),
    (LWJGL2_FIRST, "cheerpj", "LWJGL 2 era: the browser JVM can run this (verdict from the date so far)"),
)


def era_verdict(version: dict) -> dict:
    """A verdict from the release date alone, for versions no document has been read for yet."""
    if version.get("type") in ("old_beta", "old_alpha"):
        return {"route": "cheerpj-old", "source": "era",
                "why": "alpha/beta era: pre-shader, so it draws with fixed-function OpenGL. An "
                       "attempt rather than a promise - not every one of these will boot."}
    released = str(version.get("releaseTime") or "")
    for first_date, route, why in EARA_WALLS:
        if released >= first_date:
            return {"route": route, "source": "era", "why": why}
    if released:
        return {"route": "unknown", "source": "era",
                "why": "older than the LWJGL 2 range: no verdict yet, and probably no client jar "
                       "in Mojang's metadata"}
    return {"route": "unknown", "source": "era", "why": "no release date in the metadata"}


def interesting(version: dict, known: dict) -> bool:
    """Whether this version is worth spending a request on."""
    if version["id"] in known:
        return False
    released = str(version.get("releaseTime") or "")
    if version.get("type") in ("old_beta", "old_alpha"):
        # the route comes from the type, but the jar url still has to be read out of the document,
        # and a version whose metadata has no client download simply never becomes playable
        return True
    return LWJGL2_FIRST <= released <= LWJGL2_LAST


def refresh(manifest: dict, state: dict, limit: int, fetch=fetch_document, log=print) -> dict:
    """Classify up to `limit` versions that are not known yet; return the updated table."""
    table = dict(state.get("jvm") or {})
    todo = [v for v in manifest["versions"] if interesting(v, table)][: max(0, limit)]
    for version in todo:
        try:
            document = fetch(version["url"])
        except Exception as error:  # noqa: BLE001 - one bad version must not stop the run
            log(f"  ! {version['id']}: {error}")
            continue
        verdict = classify(document)
        verdict["source"] = "document"
        downloads = document.get("downloads") or {}
        verdict.update({
            "id": version["id"],
            "type": version.get("type"),
            "released": version.get("releaseTime"),
            "jar_url": (downloads.get("client") or {}).get("url"),
            "jar_sha1": (downloads.get("client") or {}).get("sha1"),
            "jar_bytes": (downloads.get("client") or {}).get("size"),
            "main_class": document.get("mainClass"),
            "asset_index": (document.get("assetIndex") or {}).get("id"),
            "meta_url": version.get("url"),
            "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        table[version["id"]] = verdict
    state["jvm"] = table
    return table


def summarise(table: dict) -> str:
    counts: dict[str, int] = {}
    for verdict in table.values():
        counts[verdict["route"]] = counts.get(verdict["route"], 0) + 1
    return ", ".join(f"{route}: {count}" for route, count in sorted(counts.items()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=None,
                        help="local manifest json; by default Mojang's live version list is used")
    parser.add_argument("--state", default=str(ROOT / "state.json"))
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--document", help="classify one version document and print the verdict")
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)

    if args.document:
        document = json.loads(pathlib.Path(args.document).read_text())
        print(json.dumps(classify(document), indent=1))
        return 0

    if args.manifest:
        manifest = json.loads(pathlib.Path(args.manifest).read_text())
    else:
        sys.path.insert(0, str(HERE))
        import versions as version_tools  # noqa: PLC0415 - only needed on this path
        # load_manifest takes a URL as happily as a path; the classifier wants the live list
        manifest = version_tools.load_manifest(version_tools.MANIFEST_URL)
    state_path = pathlib.Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    before = len(state.get("jvm") or {})
    table = refresh(manifest, state, args.limit)
    print(f"classified {len(table)} versions (+{len(table) - before} this run): {summarise(table)}")
    if args.write:
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        print(f"wrote {state_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
