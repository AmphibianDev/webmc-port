package net.fabricmc.fabric.api.client.screen.v1;

import java.util.List;
import net.minecraft.client.gui.components.Renderable;
import net.minecraft.client.gui.screens.Screen;

public final class Screens {
    private Screens() {
    }

    /** The screen's renderable list, exposed by the Screen.  */
    public static List<Renderable> getWidgets(Screen screen) {
        return screen.eaglerGetRenderables();
    }
}
