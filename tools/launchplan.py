#!/usr/bin/env python3
"""The launch plan: everything a browser needs to start one version, in one file.

    python3 tools/launchplan.py --print --version 1.12.2      # what would be written for one version
    python3 tools/launchplan.py --write                       # site/launch.json, from the catalogue
    python3 tools/launchplan.py --write --stage site/mods      # ...and copy the mods next to it
    python3 tools/launchplan.py --limit 40 --write             # fetch at most 40 new Fabric profiles

A version's route is decided by its own metadata (tools/jvm_route.py). This tool turns that verdict
into the *plan* - the concrete list of things to load, in order, and the class to start:

    client jar          piston-data.mojang.com        (fetched by the visitor's browser)
    its libraries       site/libs/<version>.json      (vendored by tools/vendor_libs.py)
    the loader          site/loader/webmc-loader.jar  (built from mods/loader-jvm by our own workflow)
    Fabric              maven.fabricmc.net            (loader + ASM + Mixin + intermediary)
    mods                site/mods/<file>.jar          (the repository's mods folder, staged here)

Three rows, and the plan says which one a version is on rather than pretending they are one thing:

    cheerpj   the official jar in a browser JVM (CheerpJ). Java 8 and 17. 1.0 - 1.20.4.
    shim      the same, with our JavaScript LWJGL 3 under it. 1.13 - 1.20.4. **Not playable yet**:
              that shim has not drawn a frame, and the plan says so instead of offering a button.
    aot       no browser JVM can run Java 21+, so the client is compiled ahead of time and mods are
              baked in at build time. 1.20.5 - 26.x. The plan routes to that build, not to this page.

What the plan deliberately does not contain: any Minecraft bytes. It is a list of URLs and a class
name. The client jar comes from Mojang, the loader's libraries from Fabric's Maven, and the mods from
this repository - all fetched by the visitor's browser at the moment they press play.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import shutil
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import fabric as fabric_tools  # noqa: E402

FABRIC_META = "https://meta.fabricmc.net/v2"
LEGACY_FABRIC_META = "https://meta.legacyfabric.net/v2"

# Minecraft's own libraries that must NOT be on the classpath of the shim row: the shim *is* those
# classes (tools/lwjgl3_scaffold.py generates them from the measured surface, the JavaScript
# implements them), and a real LWJGL 3 jar would be a second copy that loads native methods that do
# not exist in a tab. Everything else in the version's library list is wanted.
SHIM_DROPS = ("org.lwjgl", "com.mojang:javabridge", "ca.weblite")

# The three rows, by the names the rest of the project already uses.
ROUTE_ROWS = {
    "cheerpj": "cheerpj",
    "cheerpj-old": "cheerpj",
    "needs-lwjgl3": "shim",
    "needs-jvm": "aot",
}

# Which mode each row can offer. Fabric is loaded by Fabric Loader, and Fabric Loader is a real
# class-path loader, so it only exists where a real JVM does.
ROW_MODES = {
    "cheerpj": ("vanilla", "fabric", "legacyfabric"),
    "shim": ("vanilla", "fabric", "legacyfabric"),
    "aot": (),
}


# --------------------------------------------------------------------------- versions

def version_key(version: str) -> tuple:
    """A sortable key for the shapes Minecraft versions actually take.

    '1.12.2' -> ((1, 12, 2, 0), 0) · '26.4-snapshot-4' -> ((26, 4, 0, 0), -2) · 'b1.7.3' -> ((1, 7, 3, 0), -3)

    The numbers are padded to a fixed width so that '26.4' and the snapshots leading to it ('26.4' plus
    a fourth number) compare equal on the numbers alone - and then the second element decides. That is
    what makes a snapshot sort *below* the release it leads to, which is what a range written by a mod
    author means by '1.14' and '1.20.5'. Betas and alphas sort below the releases, not above.
    """
    text = version.strip().lower()
    kind = 0
    base = text
    if re.match(r"^[ab]\d", text):
        kind = -3
    elif "snapshot" in text:
        kind = -2
        base = text.split("snapshot")[0]
    elif "pre" in text or "rc" in text:
        kind = -1
        base = re.split(r"-?(?:pre|rc)", text)[0]
    numbers = _numbers(base)[:4]
    return (numbers + (0,) * (4 - len(numbers)), kind)


def _numbers(text: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def matches(spec: str, version: str) -> bool:
    """Whether a mod declares support for a version. Deliberately two simple things.

    '1.14 1.20.4'    a list of versions - any one of them matches, which is how a mod says "these"
    '>=1.14 <1.21'   comparisons - all of them must hold, which is how a mod says "this window"
    '1.12.*'         a prefix (the trailing '*' is optional)
    '*'  or  'any'   everything

    The two may be mixed ('1.8.9 >=1.7.10 <1.13'), and a bare token that is a prefix matches the whole
    family: '1.12.' covers 1.12, 1.12.1 and 1.12.2. No caret ranges, no tilde, no full semver: a mod
    that needs something more specific than this can list the versions, and either way the behaviour is
    pinned by tests/test_launchplan.py rather than left to the reader.
    """
    spec = (spec or "").strip()
    if spec in ("", "*", "any"):
        return True
    constraints, wanted = [], []
    for token in spec.split():
        if token[0] in "<>=":
            constraints.append(token)
        elif token not in ("*", "any"):
            wanted.append(token)
    for token in constraints:
        if not _holds(token, version):
            return False
    if wanted and not any(_prefix(token, version) for token in wanted):
        return False
    return True


def _holds(token: str, version: str) -> bool:
    """One comparison token, out of the four a mod would write."""
    current, wanted = version_key(version), version_key(token.lstrip("<>= "))
    if token.startswith(">="):
        return current >= wanted
    if token.startswith("<="):
        return current <= wanted
    if token.startswith(">"):
        return current > wanted
    if token.startswith("<"):
        return current < wanted
    return version == token.lstrip("=")


def _prefix(token: str, version: str) -> bool:
    """'1.12' and '1.12.' both mean the 1.12 family; a trailing '*', 'x' or '.' is decoration."""
    wanted = token.rstrip("*x.").strip()
    return version == wanted or version.startswith(wanted)


# --------------------------------------------------------------------------- the mods folder

def load_mods(path: pathlib.Path, root: pathlib.Path, log=print) -> list[dict]:
    """Read mods/mods.json and check that every jar it names is actually there.

    The file is the source of truth for the launcher and for this tool; the jars themselves are
    third-party binaries, so they are git-ignored and dropped into mods/jars/ by hand. A manifest
    entry whose jar is missing is reported and skipped rather than failing the whole build: one mod
    falling off the shelf must not take the launcher down with it.
    """
    if not path.is_file():
        log(f"  mods: no manifest at {path}")
        return []
    document = json.loads(path.read_text())
    mods = []
    for entry in document.get("mods") or []:
        file = str(entry.get("file") or "")
        if not file:
            log(f"  mods: {entry.get('id')}: no file")
            continue
        jar = (path.parent / file) if not pathlib.Path(file).is_absolute() else pathlib.Path(file)
        if not jar.is_file():
            log(f"  mods: {entry.get('id')}: {jar} is not there - skipped")
            continue
        data = jar.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        pinned = str(entry.get("sha256") or "")
        if pinned and pinned != digest:
            log(f"  mods: {entry.get('id')}: sha256 does not match the manifest - skipped")
            continue
        mods.append({
            "id": entry.get("id") or jar.stem,
            "name": entry.get("name") or entry.get("id") or jar.stem,
            "version": entry.get("version") or "unknown",
            "side": entry.get("side") or "client",
            # relative to the site root, because that is how the page fetches it
            "file": f"mods/{jar.name}",
            "source": str(jar.relative_to(ROOT)) if ROOT in jar.resolve().parents else str(jar),
            "sha256": pinned or digest,
            "bytes": len(data),
            "game": entry.get("game") or ["*"],
            "loaders": entry.get("loaders") or ["fabric", "legacyfabric"],
            "entrypoint": entry.get("entrypoint"),
            "note": entry.get("note"),
        })
    return mods


def mods_for(mods: list[dict], version: str, mode: str) -> list[dict]:
    """The mods that declare support for this version in this mode."""
    return [mod for mod in mods
            if mode in (mod.get("loaders") or []) and any(matches(spec, version) for spec in mod["game"])]


# --------------------------------------------------------------------------- Fabric

def fetch_json(url: str, timeout: int = 30) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+launchplan)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fabric_profile(meta: str, game: str, loader: str, fetch=fetch_json) -> dict | None:
    """Fabric's own loader profile for one game version: the class to start and the jars to load.

    This is the same document the official launcher consumes, so the version of ASM, Mixin and
    intermediary is Fabric's choice rather than a guess - and maven.fabricmc.net serves it with
    `access-control-allow-origin: *`, which is what makes it usable from a page at all.
    """
    try:
        return fetch(f"{meta}/versions/loader/{game}/{loader}/profile/json")
    except Exception as error:  # noqa: BLE001 - the caller records the reason and moves on
        return {"error": str(error)}


def fabric_run(profile: dict, mode: str) -> dict:
    """The run block for Fabric, from Fabric's profile document."""
    libraries = []
    for library in profile.get("libraries") or []:
        name = str(library.get("name") or "")
        base = str(library.get("url") or "").rstrip("/")
        if not name or not base:
            continue
        group, artifact, version = name.split(":")[:3]
        path = f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.jar"
        libraries.append({
            "name": name,
            "url": f"{base}/{path}",
            # the version's own classpath already has netty/guava/etc; counting them twice is how a
            # classpath gets an old copy of a library in front of the one the game wanted
            "duplicate_of_mojang": group in ("com.google.guava", "io.netty", "com.google.code.gson"),
        })
    return {
        "mode": mode,
        "loader": "legacyfabric" if mode == "legacyfabric" else "fabric",
        "loader_version": profile.get("id"),
        "main": profile.get("mainClass") or "net.fabricmc.loader.impl.launch.knot.KnotClient",
        "jars": [library["url"] for library in libraries],
        "libraries": libraries,
        # Fabric reads its configuration from system properties, and these are the ones that matter
        # when the game jar was not put on the classpath by the launcher that normally does it:
        #   fabric.gameJarPath  where the game is (the page's own VFS path)
        #   fabric.addMods      extra mods beyond ./mods, which is the only way to hand Fabric a mod
        #                       that lives in a browser filesystem
        #   fabric.side         a client, not a server
        # The values that depend on a path are filled in by the page, which is what wrote the files.
        "props": {
            "fabric.side": "client",
            "fabric.development": "false",
            "fabric.gameVersion": None,  # filled per version below
            "fabric.modsFolder": "/str/mods",
        },
        "jvm_args": ["-DFabricMcEmu=net.minecraft.client.main.Main"],
    }


# --------------------------------------------------------------------------- the plan

def route_of(verdict: dict, client_url: str | None) -> str:
    """The row a version is on. Blocked routes keep their name so the page can explain them."""
    row = ROUTE_ROWS.get(str(verdict.get("route") or ""), "queued")
    if row != "cheerpj" and str(verdict.get("route")) in ("needs-lwjgl3", "needs-jvm"):
        # the wall is real and named: 1.13+ waits for the shim, 1.20.5+ for a Java 21 runtime
        return row
    if not client_url:
        return "queued"
    return row


def fabric_entry(source: dict, version_id: str) -> dict:
    """What Fabric's meta says about one version, from either shape that file comes in.

    tools/fabric.py writes a whole-catalogue document (`supported` is a list), while a per-version
    annotation is a dict. Both are read here so the launcher and this tool cannot disagree about
    which versions have Fabric.
    """
    entry = source.get(version_id)
    if isinstance(entry, dict):
        return entry
    if version_id in set(source.get("supported") or []):
        return {"supported": True, "loader": source.get("newest_loader")}
    return {}


def plan_version(version: dict, verdict: dict, mods: list[dict], sources: dict,
                 profiles: dict, meta_newest: str) -> dict:
    """One version's plan: what to load, in what order, and which class starts."""
    version_id = version["id"]
    client = {"url": verdict.get("jar_url"), "sha1": verdict.get("jar_sha1"),
              "bytes": verdict.get("jar_bytes")}
    row = route_of(verdict, client["url"])
    libs_index = ROOT / "site" / "libs" / f"{version_id}.json"
    plan = {
        "id": version_id,
        "type": version.get("type"),
        "released": version.get("released") or version.get("releaseTime"),
        "row": row,
        "verdict": verdict.get("route"),
        "why": verdict.get("why"),
        "java": verdict.get("java") or 8,
        "lwjgl": verdict.get("lwjgl"),
        "natives": "webmc-lwjgl3" if row == "shim" else "lwjgl-natives",
        "client": client,
        "meta_url": verdict.get("meta_url"),
        "asset_index": verdict.get("asset_index"),
        "libs": f"libs/{version_id}.json",
        "libs_ready": libs_index.is_file(),
        "playable": False,
        "modes": {},
    }
    if row == "cheerpj":
        # A version is startable when its own libraries have been vendored: with only the fallback
        # LWJGL jars a modern-ish client dies with NoClassDefFoundError before it draws anything.
        plan["playable"] = bool(client["url"]) and plan["libs_ready"]
        if not plan["playable"]:
            plan["blocker"] = ("its own libraries are not deployed yet; the catalog job vendors a few "
                               "more versions every run" if client["url"] else
                               "Mojang's metadata has no client jar for it")
    elif row == "shim":
        plan["blocker"] = ("1.13 and newer need a JavaScript LWJGL 3, and ours has not drawn its first "
                           "frame yet: " + str(verdict.get("why") or ""))
    elif row == "aot":
        plan["note"] = ("no browser JVM runs this one: Java 21 or newer. The compiled build job "
                        "produces a client for it, and mods go in at build time.")
        return plan
    if row == "queued":
        plan["blocker"] = ("Mojang's metadata has no client jar for it" if not client["url"]
                           else verdict.get("why") or "its version document has not been read yet")
        return plan

    # vanilla ------------------------------------------------------------------
    plan["modes"]["vanilla"] = {
        "mode": "vanilla",
        "main": verdict.get("main_class") or "net.minecraft.client.Minecraft",
        "jars": [],
        # Our own loader is only needed when there is something for it to load; with no mods the
        # client starts directly, which is one class less between the click and the title screen.
        "loader": bool(mods_for(mods, version_id, "vanilla")),
        "props": {},
    }

    # fabric -------------------------------------------------------------------
    for mode in ("fabric", "legacyfabric"):
        entry = fabric_entry(sources.get(mode) or {}, version_id)
        if not entry.get("supported"):
            continue
        if mode not in ROW_MODES.get(row, ()):
            continue
        profile = profiles.get((mode, version_id))
        if not profile or profile.get("error"):
            plan.setdefault("mode_errors", {})[mode] = (profile or {}).get("error", "no profile")
            continue
        run = fabric_run(profile, mode)
        run["props"]["fabric.gameVersion"] = version_id
        run["loader"] = True
        plan["modes"][mode] = run

    for mode, run in plan["modes"].items():
        run["mods"] = [mod["id"] for mod in mods_for(mods, version_id, mode)]
    return plan


def build(manifest: dict, jvm: dict, fabric: dict, mods: list[dict], loader: dict,
          limit: int = 40, fetch=fetch_json, log=print, legacy: dict | None = None) -> dict:
    """The whole document. Versions come from the catalogue, verdicts from tools/jvm_route.py."""
    sources = {"fabric": fabric or {}, "legacyfabric": legacy if legacy is not None else fabric or {}}
    versions = []
    seen = set()
    for entry in manifest.get("versions") or []:
        versions.append({"id": entry["id"], "type": entry.get("type"),
                         "released": entry.get("releaseTime") or entry.get("released")})
        seen.add(entry["id"])
    # Everything that has been classified but is not in the launcher's own list yet: the whole point
    # of classifying a version is that it can be offered, so a verdict must never be a dead end.
    for version_id, verdict in sorted((jvm or {}).items(), key=lambda pair: version_key(pair[0]),
                                      reverse=True):
        if version_id in seen:
            continue
        versions.append({"id": version_id, "type": verdict.get("type"),
                         "released": verdict.get("released")})
        seen.add(version_id)

    profiles = {}
    fetched = 0
    plans = {}
    for version in versions:
        verdict = (jvm or {}).get(version["id"])
        if not verdict:
            verdict = {"route": "unknown", "why": "not classified yet"}
        row = ROUTE_ROWS.get(str(verdict.get("route") or ""), "queued")
        # Fabric profiles are one request each, so they are fetched for the versions that could
        # actually use one, up to the per-run budget - the same convergence the classifier uses.
        if row in ("cheerpj", "shim"):
            for mode, meta in (("fabric", FABRIC_META), ("legacyfabric", LEGACY_FABRIC_META)):
                entry = fabric_entry(sources[mode], version["id"])
                if not entry.get("supported") or fetched >= limit:
                    continue
                profile = fabric_profile(meta, version["id"],
                                         entry.get("loader") or sources[mode].get("newest_loader"),
                                         fetch=fetch)
                fetched += 1
                profiles[(mode, version["id"])] = profile or {"error": "no profile"}
                if (profile or {}).get("error"):
                    log(f"  ! {version['id']} ({mode}): {profile['error']}")
        plans[version["id"]] = plan_version(version, verdict, mods, sources, profiles,
                                             (sources["fabric"] or {}).get("newest_loader"))
    log(f"  fabric profiles: {fetched} fetched, "
        f"{sum(1 for profile in profiles.values() if not profile.get('error'))} usable")

    return {
        "note": ("Generated by tools/launchplan.py. A list of URLs and class names - no game files, "
                 "no mods. The browser fetches everything from Mojang, Fabric's Maven, or this site."),
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "loader": loader,
        "mods": [{key: mod[key] for key in ("id", "name", "version", "file", "sha256", "bytes",
                                            "game", "loaders", "side")} for mod in mods],
        "versions": plans,
    }


def loader_block(path: pathlib.Path) -> dict:
    """What the page needs to fetch and start our own loader."""
    block = {"jar": "loader/webmc-loader.jar", "class": "webmc.loader.WebMcLoader"}
    if path.is_file():
        data = path.read_bytes()
        block.update({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    else:
        block["missing"] = ("site/loader/webmc-loader.jar has not been built on this machine; the "
                            "page falls back to starting the game class itself")
    return block


def stage_mods(mods: list[dict], out: pathlib.Path, log=print) -> int:
    """Copy the mods folder into the site, which is the only tree GitHub Pages serves."""
    out.mkdir(parents=True, exist_ok=True)
    copied = 0
    for mod in mods:
        source = ROOT / mod["source"] if not pathlib.Path(mod["source"]).is_absolute() else pathlib.Path(mod["source"])
        if not source.is_file():
            continue
        target = out / pathlib.Path(mod["file"]).name
        shutil.copyfile(source, target)
        copied += 1
    log(f"  staged {copied} mod jar(s) into {out}")
    return copied


def index_of(document: dict) -> dict:
    """The small half: what the launcher needs to *offer* a version, without any of the paths.

    Keeping this separate from the plans themselves is what lets the launcher card say "vanilla or
    Fabric, 2 compatible mods" for 600 versions without downloading 600 launch plans; the plan for
    the one version somebody clicked is fetched when they click play.
    """
    versions = {}
    for version_id, plan in document["versions"].items():
        entry = {
            "row": plan["row"],
            "why": plan.get("why"),
            "playable": bool(plan.get("playable")),
            "java": plan.get("java"),
            "modes": {mode: (run.get("mods") or []) for mode, run in (plan.get("modes") or {}).items()},
        }
        for key in ("blocker", "note"):
            if plan.get(key):
                entry[key] = plan[key]
        versions[version_id] = entry
    return {
        "note": document["note"],
        "updated": document["updated"],
        "loader": document["loader"],
        "mods": document["mods"],
        "versions": versions,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(ROOT / "site" / "manifest.json"))
    parser.add_argument("--state", default=str(ROOT / "state.json"))
    parser.add_argument("--fabric", default=str(ROOT / "site" / "fabric.json"))
    parser.add_argument("--legacy", default=str(ROOT / "site" / "legacy-fabric.json"),
                        help="Legacy Fabric's list, for 1.3 - 1.13.2")
    parser.add_argument("--mods", default=str(ROOT / "mods" / "mods.json"))
    parser.add_argument("--loader-jar", default=str(ROOT / "site" / "loader" / "webmc-loader.jar"))
    parser.add_argument("--out", default=str(ROOT / "site" / "launch.json"),
                        help="the index the launcher reads")
    parser.add_argument("--plans", default=str(ROOT / "site" / "plans"),
                        help="one plan per version, fetched when a launch starts")
    parser.add_argument("--stage", default=None, help="copy the mods into this folder (site/mods)")
    parser.add_argument("--limit", type=int, default=40, help="new Fabric profiles per run")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--print", action="store_true")
    parser.add_argument("--version", help="with --print, only this version")
    args = parser.parse_args(argv)

    manifest = json.loads(pathlib.Path(args.manifest).read_text()) if pathlib.Path(args.manifest).is_file() else {"versions": []}
    state = json.loads(pathlib.Path(args.state).read_text()) if pathlib.Path(args.state).is_file() else {}
    fabric_data = json.loads(pathlib.Path(args.fabric).read_text()) if pathlib.Path(args.fabric).is_file() else {}
    legacy_data = json.loads(pathlib.Path(args.legacy).read_text()) if pathlib.Path(args.legacy).is_file() else {}
    mods = load_mods(pathlib.Path(args.mods), ROOT)
    print(f"mods: {len(mods)} in the manifest"
          + (f" ({', '.join(mod['id'] for mod in mods)})" if mods else ""))
    print(f"fabric: {len(fabric_data.get('supported') or [])} versions from the modern meta, "
          f"{len(legacy_data.get('supported') or [])} from Legacy Fabric")

    document = build(manifest, state.get("jvm") or {}, fabric_data, mods,
                     loader_block(pathlib.Path(args.loader_jar)), limit=args.limit, legacy=legacy_data)

    if args.stage:
        stage_mods(mods, pathlib.Path(args.stage))

    if args.version:
        print(json.dumps(document["versions"].get(args.version), indent=1))
        return 0

    counts: dict[str, int] = {}
    for plan in document["versions"].values():
        key = f"{plan['row']}" + (" (playable)" if plan["modes"] else "")
        counts[key] = counts.get(key, 0) + 1
    print(f"planned {len(document['versions'])} versions: "
          + ", ".join(f"{name}: {count}" for name, count in sorted(counts.items())))
    fabric_modes = sum(1 for plan in document["versions"].values() if "fabric" in plan["modes"])
    print(f"fabric available on {fabric_modes} of them, "
          f"{len(mods)} mod(s) in the folder, loader "
          + ("built" if "sha256" in document["loader"] else "NOT BUILT (run tools/build_loader.py)"))

    if args.print:
        print(json.dumps(document["versions"], indent=1)[:4000])

    if args.write:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        index = index_of(document)
        out.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
        plans = pathlib.Path(args.plans)
        plans.mkdir(parents=True, exist_ok=True)
        written = 0
        for version_id, plan in document["versions"].items():
            # A queued version has nothing to launch, so it has no plan file: the index entry (with
            # its reason) is the whole story, and the launcher reads exactly that.
            if not plan.get("modes"):
                continue
            target = plans / f"{version_id}.json"
            target.write_text(json.dumps(plan, indent=1, sort_keys=True) + "\n")
            written += 1
        try:
            shown = out.resolve().relative_to(ROOT)
        except ValueError:
            shown = out
        print(f"wrote {shown} ({out.stat().st_size / 1024:.0f} kB index) "
              f"and {written} plan(s) under {plans.name}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
