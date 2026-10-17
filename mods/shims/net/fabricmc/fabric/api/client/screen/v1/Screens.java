package net.fabricmc.fabric.api.client.screen.v1;

import java.util.ArrayList;
import java.util.List;
import net.minecraft.client.gui.components.Renderable;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.Screen;

public final class Screens {
    private Screens() {
    }

    /**
     * The screen's renderable children.
     *
     * The ported 26.3 client exposes its own `eaglerGetRenderables()`, and 26.2 does not - so the
     * shim uses the vanilla accessor both versions keep (`Screen.children()`), filtered to what is
     * renderable. A shim that only compiles for one version is a shim that breaks the other one's
     * build, which is exactly what happened when this was written the other way.
     */
    public static List<Renderable> getWidgets(Screen screen) {
        List<Renderable> out = new ArrayList<>();
        for (GuiEventListener listener : screen.children()) {
            if (listener instanceof Renderable renderable) {
                out.add(renderable);
            }
        }
        return out;
    }
}
