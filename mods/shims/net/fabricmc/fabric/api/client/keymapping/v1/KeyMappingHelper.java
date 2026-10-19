package net.fabricmc.fabric.api.client.keymapping.v1;

import net.minecraft.client.KeyMapping;

/**
 * On the ported client a KeyMapping already registers itself in its constructor
 * (KeyMapping.java puts it into ALL and MAP), which is exactly what Fabric's helper
 * exists to force. So this is a no-op that returns the mapping, and needs no game hook.
 */
public final class KeyMappingHelper {
    private KeyMappingHelper() {
    }

    public static KeyMapping registerKeyMapping(KeyMapping mapping) {
        if (mapping == null) {
            throw new NullPointerException("mapping");
        }
        return mapping;
    }
}
