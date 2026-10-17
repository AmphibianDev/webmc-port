#!/usr/bin/env python3
"""Compile the example mods in mods/examples/ into mods/jars/.

    python3 tools/build_mods.py                 # every example, against the built loader jar
    python3 tools/build_mods.py --only hello-webmc

An example mod is source, not a binary: mods/jars/ is git-ignored, so the only way the launcher can
always offer at least one mod is to build one here. These are deliberately game-independent - they use
the loader's API and nothing else - so building one needs a JDK and no Minecraft jar at all, which
means CI can do it without ever fetching the game.

A real mod is a jar somebody else built: drop it into mods/jars/ and describe it in mods/mods.json.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import build_loader  # noqa: E402

EXAMPLES = ROOT / "mods" / "examples"
JARS = ROOT / "mods" / "jars"
STAMP = build_loader.STAMP


def build_example(name: str, javac: str, loader_jar: pathlib.Path, out: pathlib.Path, log=print) -> dict | None:
    """Compile one example and pack it with its descriptor at the jar root."""
    folder = EXAMPLES / name
    sources = sorted((folder / "src").rglob("*.java"))
    if not sources:
        log(f"  {name}: no sources")
        return None
    with tempfile.TemporaryDirectory() as scratch:
        classes = pathlib.Path(scratch) / "classes"
        classes.mkdir(parents=True)
        # a mod may use the loader's API and/or the Fabric API shims the bake provides
        # the loader's API, the Fabric shims the bake provides, and the mixin annotations this repository
        # stubs (MIXINS.md): a mod that uses none of them needs none of them
        classpath = os.pathsep.join([str(loader_jar) if loader_jar.is_file() else str(build_loader.SOURCE),
                                     str(ROOT / "mods" / "shims"), str(ROOT / "mods" / "mixin-shims")])
        command = [javac, "--release", "8", "-nowarn", "-cp", classpath,
                   "-d", str(classes)] + [str(source) for source in sources]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0 and "--release" in result.stderr:
            command = [javac, "-nowarn", "-cp", classpath, "-d", str(classes)] + \
                [str(source) for source in sources]
            result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            log(f"  {name}: javac failed: {(result.stderr or result.stdout).strip()[:400]}")
            return None

        out.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as jar:
            for path in sorted(classes.rglob("*.class")):
                relative = str(path.relative_to(classes)).replace("/", "/")
                if relative.startswith("org/spongepowered/asm/"):
                    # javac found the annotation stubs on the classpath and compiled them along the way. They
                    # belong to the client they are merged into, not to this jar: tools/build_mixin_shims.py
                    # ships the two that are real code, and the annotations are read out of the class file.
                    continue
                info = zipfile.ZipInfo(relative, STAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                jar.writestr(info, path.read_bytes())
                entries.append(relative)
            # The descriptor has to be at the root: that is where the loader looks for it.
            for resource in sorted((folder / "resources").glob("*")):
                info = zipfile.ZipInfo(resource.name, STAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                jar.writestr(info, resource.read_bytes())
                entries.append(resource.name)
    log(f"  {name}: {out.name} ({len(entries)} entries)")
    return {"name": name, "jar": str(out), "entries": entries}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=None)
    parser.add_argument("--loader-jar", default=str(build_loader.DEFAULT_OUT))
    parser.add_argument("--javac", default=None)
    args = parser.parse_args(argv)

    javac = build_loader.find_javac(args.javac)
    if not javac:
        print("no javac: example mods were not built. The manifest still lists them and the launcher "
              "reports them as missing rather than failing.")
        return 3

    names = [args.only] if args.only else sorted(path.name for path in EXAMPLES.iterdir() if path.is_dir())
    built = 0
    for name in names:
        if build_example(name, javac, pathlib.Path(args.loader_jar), JARS / f"{name}.jar") is not None:
            built += 1
    print(f"built {built} of {len(names)} example mod(s) into {JARS.relative_to(ROOT)}/")
    return 0 if built else 1


if __name__ == "__main__":
    sys.exit(main())
