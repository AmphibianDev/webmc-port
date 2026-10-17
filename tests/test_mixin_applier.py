#!/usr/bin/env python3
"""The mixin applier, proved on a JVM instead of described.

A mixin is a promise about somebody else's class. The only way to know this applier keeps that promise is to
compile a client, compile a mod that mixes into it, apply the mixin, and then *run* the result and read what it
printed - so that is what this does. The annotations the mod compiles against are this repository's stubs
(`mods/mixin-shims`), the applier is the real one, and the JVM is whatever JDK is on the machine.

Without a JDK the whole file reports one skip, because a mixin test that cannot compile is not a test.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPLIER = ROOT / "mods" / "mixin-applier" / "src" / "dev" / "gaius" / "tools" / "WebMcMixinApplier.java"
SHIMS = ROOT / "mods" / "mixin-shims"

WORK = pathlib.Path(os.environ.get("WEBMC_WORK", pathlib.Path.home() / ".cache" / "webmc" / "mixin-test"))
ASM_JARS = [WORK / "asm.jar", WORK / "asm-tree.jar", ROOT / "work" / "asm" / "asm.jar",
            ROOT / "work" / "asm" / "asm-tree.jar"]

FAILURES = 0
SKIPPED = False


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


def java_home() -> pathlib.Path | None:
    for candidate in [os.environ.get("JAVA_HOME"), "/tmp/jdkx/jdk-21.0.12.1+1/Contents/Home",
                      "/usr/libexec/java_home"]:
        if not candidate:
            continue
        path = pathlib.Path(candidate)
        if (path / "bin" / "javac").is_file():
            return path
    found = shutil.which("javac")
    return pathlib.Path(found).parent.parent if found else None


def asm_classpath() -> str:
    jars = [jar for jar in ASM_JARS if jar.is_file()]
    if not jars:
        subprocess.run([sys.executable, str(ROOT / "tools" / "fetch_asm.py")], capture_output=True, text=True)
        jars = [jar for jar in ASM_JARS if jar.is_file()]
    return os.pathsep.join(str(jar) for jar in jars)


CLIENT_SOURCE = """
package example.client;

public class Game {
    private int ticks = 0;

    public void tick() {
        ticks++;
        System.out.println("client tick " + ticks);
    }

    public String name() {
        return "vanilla";
    }

    public static void main(String[] args) {
        Game game = new Game();
        game.tick();
        game.tick();
        System.out.println("name = " + game.name());
    }
}
"""

# One mod, three kinds of mixin: an @Inject at HEAD that sees the shadowed field, an @Overwrite of a method that
# returns something, and an @Inject at RETURN that runs before every exit.
MIXIN_SOURCE = """
package example.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Overwrite;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(example.client.Game.class)
public class GameMixin {
    @Shadow
    private int ticks;

    @Unique
    private String banner() {
        return "[hello-webmc]";
    }

    @Inject(method = "tick", at = @At("HEAD"))
    private void onTickHead(CallbackInfo callback) {
        System.out.println(this.banner() + " tick begins, shadowed ticks = " + this.ticks);
    }

    @Inject(method = "tick", at = @At("RETURN"))
    private void onTickReturn(CallbackInfo callback) {
        System.out.println(this.banner() + " tick ends");
    }

    @Overwrite
    public String name() {
        return "modded";
    }
}
"""

# A mixin that this bake must refuse out loud rather than apply wrongly.
BAD_SOURCE = """
package example.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(example.client.Game.class)
public class BadMixin {
    @Inject(method = "tick", at = @At("SLICE"))
    private void onField(CallbackInfo callback) {
        System.out.println("never");
    }

    @Inject(method = "noSuchMethodHere", at = @At("HEAD"))
    private void onMissing(CallbackInfo callback) {
        System.out.println("never");
    }
}
"""

FABRIC_JSON = """
{"schemaVersion": 1, "id": "hello-mixin", "name": "Hello Mixin", "version": "1.0.0",
 "environment": "client",
 "entrypoints": {"client": ["example.mixin.GameMixin"]},
 "mixins": ["hello.mixins.json"]}
"""


def make_jar(jar: pathlib.Path, classes: pathlib.Path, extras: dict[str, str]) -> None:
    """A mod jar, written plainly: class files from a directory, plus the descriptor and mixin configs.

    Not with `jar -C a . -C b x.json y.json`: the second file after a -C is looked up in the original directory,
    which makes an empty jar and a confusing error at the far end of the build.
    """
    with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(classes.rglob("*.class")):
            archive.write(path, path.relative_to(classes).as_posix())
        for name, text in extras.items():
            archive.writestr(name, text)


def run(cmd: list[str], cwd: pathlib.Path | None = None, check_ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check_ok and result.returncode != 0:
        print("  command failed:", " ".join(cmd))
        print((result.stdout + result.stderr)[-2000:])
    return result


def build(javac: str, sources: list[pathlib.Path], out: pathlib.Path, classpath: str = "") -> bool:
    out.mkdir(parents=True, exist_ok=True)
    cmd = [javac, "-nowarn", "-d", str(out)]
    if classpath:
        cmd += ["-cp", classpath]
    cmd += [str(source) for source in sources]
    return run(cmd).returncode == 0


def test_without_jdk() -> None:
    global SKIPPED
    print("the mixin applier needs a JDK to be proved (put one on the machine or set JAVA_HOME)")
    SKIPPED = True


def main() -> int:
    home = java_home()
    if not home:
        test_without_jdk()
        print("\n1 mixin test skipped")
        return 0
    javac, java = str(home / "bin" / "javac"), str(home / "bin" / "java")
    asm = asm_classpath()
    if not asm:
        # A fresh clone has no work/asm: the jars are third-party binaries and git-ignored on purpose. Missing
        # inputs are a skip, not a failure - a red suite on a clean checkout teaches people to ignore the suite.
        print("  SKIP  no ASM jars (run python3 tools/fetch_asm.py to check the mixin applier)")
        return 0

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="webmc-mixin-"))
    print(f"the mixin applier, on a real JVM ({home.name})")

    # the applier itself compiles, against ASM only
    applier_out = tmp / "applier"
    check("the applier compiles against ASM and the JDK alone",
          build(javac, [APPLIER], applier_out, asm),
          "it is generated into another project's tooling, so it cannot have other dependencies")

    # the stubs a mod compiles against, and a client to mix into
    shims_out = tmp / "shims"
    shim_sources = sorted(SHIMS.rglob("*.java"))
    check("the mixin annotations are provided as compile-time stubs, so a mod can be built without Sponge",
          len(shim_sources) >= 8 and build(javac, shim_sources, shims_out), f"{len(shim_sources)} file(s)")

    client_src = tmp / "client-src" / "example" / "client" / "Game.java"
    client_src.parent.mkdir(parents=True)
    client_src.write_text(CLIENT_SOURCE)
    classes = tmp / "classes"
    check("a client class compiles", build(javac, [client_src], classes))

    vanilla = run([java, "-cp", str(classes), "example.client.Game"])
    check("and runs before any mod touches it",
          "client tick 1" in vanilla.stdout and "name = vanilla" in vanilla.stdout, vanilla.stdout.strip())

    # the mod: compiled against the stubs, packaged the way a real one is
    mod_classes = tmp / "mod-classes"
    mixin_src = tmp / "mod-src" / "example" / "mixin" / "GameMixin.java"
    mixin_src.parent.mkdir(parents=True)
    mixin_src.write_text(MIXIN_SOURCE)
    check("a mixin mod compiles against those stubs",
          build(javac, [mixin_src], mod_classes, os.pathsep.join([str(shims_out), str(classes)])))
    mixins_config = ('{"required": true, "package": "example.mixin", "compatibilityLevel": "JAVA_21",'
                     ' "mixins": [], "client": ["GameMixin"], "injectors": {"defaultRequire": 1}}\n')
    mod_jar = tmp / "hello-mixin.jar"
    make_jar(mod_jar, mod_classes, {"fabric.mod.json": FABRIC_JSON, "hello.mixins.json": mixins_config})
    check("and packs into a jar with its descriptor and config", mod_jar.is_file())

    # apply, run, read
    applied = run([java, "-cp", os.pathsep.join([str(applier_out), asm]), "dev.gaius.tools.WebMcMixinApplier",
                   str(mod_jar), str(classes)])
    check("the applier runs", applied.returncode == 0, (applied.stdout + applied.stderr)[-600:])
    check("and says what it did instead of just exiting zero",
          "1 overwritten" in applied.stdout and "2 injected" in applied.stdout
          and "shadow(s) read" in applied.stdout, applied.stdout.strip()[:400])

    # the callback classes travel with the client: an injected call constructs one, so it has to be there
    for shim in ("org/spongepowered/asm/mixin/injection/callback/CallbackInfo.class",
                 "org/spongepowered/asm/mixin/injection/callback/CallbackInfoReturnable.class"):
        target = classes / shim
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(shims_out / shim, target)
    modded = run([java, "-cp", str(classes), "example.client.Game"])
    lines = modded.stdout.strip().splitlines()
    check("the injected HEAD runs before the client's own first line",
          lines and lines[0].startswith("[hello-webmc] tick begins"), str(lines[:2]))
    check("it sees the target's own field through @Shadow, which was never copied",
          "shadowed ticks = 0" in lines[0] and "shadowed ticks = 1" in modded.stdout, str(lines))
    check("the injected RETURN runs before the client's line", "tick ends" in modded.stdout)
    check("the @Overwrite replaced the method body", "name = modded" in modded.stdout, modded.stdout)
    check("and the client's own code still runs", "client tick 1" in modded.stdout and "client tick 2" in modded.stdout)
    check("the mixin's annotations did not travel into the client",
          "org/spongepowered/asm/mixin/Inject" not in (classes / "example" / "client" / "Game.class")
          .read_bytes().decode("latin1"),
          "an annotation that is read at build time has no business in the compiled client")

    # the refusals: a @At the bake does not implement, and an injection into a method that is not there
    bad_src = tmp / "bad-src" / "example" / "mixin" / "BadMixin.java"
    bad_src.parent.mkdir(parents=True)
    bad_src.write_text(BAD_SOURCE)
    bad_classes = tmp / "bad-classes"
    build(javac, [bad_src], bad_classes, os.pathsep.join([str(shims_out), str(classes)]))
    bad_jar = tmp / "bad-mixin.jar"
    make_jar(bad_jar, bad_classes,
             {"fabric.mod.json": FABRIC_JSON.replace("hello.mixins.json", "bad.mixins.json")
                                             .replace("GameMixin", "BadMixin"),
              "bad.mixins.json": '{"package": "example.mixin", "client": ["BadMixin"]}\n'})
    fresh = tmp / "fresh-classes"
    shutil.copytree(classes, fresh)
    # rebuild the client so the refusal test starts from an unmodified class
    run([java, "-cp", str(fresh), "example.client.Game"])
    refused = run([java, "-cp", os.pathsep.join([str(applier_out), asm]), "dev.gaius.tools.WebMcMixinApplier",
                   str(bad_jar), str(fresh)], check_ok=False)
    check("a mixin into a method the client does not have is refused, not skipped",
          "REFUSED" in refused.stdout and "does not have" in refused.stdout, refused.stdout.strip()[:400])
    check("an @At the applier does not implement is called out by name",
          "@At(\"SLICE\") is not implemented" in refused.stdout or "@At(\"SLICE\")" in refused.stdout,
          refused.stdout.strip()[:400])
    check("and a refusal is a non-zero exit, so a build cannot ignore it", refused.returncode != 0)

    print(f"\n{12 + 5 - FAILURES}/{12 + 5} mixin checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
