#!/usr/bin/env python3
"""The Java 21 -> 17 backport, checked on bytecode that it has to get exactly right.

The tool rewrites call sites into a shim. When the matching is too loose it produces bytecode that still
loads but misbehaves at run time (that happened three times: a record accessor, Guava's Iterables.getFirst
and Comparator.reversed), so this test checks both directions: what must be rewritten, and what must be
left alone - and then executes the rewritten methods on a Java 17 JVM, which is the only honest proof.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
import apisurface  # noqa: E402

JDK17 = Path.home() / ".jdks" / "jdk-17.0.13+11" / "Contents" / "Home"
JAVA21 = Path.home() / ".jdks" / "jdk-21.0.12.1+1" / "Contents" / "Home"


def asm_jars():
    engine = ROOT.parent / "webmc-engine" / "toolchain" / "m2" / "org" / "ow2" / "asm"
    jars = sorted(str(path) for path in engine.rglob("asm-*.jar") if "sources" not in path.name)
    if not jars:
        raise SystemExit(f"no ASM jars under {engine}")
    return os.pathsep.join(jars)


def run(command, cwd=None):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(map(str, command))}\n{result.stdout}\n{result.stderr}")
    return result


def check(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"  ok  {message}")


def references(path):
    """Every member referenced by the classes in a jar, read from the constant pool."""
    found = set()
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".class"):
                continue
            data = archive.read(name)
            if data[:4] != b"\xca\xfe\xba\xbe":
                continue
            for _kind, owner, member, descriptor in apisurface.parse_refs(data):
                found.add(f"{owner}#{member}{descriptor}")
    return found


def main():
    if not JDK17.exists() or not JAVA21.exists():
        print(f"skip: needs JDK 17 ({JDK17}) and JDK 21 ({JAVA21})")
        return 0

    work = Path(tempfile.mkdtemp(prefix="backport-test-"))
    keep = os.environ.get("KEEP")
    if keep:
        print("work dir:", work)
    try:
        # 1. build the fixture jar with JDK 21 and the project's ASM
        sources = ROOT / "tests" / "fixtures"
        run([str(JAVA21 / "bin" / "javac"), "-cp", asm_jars(), "-d", str(work), str(sources / "MakeBackportFixture.java")])
        jar = work / "fixture.jar"
        run([str(JAVA21 / "bin" / "java"), "-cp", f"{work}{os.pathsep}{asm_jars()}", "MakeBackportFixture", str(jar)])
        check(jar.exists(), "the fixture jar was built")

        before = references(jar)
        for wanted in ("java/util/List#getFirst()Ljava/lang/Object;", "java/util/List#reversed()Ljava/util/List;",
                       "java/lang/Math#clamp(JJJ)J", "java/lang/Thread#sleep(Ljava/time/Duration;)V"):
            check(any(wanted in reference for reference in before), f"the fixture really uses {wanted}")
        check("java.util.SequencedMap" in run([str(JDK17 / "bin" / "javap"), "-p", "-cp", str(jar),
                                               "fixture.Sample"]).stdout,
              "the fixture really has a SequencedMap field")

        # 2. backport it
        output = work / "fixture-j17.jar"
        run([sys.executable, str(ROOT / "tools" / "backport17.py"), "--input", str(jar), "--output", str(output)])
        check(output.exists(), "the backported jar was written")

        # 3. what the code *does* is what matters, so this reads the disassembly: the constant pool of a
        # rewritten class still carries the old entries (ClassWriter copies the reader's pool, which is how
        # the stack map frames are preserved), and only the instructions show whether a call was replaced
        with zipfile.ZipFile(output) as archive:
            (work / "fixture").mkdir(exist_ok=True)
            for name in ("fixture/Sample.class", "fixture/Trap.class"):
                (work / name.replace("/", "_")).write_bytes(archive.read(name))
        disassembly = run([str(JDK17 / "bin" / "javap"), "-c", "-p", "-cp", str(output), "fixture.Sample"]).stdout

        for gone in ("java/util/List.getFirst", "java/util/List.reversed", "java/lang/Math.clamp",
                     "java/lang/Thread.sleep:(Ljava/time/Duration;)V"):
            check(gone not in disassembly, f"{gone} was rewritten away")
        check("webmc/j21/J21.listGetFirst:(Ljava/util/List;)Ljava/lang/Object;" in disassembly,
              "getFirst() calls the shim")
        check("webmc/j21/J21.listReversed:(Ljava/util/List;)Ljava/util/List;" in disassembly,
              "reversed() calls the shim")
        check("webmc/j21/J21.clamp:(JJJ)J" in disassembly, "Math.clamp() calls the shim")
        check("webmc/j21/J21.sleepDuration:(Ljava/time/Duration;)V" in disassembly,
              "Thread.sleep(Duration) calls the shim")
        check("fixture/Trap.getFirst:(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;" in disassembly,
              "a two-argument getFirst on a class that is not a List was left alone")
        check("fixture/Trap.reversed:()Lfixture/Trap;" in disassembly,
              "reversed() on a class that is not a List was left alone")
        check("java/lang/Thread.sleep:(J)V" in disassembly, "Thread.sleep(long) was left alone")
        # the declaration, not the raw bytes: the copied constant pool keeps the old type name as an unused
        # string, so scanning for it would report a false alarm
        declarations = run([str(JDK17 / "bin" / "javap"), "-p", "-cp", str(output), "fixture.Sample"]).stdout
        check("public static java.util.Map names;" in declarations, "the SequencedMap field is a Map now")

        after = references(output)
        check(any("webmc/j21/J21#listGetFirst" in reference for reference in after), "the calls go to the shim")

        with zipfile.ZipFile(output) as archive:
            versions = {int.from_bytes(archive.read(n)[6:8], "big")
                        for n in archive.namelist() if n.endswith(".class")}
            check(versions == {61}, f"every class is version 61 (found {sorted(versions)})")
            check("webmc/j21/J21.class" in archive.namelist(), "the shim travelled inside the jar")

        # 4. and it runs: the rewritten methods are called for real on a Java 17 JVM
        probe = work / "Probe.java"
        probe.write_text('''
import java.util.*;
public class Probe {
    public static void main(String[] args) throws Exception {
        List<String> names = new ArrayList<>(List.of("ada", "grace", "katherine"));
        System.out.println("first=" + fixture.Sample.first(names));
        System.out.println("reversed=" + fixture.Sample.lastReversed(names));
        System.out.println("clamped=" + fixture.Sample.clampIt(99L));
        fixture.Sample.sleepDuration(java.time.Duration.ZERO);
        System.out.println("typed=" + fixture.Sample.names);
    }
}
''')
        run([str(JDK17 / "bin" / "javac"), "-cp", str(output), "-d", str(work), str(probe)])
        result = run([str(JDK17 / "bin" / "java"), "-cp", f"{work}{os.pathsep}{output}", "Probe"])
        lines = result.stdout.strip().splitlines()
        check(lines[0] == "first=ada", "the rewritten getFirst() returns the first element")
        check(lines[1] == "reversed=[katherine, grace, ada]", "the rewritten reversed() returns a reversed list")
        check(lines[2] == "clamped=5", "the rewritten Math.clamp() clamps")
        print("all backport checks passed")
        return 0
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
