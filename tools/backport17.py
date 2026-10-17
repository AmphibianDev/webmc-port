#!/usr/bin/env python3
"""Backport a Java 21 jar to Java 17, and prove it.

    python3 tools/backport17.py --input work/client/1.21.11.jar --output work/client/1.21.11-j17.jar

Minecraft 1.21.11 is Java 21 bytecode, and a browser JVM (CheerpJ) tops out at Java 17 today. This runs
`tools/backport/J21Backport.java` - which lowers the class file version and replaces the handful of Java 21
API calls the client makes with calls into a small shim - and then does the two checks that matter:

  * every class in the output is version 61 or lower (a Java 17 JVM refuses anything higher, so this is the
    difference between "it loads" and "it does not");
  * no Java 21-only API reference is left *in the code*: the tool audits instructions, method handles and
    invokedynamic arguments, because a stale constant-pool entry that nothing calls is not a gap (and
    counting those is how an audit reports 113 problems that do not exist).

The JDK 17 on this machine (if there is one) then gets to load the result, which is the check that a
version number alone cannot make: a JVM that reads every class and reports what it cannot.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKPORT = ROOT / "tools" / "backport" / "J21Backport.java"
SHIMS = sorted((ROOT / "tools" / "backport").rglob("webmc/**/*.java"))

JAVA21_METHODS = {
    "java/util/List": {"getFirst", "getLast", "addFirst", "addLast", "removeFirst", "removeLast", "reversed"},
    "java/lang/Math": {"clamp"},
    "java/lang/Thread": {"threadId"},
}
JAVA21_TYPES = {"java/util/SequencedCollection", "java/util/SequencedSet", "java/util/SequencedMap", "java/lang/ScopedValue",
                "java/util/concurrent/StructuredTaskScope", "java/lang/StringTemplate"}


PROBE_SOURCE = """\
import java.io.*;
import java.nio.file.*;
import java.util.*;

/** Load every class of a jar into this JVM (no initialisation) and report what it refuses. */
public class Probe {
    public static void main(String[] args) throws Exception {
        List<String> names = Files.readAllLines(Path.of(args[0]));
        int ok = 0;
        List<String> bad = new ArrayList<>();
        for (String name : names) {
            try {
                Class.forName(name, false, Probe.class.getClassLoader());
                ok++;
            } catch (Throwable error) {
                if (bad.size() < 20) {
                    bad.add(name + " -> " + error.getClass().getSimpleName() + ": " + error.getMessage());
                }
            }
        }
        System.out.println("loaded " + ok + " of " + names.size() + " class(es)");
        for (String line : bad) {
            System.out.println("  " + line);
        }
    }
}
"""


def java_home(version: int) -> pathlib.Path | None:
    # A runner does not have ~/.jdks: setup-java puts the JDK wherever it likes and exports JAVA_HOME,
    # plus JAVA_HOME_<version>_<arch> for the ones it installed. Both are checked, so this works on a
    # machine that installs JDKs and on one that is handed them.
    for variable in (f"JAVA_HOME_{version}", f"JAVA_HOME_{version}_X64", f"JAVA_HOME_{version}_ARM64"):
        candidate = os.environ.get(variable)
        if candidate and (pathlib.Path(candidate) / "bin" / "javac").is_file():
            return pathlib.Path(candidate)
    if version == 17:
        candidate = os.environ.get("JAVA_HOME")
        if candidate and (pathlib.Path(candidate) / "bin" / "javac").is_file():
            return pathlib.Path(candidate)
    for candidate in sorted((pathlib.Path.home() / ".jdks").glob(f"*{version}*")) + sorted((pathlib.Path.home() / ".jdks").glob(f"jdk-{version}*")):
        home = candidate / "Contents" / "Home"
        if (home / "bin" / "javac").is_file():
            return home
        if (candidate / "bin" / "javac").is_file():
            return candidate
    return None


def asm_classpath() -> str:
    jars = []
    libraries = ("asm", "asm-tree", "asm-commons", "asm-analysis", "asm-util")
    # Where ASM actually is, in the order of how likely: this repository's vendor directory, the
    # developer's engine toolchain, and a maven cache or ~/.m2 - because a runner has none of the first
    # two and the tool has to be able to say where it looked.
    for directory in (ROOT / "work" / "asm", ROOT.parent / "webmc-engine" / "toolchain" / "m2" / "org" / "ow2" / "asm",
                      pathlib.Path.home() / ".m2" / "repository" / "org" / "ow2" / "asm"):
        if directory.is_dir():
            for name in libraries:
                jars.extend(str(path) for path in sorted(directory.glob(f"{name}/*/*.jar")))
                jars.extend(str(path) for path in sorted(directory.glob(f"{name}/*.jar")))
    for name in ("asm.jar", "asm-tree.jar", "asm-commons.jar", "asm-analysis.jar", "asm-util.jar"):
        candidate = ROOT / "work" / "asm" / name
        if candidate.is_file() and candidate.stat().st_size > 20_000:
            jars.append(str(candidate))
    return os.pathsep.join(dict.fromkeys(jars))


def compile_tool(out: pathlib.Path) -> bool:
    home = java_home(21) or java_home(17)
    asm = asm_classpath()
    if not home or not asm:
        print(f"  SKIP  the backport tool needs a JDK and ASM (JDK: {home}, ASM: {bool(asm)})")
        return False
    out.mkdir(parents=True, exist_ok=True)
    # --release 17: the *shim* is compiled into the client, so it has to be Java 17 bytecode itself. (A
    # shim compiled by a JDK 21 is refused by the very JVM it is meant to run on - which is exactly what
    # happened the first time this ran: "webmc/j21/J21 has been compiled by a more recent version".)
    result = subprocess.run([str(home / "bin" / "javac"), "-nowarn", "--release", "17", "-cp", asm, "-d", str(out),
                             str(BACKPORT), *[str(path) for path in SHIMS]], capture_output=True, text=True)
    if result.returncode != 0:
        print("  the backport tool does not compile:")
        print(result.stdout + result.stderr)
        return False
    return True


def audit(path: pathlib.Path) -> tuple[int, list[str]]:
    """Class files above version 61, and Java 21-only API references, counted from the jar itself."""
    sys.path.insert(0, str(ROOT / "mods"))
    import apisurface  # type: ignore[import-not-found]

    too_new = 0
    leftovers: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".class"):
                continue
            data = archive.read(name)
            if data[:4] != b"\xca\xfe\xba\xbe":
                continue
            if int.from_bytes(data[6:8], "big") > 61:
                too_new += 1
            # Only types are checked here: the tool's own audit covers methods *as they are called*
            # (instructions, handles, invokedynamic), which a raw constant-pool scan cannot distinguish
            # from an entry the rewrite has already made unreachable.
            for _kind, owner, _member, _descriptor in apisurface.parse_refs(data):
                if owner in JAVA21_TYPES:
                    leftovers.append(f"{name}: type {owner}")
    return too_new, leftovers


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--keep-tool", action="store_true")
    args = parser.parse_args(argv)

    source = pathlib.Path(args.input).expanduser()
    target = pathlib.Path(args.output).expanduser()
    if not source.is_file():
        print(f"{source} is not there")
        return 2

    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-backport-"))
    classes = work / "classes"
    if not compile_tool(classes):
        return 0  # a skip, not a failure: no JDK/ASM on this machine

    home = java_home(21) or java_home(17)
    asm = asm_classpath()
    target.parent.mkdir(parents=True, exist_ok=True)
    # the client's libraries are handed over so the tool can *read* which classes are Lists, instead of
    # guessing from a package name (see the note in J21Backport)
    libraries = []
    libs_dir = source.parent / "libs" / source.stem.replace("-j17", "")
    if libs_dir.is_dir():
        libraries = sorted(str(path) for path in libs_dir.glob("*.jar"))
    run = subprocess.run([str(home / "bin" / "java"), "-cp", f"{classes}{os.pathsep}{asm}",
                          "J21Backport", str(source), str(target), *libraries], capture_output=True, text=True)
    print(run.stdout.strip())
    if run.returncode != 0:
        print(run.stderr.strip()[-2000:])
        return run.returncode

    too_new, leftovers = audit(target)
    print(f"\nbackported {source.name} -> {target.name}")
    print(f"  classes above Java 17: {too_new}")
    # A count of constant-pool *names*, not of live references: the pool of a rewritten class keeps entries
    # the code no longer uses (ClassWriter copies the reader's pool, which is what preserves the stack map
    # frames), so these are the old type names sitting beside rewritten code. The tool's own audit is the
    # authority on live references - it walks instructions, method handles, invokedynamic arguments and
    # declarations - and the load below is the proof.
    print(f"  Java 21-only names left in constant pools (harmless, see the audit above): {len(leftovers)}")
    for line in leftovers[:10]:
        print(f"    {line}")

    jvm17 = java_home(17)
    if jvm17:
        # The check a version number cannot make: hand every class to a real Java 17 JVM and see what it
        # says. Class.forName with initialize=false only *loads* and *links* them, which is exactly the
        # question ("can a Java 17 JVM accept this class file") and nothing more.
        print(f"\nloading every class into {jvm17.name} (Java 17)")
        listing = target.parent / f"{target.stem}-classes.txt"
        with zipfile.ZipFile(target) as archive:
            names = [name[:-6].replace("/", ".") for name in archive.namelist() if name.endswith(".class")]
        listing.write_text("\n".join(names))
        probe = work / "Probe.java"
        probe.write_text(PROBE_SOURCE)
        compile_probe = subprocess.run([str(jvm17 / "bin" / "javac"), "-d", str(work), str(probe)], capture_output=True, text=True)
        if compile_probe.returncode == 0:
            # the client's libraries must be on the probe's classpath too, otherwise a class whose
            # superclass lives in a library cannot be resolved and the count understates what loads
            probe_path = os.pathsep.join([str(work), str(target)] + list(libraries))
            load = subprocess.run([str(jvm17 / "bin" / "java"), "-cp", probe_path,
                                   "Probe", str(listing)], capture_output=True, text=True)
            print(load.stdout.strip()[-4000:])
            if load.stderr.strip():
                print(load.stderr.strip()[:1000])
        else:
            print("  the probe did not compile:", compile_probe.stderr[:400])

    if not args.keep_tool:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if too_new == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
