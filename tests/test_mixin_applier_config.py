#!/usr/bin/env python3
"""Two ways a mixin config names things, both of which the applier got wrong.

Every other test in this directory uses the shapes that already worked, which is why these two survived:
they are how Sodium, Lithium, Iris and Meteor write their configs, and both failures were *silent refusals*
- the bake stayed green, the mod's mixins simply were not there.

  1. A name in a config is relative to the config's `package`, even when it has dots in it.
     `"package": "example.mixin"` with `"client": ["nested.GameMixin"]` means
     `example.mixin.nested.GameMixin`. Reading "contains a dot" as "already qualified" sent the applier looking
     for `nested/GameMixin.class` at the root of the jar and refusing each one as "listed as a mixin but its
     class is not in the jar". That alone took Sodium from 65 applied classes to 1.

  2. An @Inject may name the target's descriptor in the same string: `method = "value()I"`.
     The applier looked up a method *named* `value()I`, found nothing, and refused it as "an @Inject into a
     method this client does not have" - while the method was right there. That took Lithium from 155 applied
     classes to 139 and cost Meteor and Iris their GUI and renderer injects.
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

CLIENT = """
package example.client;

public class Game {
    public int value() {
        return 1;
    }

    public String label() {
        return "vanilla";
    }

    public static void main(String[] args) {
        Game game = new Game();
        System.out.println("value = " + game.value());
        System.out.println("label = " + game.label());
    }
}
"""

# one mixin, in a *sub*package, so its config entry has to be qualified by the applier
MIXIN = """
package example.mixin.nested;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(example.client.Game.class)
public class GameMixin {
    @Inject(method = "value()I", at = @At("HEAD"), cancellable = true)
    private void onValue(CallbackInfoReturnable<Integer> callback) {
        System.out.println("the mixin ran");
        callback.setReturnValue(41);
    }
}
"""

FABRIC_JSON = ('{"schemaVersion": 1, "id": "config-test", "version": "1.0.0", "entrypoints": {},'
          ' "mixins": ["example.mixins.json"]}\n')
CONFIG = ('{"required": true, "package": "example.mixin", "compatibilityLevel": "JAVA_21",'
          ' "mixins": [], "client": ["nested.GameMixin"], "injectors": {"defaultRequire": 1}}\n')


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


def java_home() -> pathlib.Path | None:
    for candidate in [os.environ.get("JAVA_HOME"), str(pathlib.Path.home() / ".jdks" / "jdk-21.0.12.1+1" / "Contents" / "Home")]:
        if candidate and (pathlib.Path(candidate) / "bin" / "javac").is_file():
            return pathlib.Path(candidate)
    found = shutil.which("javac")
    return pathlib.Path(found).parent.parent if found else None


def asm_classpath() -> str:
    jars = [jar for jar in ASM_JARS if jar.is_file()]
    if not jars:
        return ""
    return os.pathsep.join(str(jar) for jar in jars)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def build(javac: str, sources: list[pathlib.Path], out: pathlib.Path, classpath: str = "") -> bool:
    out.mkdir(parents=True, exist_ok=True)
    cmd = [javac, "-nowarn", "-d", str(out)]
    if classpath:
        cmd += ["-cp", classpath]
    cmd += [str(source) for source in sources]
    return run(cmd).returncode == 0


def make_jar(jar: pathlib.Path, classes: pathlib.Path, extras: dict[str, str]) -> None:
    with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(classes.rglob("*.class")):
            archive.write(path, path.relative_to(classes).as_posix())
        for name, text in extras.items():
            archive.writestr(name, text)


def main() -> int:
    home = java_home()
    asm = asm_classpath()
    if not home or not asm:
        print("  SKIP  needs a JDK and the ASM jars (python3 tools/fetch_asm.py)")
        return 0
    javac, java = str(home / "bin" / "javac"), str(home / "bin" / "java")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="webmc-mixin-config-"))
    if os.environ.get("KEEP"):
        print("work dir:", tmp)
    print("a config that names its mixins the way real mods do")

    applier = tmp / "applier"
    check("the applier compiles", build(javac, [APPLIER], applier, asm))
    shims = tmp / "shims"
    check("the mixin stubs compile", build(javac, sorted(SHIMS.rglob("*.java")), shims))

    client_src = tmp / "src" / "example" / "client" / "Game.java"
    client_src.parent.mkdir(parents=True)
    client_src.write_text(CLIENT)
    classes = tmp / "classes"
    check("the client compiles", build(javac, [client_src], classes))

    mixin_src = tmp / "mod-src" / "example" / "mixin" / "nested" / "GameMixin.java"
    mixin_src.parent.mkdir(parents=True)
    mixin_src.write_text(MIXIN)
    mod_classes = tmp / "mod-classes"
    check("the mod compiles against the stubs",
          build(javac, [mixin_src], mod_classes, os.pathsep.join([str(shims), str(classes)])))
    mod_jar = tmp / "config-test.jar"
    make_jar(mod_jar, mod_classes, {"fabric.mod.json": FABRIC_JSON, "example.mixins.json": CONFIG})

    applied = run([java, "-cp", os.pathsep.join([str(applier), asm]), "dev.gaius.tools.WebMcMixinApplier",
                   str(mod_jar), str(classes)])
    check("the applier accepts a subs-package name that is not fully qualified",
          "listed as a mixin but its class is not in the jar" not in applied.stdout,
          applied.stdout.strip()[-300:])
    check("the applier accepts an @Inject that names the descriptor",
          "does not have" not in applied.stdout, applied.stdout.strip()[-300:])
    check("nothing was refused at all", "REFUSED" not in applied.stdout, applied.stdout.strip()[-300:])

    for shim in ("org/spongepowered/asm/mixin/injection/callback/CallbackInfoReturnable.class",
                 "org/spongepowered/asm/mixin/injection/callback/CallbackInfo.class"):
        target = classes / shim
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(shims / shim, target)
    ran = run([java, "-cp", str(classes), "example.client.Game"])
    check("and the mixin really runs in the merged client",
          "the mixin ran" in ran.stdout and "value = 41" in ran.stdout, ran.stdout.strip())
    check("while the rest of the client is untouched", "label = vanilla" in ran.stdout, ran.stdout.strip())

    if not os.environ.get("KEEP"):
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\nmixin config checks: {FAILURES} failed")
        return 1
    print("\nmixin config checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
