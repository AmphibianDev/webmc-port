package webmc.loader;

import net.fabricmc.eaglerbridge.EaglerFabricLoader;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * The one line that makes a browser client run Fabric mods.
 *
 * A browser client has no class loader, so nothing scans mods at startup. This mixin is
 * applied by the real Fabric loader during the bake (when the modded classes are captured),
 * and what it leaves behind is a call to EaglerFabricLoader.ensureInitialized() inside the
 * client's own runTick(). Because the call is real bytecode, the ahead-of-time compiler can
 * see it, and the entrypoints behind it, and compiles all of them in.
 *
 * The target is written as a string so this class needs no game types to compile, and
 * `require` stays at its default of 1: if a version bump moves runTick, the bake stops
 * instead of silently shipping a client whose mods never start.
 */
@Mixin(targets = "net.minecraft.client.Minecraft")
public class WebMcLoaderMixin {

    @Inject(method = "runTick", at = @At("HEAD"))
    private void webmc$startMods(CallbackInfo callback) {
        EaglerFabricLoader.ensureInitialized();
    }
}
