#!/usr/bin/env python3
"""Make the client and its mod set compile for a browser: point platform APIs the compiler lacks at stubs.

    python3 tools/platform_shims.py --classlib <teavm-classlib.jar> --client <client-named.jar> \
        --mods-dir /tmp/mods --asm <asm jars> --out <dir> [--report <json>]

Why this exists: a browser compiler implements a subset of java.base. The vanilla client happens to stay
inside that subset, but a real mod set does not - Meteor's bundled Discord IPC opens a SocketChannel and
reads a RuntimeMXBean, MinecraftAuth does the same for accounts - and a whole-program compile stops at the
first reference it cannot resolve. That is not a mod being broken: it is the platform being absent.

So the missing classes are given the shape they are used with, in a package of our own (javac will not let
us define java.*), and every reference to them is renamed to match. The code stays; it becomes inert -
which is the honest outcome for a socket in a browser.

The list of what is missing is computed against the compiler's own class library, so this needs no guessing
and no build: `tools/remap/MissingRefs.java` answers it in one pass.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_loader  # noqa: E402 - the JDK finder that already knows about macOS stubs

# Classes whose absence is *expected* to be fixable by stubbing. Everything missing is stubbed, but a
# package listed here is reported as "this is a browser limitation" rather than "this is a surprise".
PLATFORM_PACKAGES = ("java/lang/management/", "java/nio/channels/", "java/net/", "javax/", "jdk/",
                     "java/awt/", "java/time/", "java/security/", "java/nio/file/")

TARGET_PACKAGE = "webmc/platform"


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


def compile_tools(javac: str, asm: str, work: pathlib.Path, log) -> pathlib.Path:
    classes = work / "tools"
    classes.mkdir(parents=True, exist_ok=True)
    sources = ["tools/remap/MissingRefs.java", "tools/remap/ModRemapper.java", "tools/ScanRefs.java"]
    result = run([javac, "-nowarn", "-cp", asm, "-d", str(classes)] + [str(ROOT / source) for source in sources])
    if result.returncode != 0:
        log(result.stderr[-1500:])
        raise SystemExit("could not compile the platform tools")
    return classes


def missing_classes(java: str, tools: pathlib.Path, asm: str, classlib: pathlib.Path, scan: list,
                    log) -> tuple:
    result = run([java, "-cp", f"{tools}{os.pathsep}{asm}", "MissingRefs", "--classlib", str(classlib),
                  "--scan", *[str(path) for path in scan]])
    if result.returncode != 0:
        log(result.stderr[-1500:])
        raise SystemExit("could not compute the missing references")
    classes, methods, fields = [], [], []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "class" and len(parts) > 1:
            classes.append(parts[1])
        elif parts[0] == "method" and len(parts) > 1:
            methods.append(parts[1])
        elif parts[0] == "field" and len(parts) > 1:
            fields.append(parts[1])
    summary = [line for line in result.stdout.splitlines() if line.startswith("# checked")]
    log("  " + (summary[-1] if summary else "no summary"))
    return classes, methods, fields


def target_names(missing: list) -> dict:
    """One target name per missing class, in our own package, with collisions resolved."""
    names = {}
    used = {}
    for name in missing:
        package, _, simple = name.rpartition("/")
        candidate = simple
        if candidate in used:
            # two packages each with a Foo: keep them apart by package, still under our own namespace
            candidate = package.replace("/", "_") + "_" + simple
        used[candidate] = name
        names[name] = f"{TARGET_PACKAGE}/{candidate}"
    return names


def write_table(work: pathlib.Path, names: dict) -> pathlib.Path:
    prefix = work / "platform-rename"
    prefix.write_text("".join(f"{was}\t{now}\n" for was, now in sorted(names.items())))
    prefix.with_suffix(".fields.tsv").write_text("")
    prefix.with_suffix(".methods.tsv").write_text("")
    return prefix


def rename(java: str, tools: pathlib.Path, asm: str, table: pathlib.Path, jar: pathlib.Path,
           work: pathlib.Path, log) -> bool:
    """Rewrite references to the missing classes in one jar, in place."""
    output = work / "renamed" / jar.name
    output.parent.mkdir(parents=True, exist_ok=True)
    result = run([java, "-cp", f"{tools}{os.pathsep}{asm}", "ModRemapper", str(jar), str(output),
                  str(table)])
    if result.returncode != 0 or not output.is_file():
        log(f"  ! {jar.name}: could not be renamed ({result.stderr.strip()[:140]})")
        return False
    summary = [line for line in result.stdout.splitlines() if line.startswith("remapped ")]
    if summary:
        log("  " + summary[-1].replace(str(output), jar.name))
    shutil.copyfile(output, jar)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--classlib", required=True, help="the compiler's class library jar")
    parser.add_argument("--client", default=None, help="the client jar to rewrite in place")
    parser.add_argument("--mods-dir", default=None, help="where the mod jars are, and where the stubs land")
    parser.add_argument("--asm", required=True, help="classpath of ASM jars")
    parser.add_argument("--out", required=True, help="directory for the generated stub jar")
    parser.add_argument("--report", default=None, help="write a JSON report of what was stubbed")
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)

    log = print
    javac = build_loader.find_javac()
    if not javac:
        log("no javac: cannot compile the platform tools")
        return 2
    java = str(pathlib.Path(javac).parent / "java")
    classlib = pathlib.Path(args.classlib)
    if not classlib.is_file():
        log(f"! the class library is missing: {classlib}")
        return 2

    scan = []
    if args.client:
        scan.append(pathlib.Path(args.client))
    if args.mods_dir:
        scan.extend(sorted(pathlib.Path(args.mods_dir).glob("*.jar")))
    if not scan:
        log("nothing to scan: pass --client or --mods-dir")
        return 2

    with tempfile.TemporaryDirectory(prefix="webmc-platform-") as tmp:
        work = pathlib.Path(tmp)
        tools = compile_tools(javac, args.asm, work, log)

        # 1. what does the compiler lack that these jars reference?
        missing, methods, fields = missing_classes(java, tools, args.asm, classlib, scan, log)
        if not missing:
            log("  nothing platform-shaped is missing: no stubs needed")
            return 0
        by_package = {}
        for name in missing:
            by_package.setdefault(name.rpartition("/")[0], []).append(name)
        log(f"  {len(missing)} class(es) the compiler does not have, from {len(by_package)} package(s):")
        for package, names in sorted(by_package.items(), key=lambda item: -len(item[1]))[:8]:
            log(f"    {len(names):3d}  {package}")

        # 2. shapes for them, generated from how they are used, emitted under our own package
        names = target_names(missing)
        stubs = pathlib.Path(args.out) / "webmc-platform-stubs.jar"
        stubs.parent.mkdir(parents=True, exist_ok=True)
        moves = [f"{source}:{target}" for source, target in names.items()]
        command = [sys.executable, str(ROOT / "tools" / "build_optional_stubs.py"),
                   "--asm", args.asm, "--out", str(stubs),
                   "--client", str(args.client or scan[0]), "--source-out", str(work / "sources")]
        for path in scan:
            command += ["--scan", str(path)]
        for source in missing:
            command += ["--class", source]
        for move in moves:
            command += ["--move", move]
        result = run(command)
        if result.returncode != 0 or not stubs.is_file():
            log(result.stdout[-2000:])
            log(result.stderr[-2000:])
            log("! the platform stubs could not be built")
            return 1
        for line in result.stdout.strip().splitlines()[-3:]:
            log("  " + line)

        # 3. rename every reference to them, in the client and in the mods
        table = write_table(work, names)
        renamed = 0
        for jar in scan:
            if rename(java, tools, args.asm, table, jar, work, log):
                renamed += 1
        log(f"  renamed references in {renamed} jar(s)")

        if args.mods_dir:
            shutil.copyfile(stubs, pathlib.Path(args.mods_dir) / stubs.name)
            log(f"  stub jar shipped as {stubs.name}")

        names_path = ROOT / "tools" / "platform-shims.json"
        names_path.write_text(json.dumps({"stubbed": names, "methods": methods, "fields": fields},
                                         indent=1, sort_keys=True) + "\n")
        if args.report:
            shutil.copyfile(names_path, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
