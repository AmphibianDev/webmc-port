#!/usr/bin/env python3
"""Bake the Fabric API modules a mod set asks for, not all forty-three of them.

    python3 tools/prune_fabric_api.py --fabric-api mods/vendor/fabric-api-26.2.jar --mods /tmp/mods --out /tmp/mods

Why this exists: Fabric API is a bundle of ~43 module jars, and a mod uses a handful. Baking the *whole*
bundle compiles modules nobody asked for, and those modules are exactly the ones a browser cannot carry -
Indigo's mixin plugin, the networking stack, Quilt compatibility, the game-test harness - which is how a
build ends up failing on `Class java.lang.ScopedValue was not found` for a mod that never touches it.

What it does:

  1. reads the mod jars in --mods and works out which `net/fabricmc/fabric/...` packages they reference
     (the same bytecode scan `mods/apisurface.py` does, because a mod's *bytecode* is the truth about what
     it calls, and its descriptor is not);
  2. finds the module inside the bundle that provides each of those packages;
  3. follows each module's own `depends` to other Fabric API modules, because a module that runs needs the
     modules it names;
  4. writes those modules as individual jars, and says by name what it left out and why.

The result is a smaller bake set that compiles, and a log line that says exactly which modules are in it.
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import re
import shutil
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mods"))

FABRIC_PACKAGE = re.compile(r"^net/fabricmc/fabric/(.+)$")


def referenced_packages(jar_path: pathlib.Path) -> set[str]:
    """Every net/fabricmc/fabric package a mod's own bytecode mentions."""
    packages: set[str] = set()
    try:
        with zipfile.ZipFile(jar_path) as archive:
            for name in archive.namelist():
                if not name.endswith(".class"):
                    continue
                try:
                    refs = parse_refs(archive.read(name))
                except Exception:  # noqa: BLE001 - a class this cannot read demands nothing
                    continue
                for _kind, owner, _name, _descriptor in refs:
                    match = FABRIC_PACKAGE.match(owner)
                    if match:
                        # net/fabricmc/fabric/api/client/keymapping/v1/Foo -> .../v1
                        packages.add("net/fabricmc/fabric/" + match.group(1).rsplit("/", 1)[0])
    except zipfile.BadZipFile:
        pass
    return packages


def parse_refs(data: bytes):
    import apisurface  # type: ignore[import-not-found]  # the repository's own scanner

    return apisurface.parse_refs(data)


def module_packages(module_bytes: bytes) -> set[str]:
    """The net/fabricmc packages a module jar's classes live in."""
    packages: set[str] = set()
    with zipfile.ZipFile(io.BytesIO(module_bytes)) as archive:
        for name in archive.namelist():
            if not name.endswith(".class"):
                continue
            packages.add(name.rsplit("/", 1)[0])
    return packages


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fabric-api", required=True, help="the Fabric API bundle jar")
    parser.add_argument("--mods", required=True, help="the folder of mod jars to be baked")
    parser.add_argument("--out", required=True, help="where the module jars are written")
    parser.add_argument("--keep-bundle", action="store_true", help="leave the bundle in place as well")
    args = parser.parse_args(argv)

    bundle = pathlib.Path(args.fabric_api)
    mods_dir = pathlib.Path(args.mods)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    wanted: set[str] = set()
    for mod in sorted(mods_dir.glob("*.jar")):
        wanted |= referenced_packages(mod)
    if not wanted:
        print("  no mod asks for Fabric API: nothing to prune")
        return 0

    with zipfile.ZipFile(bundle) as archive:
        modules = {}
        for name in archive.namelist():
            if not (name.startswith("META-INF/jars/") and name.endswith(".jar")):
                continue
            data = archive.read(name)
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as module:
                    descriptor = json.loads(module.read("fabric.mod.json").decode("utf-8"))
            except Exception:  # noqa: BLE001 - not a module, or not a descriptor
                continue
            modules[descriptor.get("id") or name] = {
                "file": name,
                "data": data,
                "id": descriptor.get("id"),
                "name": descriptor.get("name"),
                "depends": descriptor.get("depends") or {},
                "packages": module_packages(data),
            }

    # Modules that need a JVM this client does not have: TeaVM has no JDK-21 methods they use, and their
    # subject matter (a second registry layer, a networking stack, Quilt compatibility, the game-test
    # harness) has no browser equivalent yet. They are skipped by name, and a mod that truly needs one is
    # reported instead of silently half-building.
    UNCOMPILABLE = {
        "fabric-networking-api-v1", "fabric-registry-sync-v0", "fabric-content-registries-v0",
        "fabric-transfer-api-v1", "fabric-data-generation-api-v1", "fabric-data-attachment-api-v1",
        "fabric-client-gametest-api-v1", "fabric-renderer-indigo", "fabric-model-loading-api-v1",
        "fabric-particles-v1", "fabric-rendering-data-attachment-v1", "fabric-tag-api-v1",
    }

    kept: set[str] = set()
    for key, module in modules.items():
        # A module is kept when it provides a package the mods call, or when they call something deeper in
        # its tree (`.../client/renderer/v1/mesh` implies the module that owns `.../client/renderer/v1`).
        if (module["id"] or "") in UNCOMPILABLE:
            continue
        for package in wanted:
            if any(package == provided or provided.startswith(package + "/") or package.startswith(provided + "/")
                   for provided in module["packages"]):
                kept.add(key)
                break

    # then whatever the kept modules themselves need
    changed = True
    while changed:
        changed = False
        for key in list(kept):
            for dependency in modules[key]["depends"]:
                target = next((k for k, module in modules.items() if module["id"] == dependency
                               and (module["id"] or "") not in UNCOMPILABLE), None)
                if target and target not in kept:
                    kept.add(target)
                    changed = True

    written = []
    for key in sorted(kept):
        module = modules[key]
        target = out_dir / pathlib.Path(module["file"]).name
        target.write_bytes(module["data"])
        written.append(target)

    skipped = [module["id"] or key for key, module in modules.items() if key not in kept]
    print(f"  fabric api: {len(modules)} module(s) in the bundle, {len(written)} needed by this mod set")
    print(f"  kept: {', '.join(sorted(modules[key]['id'] or key for key in kept))}")
    print(f"  left out (not called, and a browser cannot compile what is not called): {len(skipped)} module(s)")

    if not written:
        # Nothing matched: leave the input alone rather than deleting a jar and baking nothing.
        print("  ! no module matched: the bundle is left in place (this is a bug in the matcher, not a mod)")
        return 0
    if not args.keep_bundle:
        try:
            bundle.unlink()
            print(f"  removed {bundle.name}: the modules above stand in for it")
        except OSError as error:
            print(f"  could not remove {bundle.name}: {error}")
        # a *copy* of the bundle staged in the output folder goes too, by its exact name: a glob would
        # match the module jars just written (they all start with "fabric-api") and delete the result.
        staged_copy = out_dir / bundle.name
        if staged_copy.exists() and staged_copy.resolve() != bundle.resolve():
            try:
                staged_copy.unlink()
                print(f"  removed {staged_copy.name} (the bundle was staged twice)")
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
