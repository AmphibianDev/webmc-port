package net.eaglertest;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.Minecraft;

/**
 * Smoke test for the build-time loader: a real (if tiny) Fabric mod.
 * If the client console prints the tick line below, then the whole chain works:
 * shims compiled -> mod compiled -> entrypoint ran -> game hook fired.
 */
public class HelloMod implements ClientModInitializer {
    private int ticks;

    @Override
    public void onInitializeClient() {
        System.out.println("[eaglertest] hello from a Fabric mod inside a browser client");
        ClientTickEvents.END_CLIENT_TICK.register(this::onEndTick);
    }

    private void onEndTick(Minecraft client) {
        this.ticks++;
        if (this.ticks == 20 || this.ticks % 600 == 0) {
            System.out.println("[eaglertest] client tick " + this.ticks
                + " (screen: " + (client.screen == null ? "none" : client.screen.getClass().getSimpleName()) + ")");
        }
    }
}
