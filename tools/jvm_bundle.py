#!/usr/bin/env python3
"""Assemble what the browser needs to run 1.21.11 on the real JVM, and put it where the page can fetch it.

    python3 tools/jvm_bundle.py --version 1.21.11 --out site/jvm/1.21.11

Why this exists at all: CheerpJ runs Java in the page, but the page has to *fetch* the client, its libraries
and its assets first - and two of the three hosts involved refuse to be fetched from a page. Measured, not
assumed (the same three-line check that saved this project before, see the CORS skill):

    piston-data.mojang.com          access-control-allow-origin: *      the client jar, fetchable
    piston-meta.mojang.com          access-control-allow-origin: *      the version and asset index, fetchable
    maven.fabricmc.net              access-control-allow-origin: *      Fabric, fetchable at play time
    libraries.minecraft.net         (no header)                        NOT fetchable: must be hosted
    resources.download.minecraft.net (no header)                       NOT fetchable: must be hosted

So the libraries and the assets are copied here, next to the page, and everything else is fetched live.

Two deliberate omissions, both big:

  * the LWJGL jars and their natives: this repository *is* LWJGL 3 for the browser (lwjgl3-webgl), and the
    natives are irrelevant to a page. That is most of the weight.
  * assets outside a title-screen subset: the full index is 4591 objects and several hundred megabytes, of
    which the menu needs a few fonts, the GUI textures, a language file and the icons. The rest are only
    needed once a world is loaded, which is the next thing to add - and it is a download, not a mystery.

The manifest this writes is what the page reads: one list of files, each with its sha256, so the page can check
what it got and say which file was wrong rather than failing somewhere inside the JVM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

FABRIC_MAVEN = "https://maven.fabricmc.net"
# The loader's own dependencies, as its installer metadata lists them: a mismatched Mixin fails inside the
# loader rather than here, so these are pinned to one version and used both by this tool and by the page.
FABRIC = (
    "net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar",
    "net/fabricmc/sponge-mixin/0.17.4+mixin.0.8.7/sponge-mixin-0.17.4+mixin.0.8.7.jar",
    "net/fabricmc/intermediary/1.21.11/intermediary-1.21.11-v2.jar",
    "org/ow2/asm/asm/9.10.1/asm-9.10.1.jar",
    "org/ow2/asm/asm-tree/9.10.1/asm-tree-9.10.1.jar",
    "org/ow2/asm/asm-commons/9.10.1/asm-commons-9.10.1.jar",
    "org/ow2/asm/asm-analysis/9.10.1/asm-analysis-9.10.1.jar",
    "org/ow2/asm/asm-util/9.10.1/asm-util-9.10.1.jar",
)
PORT = "https://amphibiandev.github.io/webmc-port"

# What the title screen needs, by asset path prefix. Everything else waits for a world to be loaded.
TITLE_SCREEN_PREFIXES = (
    "minecraft/font/", "minecraft/textures/font/", "minecraft/textures/gui/",
    "minecraft/lang/en_us", "minecraft/textures/misc/", "icons/", "minecraft/nothing.json",
)

# Replaced by this repository's shim: shipping them would be both wrong and heavy.
SKIP_GROUPS = ("org.lwjgl",)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, target: pathlib.Path, proxy: str | None) -> pathlib.Path:
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+jvm-bundle)"})
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
        response = opener.open(request, timeout=600)
    else:
        response = urllib.request.urlopen(request, timeout=600)
    with response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="1.21.11")
    parser.add_argument("--out", default=str(ROOT / "site" / "jvm" / "1.21.11"))
    parser.add_argument("--client", default=str(ROOT / "work" / "client" / "1.21.11-j17.jar"),
                        help="the backported client (tools/backport17.py wrote it)")
    parser.add_argument("--libs", default=str(ROOT / "work" / "client" / "libs" / "1.21.11"))
    parser.add_argument("--assets", action="store_true", help="also copy the title-screen asset subset")
    parser.add_argument("--fabric", action="store_true", default=True,
                        help="vendor Fabric Loader, Mixin, intermediary and ASM (default: yes)")
    parser.add_argument("--no-fabric", dest="fabric", action="store_false")
    parser.add_argument("--version-json", default=None,
                        help="Mojang's document for the version (default work/client/<version>.json)")
    parser.add_argument("--mods", default=None,
                        help="mods to ship with the bundle, already backported to Java 17")
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)

    out = pathlib.Path(args.out).expanduser()
    version_json = pathlib.Path(args.version_json) if args.version_json \
        else ROOT / "work" / "client" / f"{args.version}.json"
    client = pathlib.Path(args.client).expanduser()
    libs = pathlib.Path(args.libs).expanduser()
    if args.mods is None:
        args.mods = str(ROOT / "work" / "client" / "mods-j17")
    if not version_json.is_file():
        print(f"{version_json} is not there: it is the version document the client's libraries come from")
        return 2
    document = json.loads(version_json.read_text())

    files = []

    # the client, backported to the Java the browser runtime has
    if client.is_file():
        target = out / "client.jar"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(client, target)
        files.append({"path": "client.jar", "bytes": target.stat().st_size, "sha256": sha256(target),
                      "what": f"the official {args.version} client, backported to Java 17"})
    else:
        print(f"  ! no backported client at {client}: run tools/backport17.py first")

    # the libraries, minus the ones this repository replaces
    missing = 0
    for library in document.get("libraries", []):
        artifact = (library.get("downloads") or {}).get("artifact")
        if not artifact or not artifact.get("path"):
            continue
        name = library["name"]
        if any(name.startswith(group) for group in SKIP_GROUPS):
            continue
        rules = library.get("rules")
        if rules and not applies(rules):
            continue
        name = pathlib.PurePosixPath(artifact["path"]).name
        # A library that is itself Java 21 bytecode was backported into libs/<version>-j17 by
        # tools/backport17.py (jtracy is the one for 1.21.11); prefer that copy, because the official jar
        # cannot be loaded by the Java 17 runtime the page has.
        backported = libs.parent / f"{libs.name}-j17" / name
        source = backported if backported.is_file() else libs / name
        if not source.is_file():
            missing += 1
            continue
        target = out / "libs" / pathlib.PurePosixPath(artifact["path"]).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files.append({"path": f"libs/{target.name}", "bytes": target.stat().st_size, "sha256": sha256(target),
                      "what": name})
    if missing:
        print(f"  ! {missing} librar(y/ies) were not in {libs}: the page will say which one is missing")

    # Mods that travel with the bundle. They are here rather than fetched at play time for one reason: a mod for
    # 1.21.11 is Java 21 bytecode, so it has to go through tools/backport_mods.py first, and that cannot happen
    # in the page. Fabric API is the one every other mod expects; a player's own mods come in the same way.
    mods = pathlib.Path(args.mods).expanduser()
    mod_count = 0
    if mods.is_dir():
        for jar in sorted(mods.glob("*.jar")):
            target = out / "mods" / jar.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(jar, target)
            files.append({"path": f"mods/{jar.name}", "bytes": target.stat().st_size, "sha256": sha256(target),
                          "what": f"{jar.name} (backported)"})
            mod_count += 1

    loader_count = 0
    # Fabric Loader, Mixin, intermediary and ASM: the loader's own dependencies, vendored here because the
    # page otherwise has to fetch them from maven.fabricmc.net at play time - and a visitor whose resolver
    # filters that host gets "without Fabric this route has nothing to run" instead of a game.
    if args.fabric:
        loader_dir = out / "loader"
        loader_dir.mkdir(parents=True, exist_ok=True)
        for path in FABRIC:
            name = path.split("/")[-1]
            target = loader_dir / name
            cached = ROOT / "work" / "client" / "fabric" / name
            if cached.is_file() and not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cached, target)
                print(f"  cached {name} ({target.stat().st_size // 1024} KB)")
            try:
                fetch(f"{FABRIC_MAVEN}/{path}", target, args.proxy)
            except Exception as error:  # noqa: BLE001 - the page can still fetch it live
                print(f"  ! {name}: {error}")
                continue
            files.append({"path": f"loader/{name}", "bytes": target.stat().st_size, "sha256": sha256(target),
                          "what": f"{name} (Fabric)"})
            loader_count += 1

    assets = 0
    if args.assets:
        # The index is one small JSON from piston-meta, which does allow CORS - but this machine's DNS does not
        # resolve Mojang's hosts, and a cached copy is the same bytes: use it when it is there.
        cached_index = ROOT / "work" / "client" / "assets-index.json"
        if cached_index.is_file():
            index = json.loads(cached_index.read_text())
            print(f"  using the cached asset index ({len(index['objects'])} objects)")
        else:
            index_url = document["assetIndex"]["url"]
            index = json.loads(fetch(index_url, out / "assets-index.json", args.proxy).read_text())
        wanted = {name: entry for name, entry in index["objects"].items()
                  if name.startswith(TITLE_SCREEN_PREFIXES)}
        for name, entry in sorted(wanted.items()):
            digest = entry["hash"]
            source = ROOT / "work" / "client" / "assets" / digest[:2] / digest
            try:
                fetch(f"https://resources.download.minecraft.net/{digest[:2]}/{digest}", source, args.proxy)
            except Exception as error:  # noqa: BLE001 - one asset must not stop the bundle
                print(f"  ! {name}: {error}")
                continue
            target = out / "assets" / "objects" / digest[:2] / digest
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            files.append({"path": f"assets/objects/{digest[:2]}/{digest}", "bytes": target.stat().st_size,
                          "sha256": sha256(target), "what": name})
            assets += 1
        # the index itself, which is how the client maps a logical name to an object
        (out / "assets" / "indexes").mkdir(parents=True, exist_ok=True)
        (out / "assets" / "indexes" / f"{args.version}.json").write_text(json.dumps(index))
        if (out / "assets-index.json").is_file():
            (out / "assets-index.json").unlink()

    manifest = {
        "version": args.version,
        "note": "Assembled by tools/jvm_bundle.py. The libraries and assets are here because their hosts send no "
                "CORS header; the client is here because it is the backported one. Fabric is fetched at play time "
                "from maven.fabricmc.net, which does allow it.",
        "client": "client.jar",
        "assets_index": f"assets/indexes/{args.version}.json",
        "files": files,
        "total_bytes": sum(entry["bytes"] for entry in files),
        "counts": {"files": len(files), "libraries": len(files) - (1 if client.is_file() else 0) - assets - mod_count
                   - loader_count, "loader": loader_count,
                   "assets": assets, "mods": mod_count},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{out}: {len(files)} file(s), {manifest['total_bytes'] / 1048576:.0f} MB "
          f"({manifest['counts']})")
    if args.report:
        for entry in sorted(files, key=lambda item: -item["bytes"])[:8]:
            print(f"   {entry['bytes'] / 1048576:6.1f} MB  {entry['path']}")
    return 0


def applies(rules: list[dict]) -> bool:
    """Whether a library's rules allow this machine - kept simple on purpose: the page runs in browsers."""
    allowed = True
    for rule in rules:
        action = rule.get("action") == "allow"
        os_name = (rule.get("os") or {}).get("name")
        if os_name in (None, "macos", "linux", "windows"):
            allowed = action
    return allowed


if __name__ == "__main__":
    raise SystemExit(main())
