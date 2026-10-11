package net.fabricmc.fabric.api.client.event.lifecycle.v1;

import net.fabricmc.fabric.api.event.AbstractEvent;
import net.fabricmc.fabric.api.event.Event;
import net.minecraft.client.Minecraft;

/** Fires once per client tick. Hooked at the end of Minecraft.tick(). */
public final class ClientTickEvents {
    private ClientTickEvents() {
    }

    @FunctionalInterface
    public interface EndTick {
        void onEndTick(Minecraft client);
    }

    private static final class Impl extends AbstractEvent<EndTick> implements EndTick {
        @Override
        public EndTick invoker() {
            return this;
        }

        @Override
        public void onEndTick(Minecraft client) {
            for (EndTick listener : listeners()) {
                listener.onEndTick(client);
            }
        }
    }

    public static final Event<EndTick> END_CLIENT_TICK = new Impl();

    /** Called by the client hook. */
    public static void eaglerFireEndTick(Minecraft client) {
        ((Impl) END_CLIENT_TICK).onEndTick(client);
    }
}
