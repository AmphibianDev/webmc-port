#!/usr/bin/env python3
"""Build WebMC's own loader: webmc.loader.WebMcLoader, from mods/loader-jvm.

    python3 tools/build_loader.py                     # build site/loader/webmc-loader.jar
    python3 tools/build_loader.py --print-hash         # just the sha256 of what would be built
    python3 tools/build_loader.py --javac /path/to/javac

The jar is the one piece of this project that is *code we wrote* and that the page loads before the
game, so it is built to be verifiable rather than merely present:

* **Java 8 bytecode.** CheerpJ runs 8, 11 and 17; a jar targeted at 8 loads in all three, and a jar
  targeted at 17 is a class file the Java 8 runtime refuses. One jar, every version the launcher can
  start.
* **Reproducible.** Entries are sorted and stamped with a fixed date, so the same source produces the
  same bytes and therefore the same sha256 - which is what lets site/launch.json pin the hash of the
  exact jar the page is about to run.
* **No dependencies.** Nothing here is downloaded to build it, and the jar is a few kilobytes.

Without a JDK the build is skipped with instructions instead of failing: every other test in this
repository runs without one, and the launcher degrades to starting the game class directly.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCE = ROOT / "mods" / "loader-jvm" / "src"
DEFAULT_OUT = ROOT / "site" / "loader" / "webmc-loader.jar"
MAIN_CLASS = "webmc.loader.WebMcLoader"

# The fixed timestamp that makes the jar reproducible. Nothing about a zip requires the build date to
# be the date: 1980-01-01 is the epoch of the DOS format every zip reader understands.
STAMP = (1980, 1, 1, 0, 0, 0)


def find_javac(explicit: str | None = None) -> str | None:
    """javac from the flag, the environment, the PATH, or the usual places - in that order."""
    if explicit:
        return explicit if pathlib.Path(explicit).is_file() else None
    if os.environ.get("JAVA_HOME"):
        candidate = pathlib.Path(os.environ["JAVA_HOME"]) / "bin" / "javac"
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("javac")
    if found:
        # macOS ships a /usr/bin/javac stub that exists but cannot compile; running it is the only way
        # to tell, and it is checked where it matters (in build()).
        return found
    for pattern in ("/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/javac",
                    "/opt/homebrew/opt/openjdk*/bin/javac",
                    "/usr/lib/jvm/*/bin/javac",
                    str(pathlib.Path.home() / ".jdks" / "*" / "bin" / "javac")):
        for candidate in sorted(pathlib.Path("/").glob(pattern.lstrip("/"))):
            if candidate.is_file():
                return str(candidate)
    return None


def sources() -> list[pathlib.Path]:
    return sorted(SOURCE.rglob("*.java"))


def compile(javac: str, release: str, classes: pathlib.Path, files: list[pathlib.Path], log=print) -> str:
    """Compile at the requested release, falling back through newer ones and saying so.

    Returning a *newer* class file version is allowed but reported: a jar targeted above Java 8 still
    runs in CheerpJ's Java 17 mode, and does not run at all in its Java 8 mode, which is the mode the
    LWJGL 2 clients need.
    """
    attempts = [release] + [candidate for candidate in ("8", "11", "17") if candidate != release] + [None]
    for candidate in attempts:
        command = [javac]
        if candidate:
            command += ["--release", candidate]
        command += ["-nowarn", "-d", str(classes)] + [str(path) for path in files]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            if candidate != release:
                log(f"  ! this JDK will not target Java {release}: compiled at "
                    f"{candidate or 'its own default'} instead, which will not load in Java 8 mode")
            return candidate or "default"
    raise SystemExit("javac failed:\n" + (result.stderr or result.stdout))


def build(javac: str, out: pathlib.Path, release: str = "8", log=print) -> dict:
    """Compile and package. Returns {jar, entries, bytes, sha256, release}."""
    with tempfile.TemporaryDirectory() as scratch:
        classes = pathlib.Path(scratch) / "classes"
        classes.mkdir(parents=True)
        files = sources()
        if not files:
            raise SystemExit(f"no sources under {SOURCE}")
        used = compile(javac, release, classes, files, log=log)

        out.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as jar:
            manifest = ("Manifest-Version: 1.0\r\n"
                        f"Main-Class: {MAIN_CLASS}\r\n"
                        "Implementation-Title: WebMC loader\r\n"
                        "Implementation-Version: 1\r\n"
                        "Created-By: tools/build_loader.py\r\n\r\n")
            info = zipfile.ZipInfo("META-INF/MANIFEST.MF", STAMP)
            jar.writestr(info, manifest)
            for path in sorted(classes.rglob("*.class")):
                name = str(path.relative_to(classes)).replace(os.sep, "/")
                info = zipfile.ZipInfo(name, STAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                jar.writestr(info, path.read_bytes())
                entries.append(name)
    data = out.read_bytes()
    major = int.from_bytes(_first_class(out)[6:8], "big") if _first_class(out) else 0
    return {"jar": str(out), "entries": entries, "bytes": len(data), "release": used,
            "bytecode": major, "java": (major - 44) if major >= 45 else None,
            "sha256": hashlib.sha256(data).hexdigest()}


def _first_class(jar: pathlib.Path) -> bytes:
    """The bytes of one of our class files, for the bytecode level check the test makes."""
    try:
        with zipfile.ZipFile(jar) as archive:
            return archive.read(MAIN_CLASS.replace(".", "/") + ".class")
    except (KeyError, zipfile.BadZipFile):
        return b""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--javac", default=None)
    parser.add_argument("--release", default="8")
    parser.add_argument("--print-hash", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    log = (lambda *a: None) if args.quiet else print
    javac = find_javac(args.javac)
    if not javac:
        print("no javac: the loader jar was not built. Install a JDK (any version) and re-run, or let "
              "the catalog workflow build it - the launcher starts the game class directly without it.")
        return 3

    result = build(javac, pathlib.Path(args.out), args.release, log=log)
    if args.print_hash:
        print(result["sha256"])
        return 0
    print(f"built {pathlib.Path(result['jar']).relative_to(ROOT)} "
          f"({result['bytes']} bytes, {len(result['entries'])} classes, "
          f"Java {result['java']} bytecode, sha256 {result['sha256'][:16]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
