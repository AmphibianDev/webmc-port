#!/usr/bin/env python3
"""Tests for the mod bake: a Fabric mod set turned into code the compiled client runs.

    python3 tests/test_bake_mods.py

Offline. A fake engine tree is built here in the shape the real one has (a profile, a tools package, an
overrides tree), a fake mod jar is built with a `fabric.mod.json` and a class in it, and the test checks
what the bake writes rather than only that it wrote something:

  * the patcher source is glued in with its own class name in the right places (a `printf` template with a
    mistyped placeholder is a compile error 45 minutes into a build);
  * the hook calls one entrypoint per mod, and does *not* invent a call for a mod that declares none;
  * the mod's classes are shipped to where the patcher will look, and the `WEBMC_MODS` list points at them;
  * the version profile's `extraPatchers` gains the patcher, once, and keeps whatever was there;
  * a mod carrying mixins is reported as such, because the bake applies entrypoints and the report is the
    only place that says so.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import bake_mods  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


FAKE_CLASS = b"\xca\xfe\xba\xbe\x00\x00\x00\x41fake"


def fake_engine(root: pathlib.Path, version: str = "1.21.11") -> pathlib.Path:
    (root / "port" / "versions").mkdir(parents=True)
    (root / "port" / "versions" / f"{version}.json").write_text(json.dumps({
        "id": version, "releaseType": "release", "protocolVersion": 777, "worldVersion": 1,
        "javaVersion": 21, "classFileVersion": 65, "clientDistribution": "obfuscated-with-mappings",
        "patchSet": "legacy-12111", "extraPatchers": ["Minecraft12111BrowserPatcher"],
    }))
    (root / "port" / "tools" / "src" / "main" / "java" / "dev" / "gaius" / "tools").mkdir(parents=True)
    (root / "port" / "overrides" / "client" / "src" / "versions" / version / "java").mkdir(parents=True)
    return root


def fake_mod(path: pathlib.Path, mod_id: str, entrypoint: str | None, mixins: bool = False) -> pathlib.Path:
    descriptor = {"schemaVersion": 1, "id": mod_id, "name": mod_id.upper(), "version": "1.0.0",
                  "environment": "client"}
    if entrypoint:
        descriptor["entrypoints"] = {"client": [entrypoint]}
    if mixins:
        descriptor["mixins"] = ["example.mixins.json"]
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("fabric.mod.json", json.dumps(descriptor))
        # a real mod's class sits at the path its descriptor names, which is what the hook then calls
        jar.writestr((entrypoint.replace(".", "/") if entrypoint else f"{mod_id}/Mod") + ".class", FAKE_CLASS)
        if mixins:
            jar.writestr("example.mixins.json", '{"required": true, "package": "example.mixin"}')
            jar.writestr("example/mixin/MixinThing.class", FAKE_CLASS)
    return path


def test_read_mod(tmp: pathlib.Path) -> None:
    print("\nwhat a mod jar says about itself")
    plain = bake_mods.read_mod(fake_mod(tmp / "plain.jar", "plain", "com.example.Plain"))
    check("the id, the entrypoint and the class count come out", plain["id"] == "plain"
          and plain["entrypoint"] == "com.example.Plain" and plain["classes"] == 1, str(plain))
    check("a mod with no mixins is not marked as needing them", plain["needs_mixins"] is False)
    check("a mod with no entrypoint says so rather than inventing one",
          bake_mods.read_mod(fake_mod(tmp / "lib.jar", "lib", None))["entrypoint"] is None)
    mixed = bake_mods.read_mod(fake_mod(tmp / "mixed.jar", "mixed", "com.example.Mixed", mixins=True))
    check("a mod that carries mixins is flagged", mixed["needs_mixins"] is True, str(mixed))
    check("and its mixin configs are listed", mixed["mixin_configs"] == ["example.mixins.json"])

    print("\nthe generated hook")
    hook = bake_mods.hook_source([plain, bake_mods.read_mod(fake_mod(tmp / "lib.jar", "lib", None))])
    check("it is a class in its own package", hook.startswith("package webmc.baked;"))
    check("it has the method the patcher calls",
          "public static void initialize()" in hook)
    check("it instantiates and calls each entrypoint",
          "new com.example.Plain().onInitializeClient();" in hook, hook[:400])
    check("and it does not invent a call for a mod with no entrypoint",
          hook.count("onInitializeClient()") == 1)
    check("the class it declares matches its name", "class WebMcBakedMods" in hook)


def test_bake(tmp: pathlib.Path) -> None:
    print("\nbaking a set into an engine")
    engine = fake_engine(tmp / "engine")
    jars = tmp / "jars"
    jars.mkdir()
    one = fake_mod(jars / "hello-fabric.jar", "hello-fabric", "com.example.HelloFabricMod")
    two = fake_mod(jars / "mixed.jar", "mixed", "com.example.Mixed", mixins=True)
    lines: list[str] = []
    report = bake_mods.bake(engine, "1.21.11", [one, two], jars, log=lines.append)

    patcher = engine / "port" / "tools" / "src" / "main" / "java" / "dev" / "gaius" / "tools" / "WebMcModBakePatcher.java"
    check("the patcher source is written into the engine's tools package", patcher.is_file())
    source = patcher.read_text()
    check("it declares the class its file name promises",
          f"public final class {bake_mods.PATCHER_CLASS} {{" in source, source[:200])
    check("and names itself as the class to construct",
          f"private {bake_mods.PATCHER_CLASS}() {{" in source)
    check("its usage message names itself too",
          f"usage: {bake_mods.PATCHER_CLASS} <input.jar>" in source, source[:900])
    check("it reads the mod list from the environment, not from a hardcoded path",
          'System.getenv("WEBMC_MODS")' in source)
    check("it copies classes rather than recompiling them",
          "Files.write(target, stream.readAllBytes())" in source)
    check("and it injects its call into the client's static initialiser",
          'method.name.equals("<clinit>")' in source and "INVOKESTATIC" in source)

    hook = engine / "port" / "overrides" / "client" / "src" / "versions" / "1.21.11" / "java" / "webmc" / "baked" / "WebMcBakedMods.java"
    check("the hook is written as a source override for this version", hook.is_file())
    check("and it mentions both mods' entrypoints", "com.example.HelloFabricMod" in hook.read_text()
          and "com.example.Mixed" in hook.read_text())

    shim = engine / "port" / "overrides" / "client" / "src" / "versions" / "1.21.11" / "java" / "net" / "fabricmc" / "api" / "ClientModInitializer.java"
    check("the Fabric interface a mod compiles against is written from this repository's shim", shim.is_file())

    profile = json.loads((engine / "port" / "versions" / "1.21.11.json").read_text())
    check("the profile runs the patcher at the tail of its chain",
          profile["extraPatchers"] == ["Minecraft12111BrowserPatcher", bake_mods.PATCHER_CLASS],
          str(profile["extraPatchers"]))
    check("and a second bake does not add it twice",
          bake_mods.bake(engine, "1.21.11", [one], jars, log=lambda *_: None)["patcher"] == bake_mods.PATCHER_CLASS
          and json.loads((engine / "port" / "versions" / "1.21.11.json").read_text())["extraPatchers"]
          == ["Minecraft12111BrowserPatcher", bake_mods.PATCHER_CLASS])

    shipped = pathlib.Path(report["mod_jars"][0])
    check("the mod jars are copied to where the build will run", shipped.is_file()
          and shipped.parent.name == "webmc-mods", str(shipped))
    check("and the environment list points at them", report["webmc_mods"] == ":".join(report["mod_jars"]))
    check("the report names the entrypoints that will be called",
          report["entrypoints"] == ["hello-fabric", "mixed"], str(report["entrypoints"]))
    check("and names the mod whose mixins it cannot apply",
          report["with_mixins"] == ["mixed"], str(report["with_mixins"]))
    check("which the log says out loud too",
          any("will not be applied" in line for line in lines), str(lines))

    print("\nwhat it refuses")
    try:
        bake_mods.bake(engine, "1.21.10", [one], jars, log=lambda *_: None)
        check("a version with no profile in the engine is refused", False, "no error")
    except SystemExit as error:
        check("a version with no profile in the engine is refused", "no profile at" in str(error), str(error))
    empty = tmp / "not-an-engine"
    empty.mkdir()
    try:
        bake_mods.bake(empty, "1.21.11", [one], jars, log=lambda *_: None)
        check("something that is not a profile engine is refused", False, "no error")
    except SystemExit as error:
        check("something that is not a profile engine is refused",
              "does not look like a profile-based engine" in str(error), str(error))


def test_command_line(tmp: pathlib.Path) -> None:
    print("\nthe command")
    engine = fake_engine(tmp / "engine-cli")
    jar = fake_mod(tmp / "one.jar", "one", "com.example.One")
    report_path = tmp / "report.json"
    code = bake_mods.main(["--engine", str(engine), "--version", "1.21.11", "--mods", str(jar),
                           "--report", str(report_path)])
    check("it exits zero", code == 0, str(code))
    check("and writes the report", report_path.is_file()
          and json.loads(report_path.read_text())["version"] == "1.21.11")
    check("with no mods it says so instead of writing an empty bake",
          bake_mods.main(["--engine", str(engine), "--version", "1.21.11"]) == 2)


def test_patcher_runs(tmp: pathlib.Path) -> None:
    """The generated Java, compiled and executed for real - the half a Python test cannot see.

    Needs javac, java and ASM on disk (the engine's build fetches ASM anyway); without them it says so and
    moves on, like every other test here that wants a JDK. What it proves is the whole contract: the class
    compiles, the mod's classes are copied into the patch output, and the call lands in the client's own
    static initialiser - verified by reading the bytecode back with javap.
    """
    print("\nthe generated patcher, compiled and run")
    # a JDK that actually works: macOS ships a /usr/bin/javac that exists and cannot compile, so the
    # finder from the loader's build tool is used and the stub is weeded out by running it
    import build_loader  # noqa: PLC0415 - only on this path
    javac = build_loader.find_javac()
    if javac:
        probe = subprocess.run([javac, "-version"], capture_output=True, text=True)
        if "Unable to locate" in (probe.stderr + probe.stdout):
            javac = None
    java = str(pathlib.Path(javac).parent / "java") if javac else None
    if javac and not pathlib.Path(java).exists():
        java = None
    javap = str(pathlib.Path(javac).parent / "javap") if javac else None
    asm = None
    for candidate in (ROOT / "work" / "asm", pathlib.Path("/tmp/baketest")):
        jars = sorted(candidate.glob("asm*.jar")) if candidate.is_dir() else []
        if len(jars) >= 2:
            asm = ":".join(str(jar) for jar in jars)
            break
    if not javac or not java or not asm:
        print("  SKIP  needs javac, java and asm/asm-tree jars (put them in work/asm/)")
        return

    engine = fake_engine(tmp / "engine-patch")
    jar = fake_mod(tmp / "hello-fabric.jar", "hello-fabric", "com.example.HelloFabricMod")
    bake_mods.bake(engine, "1.21.11", [jar], tmp, log=lambda *_: None)
    patcher = engine / "port" / "tools" / "src" / "main" / "java" / "dev" / "gaius" / "tools" / "WebMcModBakePatcher.java"

    # a client jar of our own: one class, with a static initialiser to inject into
    source = tmp / "clientsrc" / "net" / "minecraft" / "client"
    source.mkdir(parents=True, exist_ok=True)
    (source / "Minecraft.java").write_text(
        "package net.minecraft.client;\n"
        "public class Minecraft {\n"
        "    static { System.setProperty(\"mc\", \"loaded\"); }\n"
        "}\n")
    classes = tmp / "clientclasses"
    classes.mkdir(parents=True, exist_ok=True)
    compiled = subprocess.run([javac, "-d", str(classes), str(source / "Minecraft.java")],
                              capture_output=True, text=True)
    check("a fake client compiles", compiled.returncode == 0, compiled.stderr[:200])
    client_jar = tmp / "client.jar"
    with zipfile.ZipFile(client_jar, "w") as archive:
        for path in classes.rglob("*.class"):
            archive.write(path, str(path.relative_to(classes)))

    patcher_classes = tmp / "patcher-classes"
    built = subprocess.run([javac, "-nowarn", "-cp", asm, "-d", str(patcher_classes), str(patcher)],
                           capture_output=True, text=True)
    check("the generated patcher compiles as it is written", built.returncode == 0, built.stderr[:400])
    if built.returncode != 0:
        return

    output = tmp / "patched"
    environment = dict(os.environ)
    environment["WEBMC_MODS"] = str(engine / "port" / "work" / "1.21.11" / "webmc-mods" / jar.name)
    run = subprocess.run([java, "-cp", f"{patcher_classes}{os.pathsep}{asm}",
                          f"dev.gaius.tools.{bake_mods.PATCHER_CLASS}", str(client_jar), str(output),
                          "1.21.11"], capture_output=True, text=True, env=environment)
    check("it runs against a client jar and reports what it did",
          run.returncode == 0 and "injected" in run.stdout, (run.stdout + run.stderr)[:300])
    check("the mod's own class is copied into the client's patch output",
          (output / "com" / "example" / "HelloFabricMod.class").is_file())
    check("and the client class is rewritten", (output / "net" / "minecraft" / "client" / "Minecraft.class").is_file())

    disassembly = subprocess.run([javap, "-c", "-p", "-cp", str(output), "net.minecraft.client.Minecraft"],
                                 capture_output=True, text=True).stdout
    check("the client's static initialiser calls the baked mods",
          "webmc/baked/WebMcBakedMods.initialize:()V" in disassembly,
          disassembly[-300:])
    # inside the static initialiser itself, not merely somewhere in the dump
    initialiser = disassembly.split("static {}", 1)[-1].split("\n}", 1)[0]
    check("and it calls it on the way out of the client's own initialisation, before returning",
          "webmc/baked/WebMcBakedMods.initialize" in initialiser
          and initialiser.index("webmc/baked/WebMcBakedMods.initialize") < initialiser.rindex("return"),
          initialiser[-300:])

    print("\nand with no mods it does nothing, loudly")
    empty = subprocess.run([java, "-cp", f"{patcher_classes}{os.pathsep}{asm}",
                            f"dev.gaius.tools.{bake_mods.PATCHER_CLASS}", str(client_jar),
                            str(tmp / "patched-empty")], capture_output=True, text=True,
                           env={k: v for k, v in environment.items() if k != "WEBMC_MODS"})
    leftovers = sorted(path.name for path in (tmp / "patched-empty").rglob("*.class")) \
        if (tmp / "patched-empty").is_dir() else []
    check("a build with an empty WEBMC_MODS says so and writes no class file",
          "no mods in WEBMC_MODS" in empty.stdout and not leftovers,
          f"{empty.stdout[:160]} written: {leftovers}")


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        tmp = pathlib.Path(scratch)
        test_read_mod(tmp)
        test_bake(tmp)
        test_command_line(tmp)
        test_patcher_runs(tmp)
    print(f"\n{FAILURES} bake test(s) FAILED" if FAILURES else "\nbake tests passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
