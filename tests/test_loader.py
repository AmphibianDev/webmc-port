#!/usr/bin/env python3
"""Tests for WebMC's own loader: webmc.loader.WebMcLoader.

    python3 tests/test_loader.py

Two halves, on purpose.

**Structural** - always runs, no JDK: the jar the site deploys has a manifest that names the right
main class, the classes it is supposed to contain, and (when site/launch.json pins a hash) the bytes
that are actually there. This is the half that catches "the page is loading something else".

**Behavioural** - needs javac and java, and is skipped without them, like the rest of this repository:
a stub game, a stub mod and a stub Fabric are built from source in a scratch directory and the loader
is made to start them. Nothing is downloaded and no Minecraft is involved. What it proves is the
loader's contract, which is the part the browser cannot tell us until CheerpJ is running:

  * a jar with webmc.mod.json is found, started, and can see the game's own classes;
  * the game still starts when there are no mods, and when a jar in the mods folder is not a mod;
  * Fabric mode sets the properties Fabric actually reads (fabric.gameJarPath, fabric.addMods,
    fabric.side, fabric.gameVersion) before touching its entry class;
  * a missing or unreadable plan fails loudly instead of silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_loader  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


GAME_VERSION = """
package example;

public final class GameVersion {
    public static final String NAME = "stub-1.12.2";
}
"""

GAME_MAIN = """
package example;

public final class GameMain {
    public static void main(String[] args) {
        System.out.println("GAME started, classpath sees " + GameVersion.NAME + ", args=" + args.length);
    }
}
"""

# A mod that reads a *game* class. If the loader put the mod on its own class path without the game's
# classes being visible from it, this fails - which is exactly the property worth pinning down.
MOD = """
package com.example;

import webmc.loader.WebMcApi;
import webmc.loader.WebMcMod;

public final class StubMod implements WebMcMod {
    public void onLoad(WebMcApi api) {
        System.out.println("MOD started, sees " + example.GameVersion.NAME + " via " + api.gameVersion());
    }
}
"""

MOD_DESCRIPTOR = """{
  "id": "stub-mod",
  "name": "Stub",
  "version": "9.9",
  "entrypoint": "com.example.StubMod"
}
"""

NOT_A_MOD = """
package other;

public final class Library {
    public static String ping() { return "library present"; }
}
"""

FABRIC_KNOT = """
package net.fabricmc.loader.impl.launch.knot;

public final class KnotClient {
    public static void main(String[] args) {
        System.out.println("KNOT gameJarPath=" + System.getProperty("fabric.gameJarPath")
            + " side=" + System.getProperty("fabric.side")
            + " version=" + System.getProperty("fabric.gameVersion")
            + " addMods=" + System.getProperty("fabric.addMods")
            + " modsFolder=" + System.getProperty("fabric.modsFolder")
            + " args=" + args.length);
    }
}
"""


def write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def jar_up(target: pathlib.Path, classes: pathlib.Path, extras: dict[str, str] | None = None) -> pathlib.Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as jar:
        for path in sorted(classes.rglob("*.class")):
            jar.write(path, str(path.relative_to(classes)))
        for name, text in (extras or {}).items():
            jar.writestr(name, text)
    return target


def javac(javac_path: str, sources: list[pathlib.Path], classes: pathlib.Path,
          classpath: str = "") -> tuple[bool, str]:
    command = [javac_path, "-nowarn", "-d", str(classes)]
    if classpath:
        command += ["-cp", classpath]
    command += [str(source) for source in sources]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode == 0, (result.stderr or result.stdout)


def java_run(java_path: str, classpath: str, main: str, args: list[str], cwd: pathlib.Path):
    return subprocess.run([java_path, "-cp", classpath, main] + args,
                          capture_output=True, text=True, cwd=str(cwd), timeout=120)


def structural(jar: pathlib.Path, pinned: pathlib.Path) -> bool:
    """The half that always runs. Returns whether there was a jar to check."""
    print("\nthe jar the site deploys")
    check("site/loader/webmc-loader.jar is not committed (built, like site/libs)",
          "site/loader/" in (ROOT / ".gitignore").read_text(), "add it to .gitignore")
    if not jar.is_file():
        print("  SKIP  the loader jar has not been built here (run tools/build_loader.py, or let CI "
              "build it) - with no jar there is nothing to check, and the launcher degrades to "
              "starting the game class directly")
        return False
    with zipfile.ZipFile(jar) as archive:
        names = archive.namelist()
        manifest = archive.read("META-INF/MANIFEST.MF").decode()
    check("its manifest names webmc.loader.WebMcLoader",
          "Main-Class: webmc.loader.WebMcLoader" in manifest, manifest.replace("\r\n", " | "))
    for expected in ("webmc/loader/WebMcLoader.class", "webmc/loader/WebMcApi.class",
                     "webmc/loader/WebMcMod.class"):
        check(f"it carries {expected}", expected in names)
    check("it is small enough to sit on the critical path", jar.stat().st_size < 64 * 1024,
          f"{jar.stat().st_size} bytes")
    with zipfile.ZipFile(jar) as archive:
        bytecode = archive.read("webmc/loader/WebMcLoader.class")
    major = int.from_bytes(bytecode[6:8], "big")
    check("it targets Java 8 bytecode, so one jar serves CheerpJ's Java 8 and 17 modes", major == 52,
          f"class file major version {major} ({major - 44} = Java {major - 44}); "
          "a jar above 52 will not load in Java 8 mode, which is the mode 1.12.2 and older need")
    if pinned.is_file():
        document = json.loads(pinned.read_text())
        want = (document.get("loader") or {}).get("sha256")
        got = hashlib.sha256(jar.read_bytes()).hexdigest()
        check("site/launch.json pins this exact jar", want in (None, got),
              f"launch.json says {str(want)[:16]}…, the jar is {got[:16]}…")
    return True


def behavioural(javac_path: str, java_path: str, loader_source: pathlib.Path) -> None:
    print("\nthe loader, started with a real JVM")
    with tempfile.TemporaryDirectory() as scratch:
        work = pathlib.Path(scratch)
        loader_jar = work / "webmc-loader.jar"
        built = build_loader.build(javac_path, loader_jar, log=lambda *a: None)
        check("the loader compiles", bool(built["entries"]))
        check("and it compiles to Java 8 bytecode even here", built["java"] == 8, str(built["java"]))

        # the stub game
        game_src = work / "game-src"
        write(game_src / "example" / "GameVersion.java", GAME_VERSION)
        write(game_src / "example" / "GameMain.java", GAME_MAIN)
        game_classes = work / "game-classes"
        ok, error = javac(javac_path, sorted(game_src.rglob("*.java")), game_classes)
        check("the stub game compiles", ok, error[:200])
        game_jar = jar_up(work / "game.jar", game_classes)

        # a mod that implements our interface and reads a game class
        mod_src = write(work / "mod-src" / "com" / "example" / "StubMod.java", MOD)
        mod_classes = work / "mod-classes"
        ok, error = javac(javac_path, [mod_src], mod_classes, classpath=str(loader_jar) + os.pathsep + str(game_jar))
        check("a mod compiles against the loader's api and the game", ok, error[:200])
        mod_jar = jar_up(work / "mods" / "stub-mod.jar", mod_classes,
                         {"webmc.mod.json": MOD_DESCRIPTOR})

        # a jar in the mods folder that is not a mod at all
        library_src = write(work / "lib-src" / "other" / "Library.java", NOT_A_MOD)
        library_classes = work / "lib-classes"
        javac(javac_path, [library_src], library_classes)
        library_jar = jar_up(work / "mods" / "library.jar", library_classes)

        classpath = os.pathsep.join([str(loader_jar), str(game_jar)])
        plan = work / "plan.properties"

        # ---- vanilla, with a mod, a library, and the game args passed through
        plan.write_text("\n".join([
            "mode=vanilla",
            "version=stub-1.12.2",
            "game.class=example.GameMain",
            "game.jar=" + str(game_jar),
            "mod.count=2",
            "mod.0.file=" + str(mod_jar),
            "mod.0.id=stub-mod",
            "mod.1.file=" + str(library_jar),
            "mod.1.id=library",
        ]) + "\n")
        run = java_run(java_path, classpath, "webmc.loader.WebMcLoader",
                       [str(plan), "--username", "tester"], work)
        out = run.stdout
        check("the loader starts the mod", "MOD started, sees stub-1.12.2" in out, out[-300:])
        check("and reports it by id and version", "mod stub-mod 9.9: started" in out, out[-300:])
        check("the mod can read a class from the game", "via stub-1.12.2" in out)
        check("then the game itself runs", "GAME started" in out, out[-300:])
        check("with the arguments it was given", "args=2" in out)
        check("a jar that is not a mod is reported, not started",
              "library: no webmc.mod.json" in out)
        check("the exit code is zero", run.returncode == 0, str(run.returncode))
        check("the mod's line comes before the game's",
              out.index("MOD started") < out.index("GAME started"))

        # ---- vanilla, no mods at all
        plan.write_text("\n".join([
            "mode=vanilla", "version=stub-1.12.2", "game.class=example.GameMain",
            "game.jar=" + str(game_jar), "mod.count=0",
        ]) + "\n")
        run = java_run(java_path, classpath, "webmc.loader.WebMcLoader", [str(plan)], work)
        check("with no mods the game still starts", "GAME started" in run.stdout and run.returncode == 0,
              run.stdout[-200:] + run.stderr[-200:])
        check("and the loader says so", "mods: none" in run.stdout)

        # ---- fabric
        knot_src = write(work / "knot-src" / "net" / "fabricmc" / "loader" / "impl" / "launch"
                         / "knot" / "KnotClient.java", FABRIC_KNOT)
        knot_classes = work / "knot-classes"
        ok, error = javac(javac_path, [knot_src], knot_classes)
        check("the stub Fabric entry class compiles", ok, error[:200])
        knot_jar = jar_up(work / "fabric-stub.jar", knot_classes)
        plan.write_text("\n".join([
            "mode=fabric", "version=stub-1.12.2", "game.class=example.GameMain",
            "loader.main=net.fabricmc.loader.impl.launch.knot.KnotClient",
            "game.jar=/str/client_stub.jar",
            "mod.count=1", "mod.0.file=" + str(mod_jar), "mod.0.id=stub-mod",
        ]) + "\n")
        run = java_run(java_path, os.pathsep.join([str(loader_jar), str(knot_jar)]),
                       "webmc.loader.WebMcLoader", [str(plan), "--username", "tester"], work)
        out = run.stdout
        check("fabric mode starts Fabric's own entry class", "KNOT " in out, out[-300:])
        check("it points Fabric at the game jar", "gameJarPath=/str/client_stub.jar" in out, out[-300:])
        check("it tells Fabric where the mods are",
              "addMods=" in out and "stub-mod.jar" in out, out[-300:])
        check("it declares a client", "side=client" in out)
        check("and the game version", "version=stub-1.12.2" in out)

        # ---- a plan that is not there
        run = java_run(java_path, classpath, "webmc.loader.WebMcLoader",
                       [str(work / "nope.properties")], work)
        check("a missing plan exits non-zero and says why",
              run.returncode == 2 and "cannot read the launch plan" in run.stdout,
              f"exit {run.returncode}: {run.stdout[-200:]}")

    # ---- reproducibility, which is what makes the pinned hash mean anything
    with tempfile.TemporaryDirectory() as scratch:
        first = build_loader.build(javac_path, pathlib.Path(scratch) / "a.jar", log=lambda *a: None)
        second = build_loader.build(javac_path, pathlib.Path(scratch) / "b.jar", log=lambda *a: None)
        check("two builds of the same source are byte-identical", first["sha256"] == second["sha256"],
              f"{first['sha256'][:12]} vs {second['sha256'][:12]}")


def usable_javac(explicit: str | None = None) -> str | None:
    """A javac that really compiles: macOS ships a /usr/bin/javac that exists and cannot work."""
    path = build_loader.find_javac(explicit)
    if not path:
        return None
    probe = subprocess.run([path, "-version"], capture_output=True, text=True)
    if "Unable to locate" in (probe.stderr + probe.stdout):
        return None
    return path


def source_sanity(loader_src: pathlib.Path, mod_src: pathlib.Path) -> None:
    """What can be checked about Java without a JDK: the things javac refuses.

    This machine and plenty of others have no JDK, and the whole point of the loader's source being in
    the repository is that it is reviewable and verifiable there - so the failures a compiler finds
    first (a package that does not match its directory, an unbalanced brace, a mod calling a method the
    API does not have) are checked here, and the compiler's half is the behavioural test below.
    """
    print("\nthe loader's source, without a compiler")
    files = sorted(loader_src.rglob("*.java")) + sorted(mod_src.rglob("*.java"))
    check("there is source to check", len(files) >= 4, str([str(f) for f in files]))
    for path in files:
        text = path.read_text()
        name = path.name
        declared = re.search(r"^package\s+([\w.]+)\s*;", text, re.M)
        expected = str(path.parent).split("src/")[-1].replace("/", ".")
        check(f"{name} declares the package its directory implies",
              declared is not None and declared.group(1) == expected,
              f"{declared.group(1) if declared else 'none'} vs {expected}")
        # braces and parentheses, ignoring those inside strings and comments: enough to catch a real
        # mistake before a compiler does, and honest about being approximate
        stripped = re.sub(r'"(\\.|[^"\\])*"', '""', text)
        stripped = re.sub(r"//[^\n]*", "", stripped)
        stripped = re.sub(r"/\*[\s\S]*?\*/", "", stripped)
        check(f"{name}: braces balance", stripped.count("{") == stripped.count("}"),
              f"{stripped.count('{')} {{ vs {stripped.count('}')} }}")
        check(f"{name}: parentheses balance", stripped.count("(") == stripped.count(")"),
              f"{stripped.count('(')} ( vs {stripped.count(')')} )")

    api = (loader_src / "webmc" / "loader" / "WebMcApi.java").read_text()
    declared_methods = set(re.findall(r"\b(?:\w+)\s+(\w+)\s*\(", api))
    hello = next(pathlib.Path(mod_src).rglob("HelloMod.java"), None)
    check("the example mod is in the tree", hello is not None)
    example = hello.read_text() if hello else ""
    called = set(re.findall(r"api\.(\w+)\(", example))
    check(f"the example mod only calls api methods that exist ({', '.join(sorted(called))})",
          called <= declared_methods, str(sorted(called - declared_methods)))
    descriptor = json.loads((ROOT / "mods" / "examples" / "hello-webmc" / "resources"
                             / "webmc.mod.json").read_text())
    entrypoint = descriptor["entrypoint"]
    declared_type = re.search(r"public\s+final\s+class\s+(\w+)", example)
    check("the example mod's descriptor names the class that implements the interface",
          declared_type is not None and entrypoint.endswith(declared_type.group(1))
          and "implements WebMcMod" in example, f"descriptor says {entrypoint}")
    manifest = json.loads((ROOT / "mods" / "mods.json").read_text())
    listed = {mod["id"]: mod for mod in manifest["mods"]}
    check("and the mods manifest lists that same class",
          listed.get("hello-webmc", {}).get("entrypoint") == entrypoint,
          str(listed.get("hello-webmc")))
    check("the loader jar's Main-Class exists as one of its classes",
          any(path.name == "WebMcLoader.java" and f"class {build_loader.MAIN_CLASS.split('.')[-1]}" in path.read_text()
              for path in files))


def main() -> int:
    print("the loader, as the site deploys it")
    javac_path = usable_javac()
    jar = ROOT / "site" / "loader" / "webmc-loader.jar"
    if not jar.is_file() and javac_path:
        # The tests are allowed to build what they test: this is the one artefact in the repository
        # that is code we wrote and that the page loads, so verifying it is worth two seconds.
        built = build_loader.build(javac_path, jar, log=lambda *args: None)
        print(f"  built it here: {built['bytes']} bytes, sha256 {built['sha256'][:16]}…")
    structural(jar, ROOT / "site" / "launch.json")
    source_sanity(ROOT / "mods" / "loader-jvm" / "src", ROOT / "mods" / "examples")

    java_path = shutil.which("java") or (str(pathlib.Path(javac_path).parent / "java") if javac_path else "")
    if not javac_path or not java_path or not pathlib.Path(java_path).exists():
        print("\nskipped the behavioural half: no JDK on this machine "
              "(a real JVM is what runs the loader, so there is nothing to prove without one)")
    else:
        behavioural(javac_path, java_path, ROOT / "mods" / "loader-jvm" / "src")

    print(f"\n{FAILURES} loader test(s) FAILED" if FAILURES else "\nloader tests passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
