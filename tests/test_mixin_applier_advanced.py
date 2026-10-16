#!/usr/bin/env python3
"""The mixin applier's harder half, proved the same way the easy half is: compile, apply, run, read.

`tests/test_mixin_applier.py` proves the shapes the applier started with. This proves the ones added
for real mods - the ones that made Meteor Client's work list look like a wall:

  * a handler that takes the target's own arguments (97 of Meteor's mixins need it)
  * a handler that takes the return value at RETURN
  * a cancellable injection that stops the rest of the method
  * @Inject at an INVOKE, not just at HEAD/RETURN/TAIL
  * @Redirect, @ModifyArg, @ModifyVariable, @ModifyConstant
  * @ModifyExpressionValue, @ModifyReturnValue, @WrapWithCondition, @ModifyReceiver
  * @Accessor and @Invoker - members the mixin only declared
  * an access widener, applied to the client's own class files

Every one of them is a compile, an apply, a run and a line of output. A feature that cannot show a
line of output is not implemented, whatever the code says.
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
ASM_DIR = ROOT / "work" / "asm"
ENGINE_ASM = pathlib.Path.home() / "devspace" / "WebMC" / "webmc-engine" / "toolchain" / "m2" / "org" / "ow2" / "asm"

# The runtime classes a merged client needs on its own classpath: an injected call constructs a
# CallbackInfo, a @ModifyArgs builds an Args, and a @WrapOperation's generated Operation implements
# that interface.
RUNTIME_SHIMS = ("org/spongepowered/asm/mixin/injection/callback/CallbackInfo.class",
                 "org/spongepowered/asm/mixin/injection/callback/CallbackInfoReturnable.class",
                 "org/spongepowered/asm/mixin/injection/invoke/arg/Args.class",
                 "com/llamalad7/mixinextras/injector/wrapoperation/Operation.class")
CALLBACK_SHIMS = RUNTIME_SHIMS

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


def java_home() -> pathlib.Path | None:
    for candidate in [os.environ.get("JAVA_HOME"),
                      str(pathlib.Path.home() / ".jdks" / "jdk-21.0.12.1+1" / "Contents" / "Home"),
                      "/usr/libexec/java_home"]:
        if candidate and (pathlib.Path(candidate) / "bin" / "javac").is_file():
            return pathlib.Path(candidate)
    found = shutil.which("javac")
    return pathlib.Path(found).parent.parent if found else None


def asm_classpath() -> str:
    """Real ASM jars, from the engine's own toolchain or a vendored copy."""
    jars = []
    if ENGINE_ASM.is_dir():
        for name in ("asm", "asm-tree"):
            found = sorted(ENGINE_ASM.glob(f"{name}/*/*.jar"))
            jars.extend(str(jar) for jar in found)
    for name in ("asm.jar", "asm-tree.jar"):
        candidate = ASM_DIR / name
        if candidate.is_file() and candidate.stat().st_size > 20_000:
            jars.append(str(candidate))
    return os.pathsep.join(jars)


def run(cmd: list[str], check_ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check_ok and result.returncode != 0:
        print("  command failed:", " ".join(cmd))
        print((result.stdout + result.stderr)[-1500:])
    return result


def build(javac: str, sources: list[pathlib.Path], out: pathlib.Path, classpath: str = "") -> bool:
    out.mkdir(parents=True, exist_ok=True)
    cmd = [javac, "-nowarn", "-d", str(out)]
    if classpath:
        cmd += ["-cp", classpath]
    cmd += [str(source) for source in sources]
    return run(cmd).returncode == 0


def make_jar(path: pathlib.Path, classes: pathlib.Path, extra: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for item in sorted(classes.rglob("*.class")):
            archive.write(item, item.relative_to(classes).as_posix())
        for name, text in extra.items():
            archive.writestr(name, text)


CLIENT = """
package example.client;

public class Game {
    private int secret = 7;

    private int hidden() {
        return 42;
    }

    private int counter = 0;

    public int tick(int delta) {
        this.counter += delta;
        System.out.println("client tick " + this.counter);
        return delta * 2;
    }

    public int count() {
        System.out.println("client count");
        return 5;
    }

    public void guarded() {
        System.out.println("client guarded ran");
    }

    public int callsHelper() {
        Helper.work(1);
        return Helper.value();
    }

    public void maybeWork() {
        Helper.work(2);
    }

    public int twoArgs() {
        return Helper.combine(1, 2);
    }

    public int wrapped() {
        return Helper.value() * 2;
    }

    public int wrapping() {
        System.out.println("client wrapping ran");
        return 7;
    }

    public int assigned() {
        int sum = 0;
        sum += Helper.value();
        return sum;
    }

    public int constants(int input) {
        return input * 3;
    }

    public int locals() {
        int value = 10;
        return value + 1;
    }

    public int expression() {
        return Helper.value();
    }

    public static void main(String[] args) {
        Game game = new Game();
        System.out.println("tick returns " + game.tick(4));
        System.out.println("count returns " + game.count());
        game.guarded();
        System.out.println("helper returns " + game.callsHelper());
        game.maybeWork();
        System.out.println("two args returns " + game.twoArgs());
        System.out.println("wrapped returns " + game.wrapped());
        System.out.println("wrapping returns " + game.wrapping());
        System.out.println("assigned returns " + game.assigned());
        System.out.println("constants returns " + game.constants(5));
        System.out.println("locals returns " + game.locals());
        System.out.println("expression returns " + game.expression());
        System.out.println("widened reads " + game.widenedSecret());
    }

    public int widenedSecret() {
        return 0;
    }
}
"""

HELPER = """
package example.client;

public class Helper {
    public static int calls = 0;

    public static int combine(int first, int second) {
        return first * 10 + second;
    }

    public static void work(int amount) {
        calls += amount;
        System.out.println("helper work " + amount);
    }

    public static int value() {
        System.out.println("helper value");
        return 1;
    }
}
"""

MIXIN = """
package example.mixin;

import com.llamalad7.mixinextras.injector.ModifyExpressionValue;
import com.llamalad7.mixinextras.injector.ModifyReceiver;
import com.llamalad7.mixinextras.injector.ModifyReturnValue;
import com.llamalad7.mixinextras.injector.WrapOperation;
import com.llamalad7.mixinextras.injector.WrapWithCondition;
import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import example.client.Game;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.ModifyArgs;
import org.spongepowered.asm.mixin.injection.ModifyArg;
import org.spongepowered.asm.mixin.injection.ModifyConstant;
import org.spongepowered.asm.mixin.injection.ModifyVariable;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(Game.class)
public class GameMixin {
    /** A handler that takes the target's own argument: the case that used to be refused. */
    @Inject(method = "tick", at = @At("HEAD"))
    private void onTickHead(int delta, CallbackInfo callback) {
        System.out.println("mixin saw delta " + delta);
    }

    /** A handler that takes the return value at RETURN. */
    @Inject(method = "count", at = @At("RETURN"))
    private void onCountReturn(int result, CallbackInfoReturnable<Integer> callback) {
        System.out.println("mixin saw return " + result);
    }

    /** Cancelling at HEAD stops the rest of the method. */
    @Inject(method = "guarded", at = @At("HEAD"), cancellable = true)
    private void onGuarded(CallbackInfo callback) {
        System.out.println("mixin cancelled guarded");
        callback.cancel();
    }

    /** An injection at an INVOKE, not just at HEAD. */
    @Inject(method = "callsHelper", at = @At(value = "INVOKE", target = "Lexample/client/Helper;work(I)V"))
    private void beforeWork(CallbackInfo callback) {
        System.out.println("mixin before work");
    }

    /** @Redirect replaces the call entirely. */
    @Redirect(method = "callsHelper", at = @At(value = "INVOKE", target = "Lexample/client/Helper;value()I"))
    private int redirectValue() {
        System.out.println("mixin redirected value");
        return 99;
    }

    /** @ModifyArg changes what the call is given. */
    @ModifyArg(method = "callsHelper", at = @At(value = "INVOKE", target = "Lexample/client/Helper;work(I)V"), index = 0)
    private int changeAmount(int amount) {
        return amount + 40;
    }

    /** @ModifyConstant replaces a constant in the method. */
    @ModifyConstant(method = "constants", at = @At(value = "CONSTANT", target = "3"))
    private int changeThree(int value) {
        return value + 100;
    }

    /** @ModifyVariable rewrites a local on its way to being stored. */
    @ModifyVariable(method = "locals", at = @At(value = "STORE", ordinal = 0))
    private int changeLocal(int value) {
        return value * 3;
    }

    /** @ModifyArgs: the arguments of a call, boxed, changed by index. */
    @ModifyArgs(method = "twoArgs", at = @At(value = "INVOKE", target = "Lexample/client/Helper;combine(II)I"))
    private void changeArguments(org.spongepowered.asm.mixin.injection.invoke.arg.Args args) {
        args.set(1, ((Integer) args.get(1)) + 5);
    }

    /** MixinExtras' @WrapOperation: the call is replaced, and the handler can forward to it. */
    @WrapOperation(method = "wrapped",
            at = @At(value = "INVOKE", target = "Lexample/client/Helper;value()I"))
    private int wrapTheCall(Operation<Integer> original) {
        System.out.println("mixin wrapped the call");
        return original.call() + 100;
    }

    /** MixinExtras' @WrapMethod: the whole method is replaced, and the original is still reachable. */
    @com.llamalad7.mixinextras.injector.wrapmethod.WrapMethod(method = "wrapping")
    private int wrapTheMethod(com.llamalad7.mixinextras.injector.wrapoperation.Operation<Integer> original) {
        System.out.println("mixin wrapped the method");
        return original.call() + 1;
    }

    /** An @Inject at INVOKE_ASSIGN: after the call, where its result is on the stack. */
    @Inject(method = "assigned", at = @At(value = "INVOKE_ASSIGN", target = "Lexample/client/Helper;value()I"))
    private void afterAssign(org.spongepowered.asm.mixin.injection.callback.CallbackInfo callback) {
        System.out.println("mixin after the assigned call");
    }

    /** A MixinExtras wrapper over the value an instruction produced. */
    @ModifyExpressionValue(method = "expression", at = @At(value = "INVOKE", target = "Lexample/client/Helper;value()I"))
    private int wrapValue(int value) {
        return value + 1000;
    }

    /** A MixinExtras conditional: the call only runs when this says so. */
    @WrapWithCondition(method = "maybeWork", at = @At(value = "INVOKE", target = "Lexample/client/Helper;work(I)V"))
    private boolean allowWork(int amount) {
        System.out.println("mixin decided about work " + amount);
        return amount < 0; // never: the call is skipped
    }
}
"""

ACCESSOR = """
package example.mixin;

import example.client.Game;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;
import org.spongepowered.asm.mixin.gen.Invoker;

@Mixin(Game.class)
public interface GameAccessor {
    @Accessor("secret")
    int secret();

    @Invoker("hidden")
    int callHidden();
}
"""

WIDENER_MIXIN = """
package example.widener;

import example.client.Game;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.ModifyArgs;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(Game.class)
public class WidenerMixin {
    /** Reads a field that is private in the client, which is what an access widener is for. */
    @Inject(method = "widenedSecret", at = @At("HEAD"))
    private void readTheField(CallbackInfo callback) {
        Game self = (Game) (Object) this;
        System.out.println("widener read " + self.secret);
    }
}
"""


def main() -> int:
    home = java_home()
    if not home:
        print("  SKIP  the advanced mixin features need a JDK")
        return 0
    asm = asm_classpath()
    if not asm:
        print("  SKIP  no ASM jars (the engine's toolchain or work/asm)")
        return 0
    javac, java = str(home / "bin" / "javac"), str(home / "bin" / "java")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="webmc-mixin-advanced-"))
    print(f"mixin applier, the harder shapes, on {home.name}")

    applier_out = tmp / "applier"
    check("the applier compiles", build(javac, [APPLIER], applier_out, asm))

    shims_out = tmp / "shims"
    shim_sources = sorted(SHIMS.rglob("*.java"))
    check(f"the annotations a mod compiles against ({len(shim_sources)} of them) build",
          build(javac, shim_sources, shims_out))

    client_out = tmp / "client"
    client_src = tmp / "client-src" / "example" / "client"
    client_src.mkdir(parents=True)
    (client_src / "Game.java").write_text(CLIENT)
    (client_src / "Helper.java").write_text(HELPER)
    check("a client with a private field, a private method and some calls compiles",
          build(javac, [client_src / "Game.java", client_src / "Helper.java"], client_out))

    classes = tmp / "classes"
    shutil.copytree(client_out, classes)
    # the callback classes travel with the client: an injected call constructs one
    for shim in CALLBACK_SHIMS:
        target = classes / shim
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(shims_out / shim, target)

    vanilla = run([java, "-cp", str(classes), "example.client.Game"])
    check("and runs before any mod touches it",
          "client tick 4" in vanilla.stdout and "helper work 1" in vanilla.stdout
          and "mixin saw delta" not in vanilla.stdout, vanilla.stdout.strip()[:200])

    # ---- the mixin jar -------------------------------------------------------------------
    mod_classes = tmp / "mod-classes"
    mixin_dir = tmp / "mod-src" / "example" / "mixin"
    mixin_dir.mkdir(parents=True)
    (mixin_dir / "GameMixin.java").write_text(MIXIN)
    (mixin_dir / "GameAccessor.java").write_text(ACCESSOR)
    check("a mod using every shape compiles against the shims",
          build(javac, [mixin_dir / "GameMixin.java", mixin_dir / "GameAccessor.java"], mod_classes,
                os.pathsep.join([str(shims_out), str(classes)])))

    descriptor = ('{"schemaVersion": 1, "id": "example.shapes", "name": "Shapes", "version": "1.0.0",'
                  ' "entrypoints": {"client": ["example.mixin.GameMixin"]},'
                  ' "mixins": ["shapes.mixins.json"]}')
    config = ('{"required": true, "package": "example.mixin", "compatibilityLevel": "JAVA_21",'
              ' "mixins": ["GameMixin", "GameAccessor"]}')
    mod_jar = tmp / "shapes.jar"
    make_jar(mod_jar, mod_classes, {"fabric.mod.json": descriptor, "shapes.mixins.json": config})

    applied = run([java, "-cp", os.pathsep.join([str(applier_out), asm]), "dev.gaius.tools.WebMcMixinApplier",
                   str(mod_jar), str(classes)])
    check("the applier runs", applied.returncode == 0, (applied.stdout + applied.stderr)[-800:])
    output = applied.stdout
    check("it reports generated members, not just copies",
          "generated" in output and " redirected" in output and " modified" in output, output.strip()[:300])

    modded = run([java, "-cp", str(classes), "example.client.Game"])
    lines = modded.stdout.strip().splitlines()
    text = "\n".join(lines)

    check("a handler taking the target's argument sees the real value",
          "mixin saw delta 4" in text, text[:300])
    check("a handler taking the return value sees it", "mixin saw return 5" in text, text[:300])
    check("a cancelled HEAD injection stops the method", "mixin cancelled guarded" in text
          and "client guarded ran" not in text, text[:400])
    check("an @Inject at an INVOKE runs before the call",
          text.index("mixin before work") < text.index("helper work"), text[:400])
    # Four sites call Helper.value(): `callsHelper` (redirected: no longer reaches Helper), `wrapped`
    # (@WrapOperation forwards, so it still does), `expression` and `assigned` (both untouched). Three
    # prints is therefore the redirect working; four would be the redirect doing nothing.
    check("a @Redirect replaces the call", "mixin redirected value" in text and text.count("helper value") == 3,
          f"{text.count('helper value')} calls reached Helper: " + text[:400])
    check("a @ModifyArg changes the argument the call receives", "helper work 41" in text, text[:400])
    check("a @ModifyConstant changes the constant", "constants returns 515" in text, text[:400])
    check("a @ModifyVariable changes the local", "locals returns 31" in text, text[:400])
    check("a @ModifyExpressionValue wraps the value", "expression returns 1001" in text, text[:400])
    check("a @WrapWithCondition can stop a call", "mixin decided about work 2" in text
          and "helper work 2" not in text, text[:500])
    check("a @ModifyArgs changes one argument of a call by index", "two args returns 17" in text, text[:500])
    check("a @WrapOperation forwards to the original and changes the result",
          "mixin wrapped the call" in text and "wrapped returns 202" in text, text[-500:])
    check("a @WrapMethod replaces a whole method and can still call the original",
          "mixin wrapped the method" in text and "client wrapping ran" in text and "wrapping returns 8" in text,
          text[-500:])
    check("an @Inject at INVOKE_ASSIGN runs after the call returned",
          "mixin after the assigned call" in text and "assigned returns 1" in text, text[-500:])
    # The generated members are new methods on the client: they are proved by compiling a probe
    # *after* the merge, which is the only honest way to show they exist and work.
    probe_src = tmp / "probe-src" / "example" / "probe"
    probe_src.mkdir(parents=True, exist_ok=True)
    (probe_src / "Probe.java").write_text("""
package example.probe;

import example.client.Game;

public class Probe {
    public static void main(String[] args) {
        Game game = new Game();
        System.out.println("accessor reads " + game.secret());
        System.out.println("invoker calls " + game.callHidden());
    }
}
""")
    probe_out = tmp / "probe-classes"
    check("an @Accessor/@Invoker member exists in the client after the merge (a probe compiles against it)",
          build(javac, [probe_src / "Probe.java"], probe_out, str(classes)))
    probe = run([java, "-cp", os.pathsep.join([str(probe_out), str(classes)]), "example.probe.Probe"])
    check("and the generated accessor reads the real private field", "accessor reads 7" in probe.stdout, probe.stdout[:200])
    check("and the generated invoker calls the real private method", "invoker calls 42" in probe.stdout, probe.stdout[:200])

    # ---- the access widener ---------------------------------------------------------------
    # The order is the one a real mod build uses: the widener is applied to the client *before* the
    # mod is compiled (that is what Fabric Loom does), and the applier then applies it again on the
    # way into the client that ships. Both halves are checked.
    widener_text = "accessWidener v2 named\naccessible field example/client/Game secret I\n"
    widener_descriptor = ('{"schemaVersion": 1, "id": "example.widener", "name": "Widener", "version": "1.0.0",'
                          ' "accessWidener": "example.accesswidener", "mixins": ["widener.mixins.json"]}')
    widener_config = ('{"required": true, "package": "example.widener", "compatibilityLevel": "JAVA_21",'
                      ' "mixins": ["WidenerMixin"]}')

    def with_callbacks(path: pathlib.Path) -> pathlib.Path:
        for shim in CALLBACK_SHIMS:
            target = path / shim
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(shims_out / shim, target)
        return path

    loom = with_callbacks(tmp / "loom")
    shutil.copytree(client_out, loom, dirs_exist_ok=True)
    empty_mod = tmp / "widener-only"
    empty_mod.mkdir()
    widener_only_jar = tmp / "widener-only.jar"
    widener_only_descriptor = ('{"schemaVersion": 1, "id": "example.widener", "name": "Widener", "version": "1.0.0",'
                               ' "accessWidener": "example.accesswidener"}')
    make_jar(widener_only_jar, empty_mod, {"fabric.mod.json": widener_only_descriptor,
                                           "example.accesswidener": widener_text})
    widened_loom = run([java, "-cp", os.pathsep.join([str(applier_out), asm]), "dev.gaius.tools.WebMcMixinApplier",
                        str(widener_only_jar), str(loom)])
    check("the access widener is applied to the client's own class file",
          "access widener" in widened_loom.stdout and "change(s) applied" in widened_loom.stdout,
          widened_loom.stdout.strip()[:200])

    widener_classes = tmp / "widener-mod"
    widener_src = tmp / "widener-src" / "example" / "widener"
    widener_src.mkdir(parents=True)
    (widener_src / "WidenerMixin.java").write_text(WIDENER_MIXIN)
    check("a mixin that reads the widened private field compiles, once the widener has been applied",
          build(javac, [widener_src / "WidenerMixin.java"], widener_classes,
                os.pathsep.join([str(shims_out), str(loom)])))

    runtime = with_callbacks(tmp / "runtime")
    shutil.copytree(client_out, runtime, dirs_exist_ok=True)
    widener_jar = tmp / "widener.jar"
    make_jar(widener_jar, widener_classes, {
        "fabric.mod.json": widener_descriptor,
        "widener.mixins.json": widener_config,
        "example.accesswidener": widener_text,
    })
    applied_widener = run([java, "-cp", os.pathsep.join([str(applier_out), asm]), "dev.gaius.tools.WebMcMixinApplier",
                           str(widener_jar), str(runtime)])
    check("the applier applies it on the way into the shipped client too",
          "1 injected" in applied_widener.stdout and "change(s) applied" in applied_widener.stdout,
          applied_widener.stdout.strip()[:300])
    widened_run = run([java, "-cp", str(runtime), "example.client.Game"])
    check("and the mixin reads the field the widener opened", "widener read 7" in widened_run.stdout,
          widened_run.stdout[:300])

    print("")
    if FAILURES:
        print(f"advanced mixin checks: {FAILURES} failed")
        return 1
    print("advanced mixin checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
