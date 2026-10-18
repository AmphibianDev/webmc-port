#!/usr/bin/env python3
"""Prepare everything the real-JVM page needs for one version, from Mojang's own metadata.

    python3 tools/jvm_prepare.py --version 1.21.11 --out site/jvm/1.21.11

Four steps, and each one is a thing that had to exist before this route could be deployed rather than
run by hand on one machine:

  1. the client jar and its libraries, fetched from Mojang (libraries.minecraft.net and
     resources.download.minecraft.net send no CORS header, so a page cannot fetch them itself - they
     have to sit next to the page);
  2. the backport: 1.21.11 is Java 21 bytecode and the browser JVM stops at 17, so the client - and any
     library that is also Java 21, jtracy for this version - is lowered by tools/backport17.py;
  3. the mods: Fabric API and any other jar named on the command line, lowered by tools/backport_mods.py,
     because a mod for 1.21.11 is Java 21 too;
  4. the bundle: the page's file list, with a sha256 per file, so a failed launch says which file is
     wrong instead of failing inside the JVM.

Everything is cached under work/, which is git-ignored: the game is fetched, never committed.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERE = ROOT / "tools"
sys.path.insert(0, str(HERE))


def asm_classpath() -> str:
    """The ASM jars, from wherever they are: work/asm on a runner, the engine toolchain on a machine."""
    jars = []
    libraries = ("asm", "asm-tree", "asm-commons", "asm-analysis", "asm-util")
    from pathlib import Path as _Path
    for directory in (ROOT / "work" / "asm",
                      ROOT.parent / "webmc-engine" / "toolchain" / "m2" / "org" / "ow2" / "asm",
                      _Path.home() / ".m2" / "repository" / "org" / "ow2" / "asm"):
        if directory.is_dir():
            for name in libraries:
                jars.extend(str(path) for path in sorted(directory.glob(f"{name}/*/*.jar")))
                jars.extend(str(path) for path in sorted(directory.glob(f"{name}/*.jar")))
    return os.pathsep.join(dict.fromkeys(jars))


def fetch(url: str, destination: pathlib.Path, log=print, proxy: str | None = None) -> pathlib.Path:
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}) if proxy else urllib.request.ProxyHandler({})
    )
    with opener.open(url, timeout=120) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
    log(f"  fetched {destination.name} ({destination.stat().st_size // 1024} KB)")
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="1.21.11")
    parser.add_argument("--out", default=None, help="the bundle directory (default site/jvm/<version>)")
    parser.add_argument("--work", default=None, help="where downloads are cached (default work/client)")
    parser.add_argument("--mods", action="append", default=[], help="mod jars to backport into the bundle")
    parser.add_argument("--assets", action="store_true", default=True)
    parser.add_argument("--no-assets", dest="assets", action="store_false")
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--skip-backport", action="store_true", help="assume the jar is already Java 17")
    args = parser.parse_args(argv)

    version = args.version
    work = pathlib.Path(args.work or ROOT / "work" / "client")
    out = pathlib.Path(args.out or ROOT / "site" / "jvm" / version)
    work.mkdir(parents=True, exist_ok=True)

    # 1. Mojang's metadata, the client, and its libraries
    # Cache first, and the version document is the only thing actually needed: on a machine whose
    # resolver refuses piston-meta (this one does) the cached document is the difference between running
    # and not, and in CI it saves a request per build.
    document_path = work / f"{version}.json"
    if not document_path.is_file():
        manifest_path = work / "version_manifest.json"
        fetch("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json", manifest_path, proxy=args.proxy)
        manifest = json.loads(manifest_path.read_text())
        entry = next((item for item in manifest["versions"] if item["id"] == version), None)
        if entry is None:
            print(f"{version} is not in Mojang's version list")
            return 2
        fetch(entry["url"], document_path, proxy=args.proxy)
    document = json.loads(document_path.read_text())
    client_raw = fetch(document["downloads"]["client"]["url"], work / f"{version}.jar", proxy=args.proxy)

    libs = work / "libs" / version
    libs.mkdir(parents=True, exist_ok=True)
    wanted = []
    for library in document.get("libraries", []):
        downloads = library.get("downloads") or {}
        artifact = downloads.get("artifact")
        if not artifact:
            continue
        name = pathlib.PurePosixPath(artifact["path"]).name
        # natives jars are OS libraries: the browser has none, and CheerpJ needs none
        if "natives" in library.get("name", "") and "-natives-" in name:
            continue
        wanted.append(name)
        fetch(artifact["url"], libs / name, proxy=args.proxy)
    print(f"  {len(wanted)} librar(y/ies) for {version}")

    # 2. the backport: Java 21 -> Java 17, client and libraries alike
    client = work / f"{version}-j17.jar"
    if not args.skip_backport:
        subprocess.run([sys.executable, str(HERE / "backport17.py"), "--input", str(client_raw),
                        "--output", str(client)], check=True)
        backported = libs.parent / f"{version}-j17"
        for name in wanted:
            source = libs / name
            with source.open("rb") as handle:
                header = handle.read(8)
            if len(header) == 8 and header[:4] == b"PK\x03\x04":
                result = subprocess.run([sys.executable, str(HERE / "backport17.py"),
                                         "--input", str(source), "--output", str(backported / name)],
                                        capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"  ! {name} could not be backported: {result.stdout.strip()[-200:]}")

    # 2b. Main's crash-report call is redirected, so a startup failure prints itself: the report builder
    # NPEs under this JVM and takes the cause, the phase and the report file with it.
    traced = work / f"{version}-j17-traced.jar"
    asm = asm_classpath()
    java = os.environ.get("JAVA_HOME", "")
    javac = str(pathlib.Path(java) / "bin" / "javac") if java else "javac"
    classes = work / "trace-classes"
    classes.mkdir(parents=True, exist_ok=True)
    subprocess.run([javac, "--release", "17", "-nowarn", "-cp", asm, "-d", str(classes),
                    str(HERE / "backport" / "TraceMain.java")], check=False, capture_output=True)
    patched = subprocess.run([str(pathlib.Path(java) / "bin" / "java") if java else "java",
                              "-cp", f"{classes}{os.pathsep}{asm}", "TraceMain",
                              str(client), str(traced)], capture_output=True, text=True)
    print("  " + patched.stdout.strip()[:200])
    if traced.is_file():
        client = traced

    # 3. the mods, lowered the same way (their metadata and mixin configs included)
    mods_out = work / "mods-j17"
    if args.mods:
        mods_out.mkdir(parents=True, exist_ok=True)
        for mod in args.mods:
            subprocess.run([sys.executable, str(HERE / "backport_mods.py"), "--input", mod,
                            "--output", str(mods_out / pathlib.Path(mod).name)], check=True)

    # 4. the bundle the page reads
    command = [sys.executable, str(HERE / "jvm_bundle.py"), "--version", version, "--out", str(out),
               "--client", str(client if client.is_file() else client_raw), "--libs", str(libs)]
    if args.assets:
        command.append("--assets")
    if args.mods:
        command += ["--mods", str(mods_out)]
    if args.proxy:
        command += ["--proxy", args.proxy]
    command.append("--report")
    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
