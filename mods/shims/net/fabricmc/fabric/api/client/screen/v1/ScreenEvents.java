package net.fabricmc.fabric.api.client.screen.v1;

import net.fabricmc.fabric.api.event.AbstractEvent;
import net.fabricmc.fabric.api.event.Event;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;

/** Fires after a screen has initialised. Hooked at the end of Screen.init(). */
public final class ScreenEvents {
    private ScreenEvents() {
    }

    @FunctionalInterface
    public interface AfterInit {
        void afterInit(Minecraft client, Screen screen, int scaledWidth, int scaledHeight);
    }

    private static final class Impl extends AbstractEvent<AfterInit> implements AfterInit {
        @Override
        public AfterInit invoker() {
            return this;
        }

        @Override
        public void afterInit(Minecraft client, Screen screen, int scaledWidth, int scaledHeight) {
            for (AfterInit listener : listeners()) {
                listener.afterInit(client, screen, scaledWidth, scaledHeight);
            }
        }
    }

    public static final Event<AfterInit> AFTER_INIT = new Impl();

    /** Called by the client hook. */
    public static void eaglerFireAfterInit(Minecraft client, Screen screen, int scaledWidth, int scaledHeight) {
        ((Impl) AFTER_INIT).afterInit(client, screen, scaledWidth, scaledHeight);
    }
}
