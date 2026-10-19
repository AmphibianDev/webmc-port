#!/usr/bin/env python3
"""Stage the site for a version whose client this project has backported to Java 17.

    python3 tools/jvm_stage.py --version 1.21.11 --out /tmp/webmc-1211
    python3 tools/jvm_stage.py --version 1.21.11 --out site --mods webmc-mods/mods/real

What this writes is the same shape site/play.html already reads for every other version: an index, a plan
per version, the client, its libraries and the shim - so the page needs no special case for this row.

Three things are different from a Mojang-library plan, and each is a decision rather than a detail:

  * the client jar is *ours*: tools/backport17.py lowered 1.21.11's class files to 17 (and its 127 Java 21
    call sites with them), because no browser JVM runs Java 21. Its sha1 is over the backported file.
  * the real LWJGL jars are left out of the classpath. The shim *is* org.lwjgl here: two copies of it on
    one classpath means the real one loads its native methods and dies.
  * the version runs as Java 17, not 21, which is exactly what cheerpjInit is told.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

# Minecraft's own libraries that must not be on this classpath: the shim provides these packages itself.
SHIM_DROPS = ("org/lwjgl/", "org/lwjgl", "com/mojang/javabridge", "ca/weblite")
# The shim's packages, so a client that names one of these classes finds ours first.
SHIM_ORDER = ("org/lwjgl",)


def library_files(libs_dir: pathlib.Path) -> list[pathlib.Path]:
    """Every jar of the version's own classpath, in a stable order, with LWJGL left out."""
    found = []
    for jar in sorted(libs_dir.glob("*.jar")):
        name = jar.name
        if name.startswith("lwjgl-") or name.startswith("lwjgl_"):
            continue
        found.append(jar)
    return found


def copy_libraries(libs: list[pathlib.Path], target: pathlib.Path) -> list[str]:
    """Copy the jars into libs/ and index them by their path in the site.

    The page fetches each one at this path and writes it into CheerpJ's filesystem under its basename,
    because that filesystem does not create directories for a path it is handed.
    """
    target.mkdir(parents=True, exist_ok=True)
    entries = []
    for jar in libs:
        shutil.copy2(jar, target / jar.name)
        entries.append(f"{target.name}/{jar.name}")
    return entries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="1.21.11")
    parser.add_argument("--out", required=True, help="the site directory to write")
    parser.add_argument("--client", default=None, help="the backported client jar")
    parser.add_argument("--libs", default=None, help="the directory of the version's libraries")
    parser.add_argument("--backported-libs", default=None, help="jars that were backported themselves")
    parser.add_argument("--mods", action="append", default=[], help="directories of mod jars to stage")
    args = parser.parse_args(argv)

    version = args.version
    out = pathlib.Path(args.out).expanduser()
    client = pathlib.Path(args.client or ROOT / "work" / "client" / f"{version}-j17.jar")
    libs_dir = pathlib.Path(args.libs or ROOT / "work" / "client" / "libs" / version)
    backported_dir = pathlib.Path(args.backported_libs or ROOT / "work" / "client" / "libs"
                                  / f"{version}-j17")

    if not client.is_file():
        print(f"the backported client is not there: {client}")
        print(f"  build it with: python3 tools/backport17.py --input work/client/{version}.jar "
              f"--output {client}")
        return 2
    if not libs_dir.is_dir():
        print(f"the version's libraries are not there: {libs_dir}")
        return 2

    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(client, out / f"{version}.jar")
    digest = hashlib.sha1((out / f"{version}.jar").read_bytes()).hexdigest()

    libs = library_files(libs_dir)
    if backported_dir.is_dir():
        libs += library_files(backported_dir)
    entries = copy_libraries(libs, out / "libs")
    (out / "libs" / f"{version}.json").write_text(json.dumps(
        {"classpath": entries, "count": len(entries)}, indent=1) + "\n")

    # the shim, where page expects to import it from
    natives = out / "loader" / "natives"
    natives.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "lwjgl3-webgl" / "natives" / "webmc-lwjgl3.js", natives / "webmc-lwjgl3.js")

    mods = []
    for directory in args.mods:
        source = pathlib.Path(directory).expanduser()
        if not source.is_dir():
            continue
        (out / "mods").mkdir(parents=True, exist_ok=True)
        for jar in sorted(source.glob("*.jar")):
            shutil.copy2(jar, out / "mods" / jar.name)
            mods.append({"id": jar.stem, "file": f"mods/{jar.name}"})

    plan = {
        "id": version,
        "row": "shim",
        "verdict": "needs-lwjgl3",
        "why": f"the client was backported to Java 17 by this project (tools/backport17.py), and the "
               f"JavaScript LWJGL 3 under it is lwjgl3-webgl/natives/webmc-lwjgl3.js",
        "java": 17,
        "natives": "webmc-lwjgl3",
        "client": {"url": f"{version}.jar", "sha1": digest},
        "asset_index": version,
        "libs": f"libs/{version}.json",
        "libs_ready": True,
        "playable": True,
        "loader": None,
        "modes": {
            "vanilla": {
                "main": "net.minecraft.client.main.Main",
                "loader": False,
                "mods": [mod["id"] for mod in mods],
                "jars": [],
                "props": {},
            },
        },
    }
    if mods:
        plan["modes"]["fabric"] = {
            "main": "net.fabricmc.loader.impl.launch.knot.KnotClient",
            "loader": True,
            "mods": [mod["id"] for mod in mods],
            "jars": [],
            "props": {},
        }
    (out / "plans").mkdir(parents=True, exist_ok=True)
    (out / "plans" / f"{version}.json").write_text(json.dumps(plan, indent=1) + "\n")

    index = {
        "generated": None,
        "versions": {version: {"playable": True, "row": "shim", "modes": plan["modes"]}},
        "mods": mods,
        "note": "staged by tools/jvm_stage.py: the backported client, its libraries, and the LWJGL 3 shim",
    }
    (out / "launch.json").write_text(json.dumps(index, indent=1) + "\n")

    # the version document, so the page can find the asset index URL in it (and say so when the index
    # itself cannot be fetched: Mojang does not serve those with CORS, which the page already explains)
    meta_source = ROOT / "work" / f"v{version}.json"
    if meta_source.is_file():
        (out / "meta").mkdir(parents=True, exist_ok=True)
        shutil.copy2(meta_source, out / "meta" / f"{version}.json")
        print(f"  meta:     meta/{version}.json (for the asset index)")
    else:
        print(f"  meta:     not staged ({meta_source} is missing), the asset index stays unknown")
    plan["meta_url"] = f"meta/{version}.json"
    (out / "plans" / f"{version}.json").write_text(json.dumps(plan, indent=1) + "\n")
    shutil.copy2(ROOT / "site" / "play.html", out / "index.html")

    print(f"staged {version} in {out}")
    print(f"  client:   {version}.jar ({client.stat().st_size // 1048576} MB, sha1 {digest[:12]})")
    print(f"  libraries: {len(entries)} jars")
    print(f"  plan:     plans/{version}.json (row shim, java 17)")
    print(f"  open:     http://127.0.0.1:8777/index.html?version={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
