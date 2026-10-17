package com.example;

import net.fabricmc.api.ClientModInitializer;

/**
 * A Fabric mod, written to be baked into a compiled client.
 *
 * This is the shape that works on both rows of the launcher, which is why it exists:
 *
 *   * on a version with a real JVM in the page (1.0 - 1.12.2 today) Fabric Loader starts it at play
 *     time, and this class runs as it would on the desktop;
 *   * on a compiled version (1.13 and later) there is no class loader at play time, so the bake in
 *     mods/build_modded_workspace.py reads this jar's fabric.mod.json, prunes the mixins whose targets
 *     are not in that client, copies the class into the client's source tree and generates the call to
 *     {@link #onInitializeClient()} that the compiled client makes at startup.
 *
 * It implements ClientModInitializer from mods/shims, which is the same interface Fabric provides: a mod
 * written against it needs no Minecraft classes to load, which is also what makes it testable here.
 */
public final class HelloFabricMod implements ClientModInitializer {

    public void onInitializeClient() {
        System.out.println("[webmc] hello-fabric: a Fabric mod, baked into this client");
    }
}
