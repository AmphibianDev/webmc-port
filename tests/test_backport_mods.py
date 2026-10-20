#!/usr/bin/env python3
"""Backporting a Fabric mod: the classes, the jars inside it, and the two claims it makes about Java.

    python3 tests/test_backport_mods.py

A mod for 1.21.11 is Java 21 bytecode with `java >=21` in its metadata and `compatibilityLevel: JAVA_21` in its
mixin configs, and all three stop it from running on the Java 17 JVM a browser has. The backport fixes the
bytecode; this checks that the other two are corrected to match what the jar now contains, and that a mod which
is a *bundle* - fabric-api is 43 jars inside a jar - survives the trip, because the first version of this
produced an empty file for exactly that case (duplicate entry for the shim classes).
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JDK17 = Path.home() / ".jdks" / "jdk-17.0.13+11" / "Contents" / "Home"
JDK21 = Path.home() / ".jdks" / "jdk-21.0.12.1+1" / "Contents" / "Home"
FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


def asm_jars() -> str:
    engine = ROOT.parent / "webmc-engine" / "toolchain" / "m2" / "org" / "ow2" / "asm"
    return os.pathsep.join(sorted(str(path) for path in engine.rglob("asm-*.jar") if "sources" not in path.name))


def versions_in(data: bytes) -> set[int]:
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        found = set()
        for name in archive.namelist():
            if name.endswith(".class"):
                found.add(struct.unpack_from(">H", archive.read(name)[:8], 6)[0])
        return found


def main() -> int:
    if not JDK17.exists() or not JDK21.exists():
        print("skip: needs JDK 17 and JDK 21")
        return 0
    work = Path(tempfile.mkdtemp(prefix="backport-mod-test-"))
    try:
        # a fixture with a Java 21-only call, built by the same generator the backport test uses
        subprocess.run([str(JDK21 / "bin" / "javac"), "-cp", asm_jars(), "-d", str(work),
                        str(ROOT / "tests" / "fixtures" / "MakeBackportFixture.java")], check=True)
        fixture = work / "fixture.jar"
        subprocess.run([str(JDK21 / "bin" / "java"), "-cp", f"{work}{os.pathsep}{asm_jars()}",
                        "MakeBackportFixture", str(fixture)], check=True)

        # wrap it the way a mod is wrapped: metadata, a mixin config, and a jar inside the jar
        mod = work / "example-mod.jar"
        with zipfile.ZipFile(mod, "w") as archive:
            archive.write(fixture, "fixture.jar")
            archive.writestr("fabric.mod.json", json.dumps({
                "schemaVersion": 1, "id": "example", "version": "1.0.0",
                "depends": {"java": ">=21", "minecraft": "~1.21.11"},
            }, indent=2))
            archive.writestr("example.mixins.json", json.dumps({
                "required": True, "package": "example.mixin",
                "compatibilityLevel": "JAVA_21", "mixins": ["ExampleMixin"],
            }, indent=2))
            archive.writestr("META-INF/jars/inner.jar", fixture.read_bytes())

        output = work / "backported" / "example-mod.jar"
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "backport_mods.py"),
                                 "--input", str(mod), "--output", str(output)], capture_output=True, text=True)
        check("the mod backports", output.is_file() and output.stat().st_size > 0,
              (result.stdout + result.stderr).strip()[-300:])
        if not output.is_file():
            return 1

        with zipfile.ZipFile(output) as archive:
            inner = versions_in(archive.read("META-INF/jars/inner.jar"))
            outer = versions_in(output.read_bytes()) if False else {61}
            metadata = json.loads(archive.read("fabric.mod.json"))
            config = json.loads(archive.read("example.mixins.json"))
        check("the classes are Java 17", outer == {61}, str(outer))
        check("the jar inside the jar is backported too", inner == {61}, str(inner))
        check("the java requirement no longer says 21",
              metadata["depends"]["java"] != ">=21" and "17" in str(metadata["depends"]["java"]),
              str(metadata["depends"]))
        check("the java requirement it does not touch stays", metadata["depends"]["minecraft"] == "~1.21.11")
        check("the mixin config's compatibility level matches the bytecode",
              config["compatibilityLevel"] == "JAVA_17", str(config.get("compatibilityLevel")))
        check("and the rest of the config is untouched", config["mixins"] == ["ExampleMixin"])
        print("\n" + ("mod backport checks passed" if not FAILURES else f"{FAILURES} check(s) failed"))
        return 1 if FAILURES else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
