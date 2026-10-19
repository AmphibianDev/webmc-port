package com.example.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * The smallest possible mixin mod: it changes a client it has never seen, at build time.
 *
 * <p>Two decisions worth copying. It names its target with {@code targets = "..."} rather than
 * {@code @Mixin(Minecraft.class)}, so it compiles without Minecraft on the classpath - which is what lets this
 * repository build it without any of Mojang's files. And it injects at {@code HEAD} of the client's constructor
 * with a {@code (CallbackInfo)} handler, which is the shape the build-time applier implements exactly; see
 * MIXINS.md for the list of shapes and what happens to the rest.</p>
 */
@Mixin(targets = "net.minecraft.client.Minecraft")
public class HelloMixin {

    @Inject(method = "<init>", at = @At("HEAD"))
    private void webmcHello(CallbackInfo callback) {
        System.out.println("[hello-mixin] this line is not in Minecraft: a mixin was merged into the client "
                + "before it was compiled");
    }
}
