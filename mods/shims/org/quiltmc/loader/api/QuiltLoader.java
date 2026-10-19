package org.quiltmc.loader.api;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

/**
 * Quilt's loader, as a Fabric build sees it: a compatibility check.
 *
 * A client baked by this project is not a Quilt runtime, and saying so is the honest answer - a mod that
 * looks for Quilt finds nothing, exactly as it would on a Fabric-only install.
 */
public final class QuiltLoader {
    private QuiltLoader() {
    }

    public static boolean isModLoaded(String id) {
        return false;
    }

    public static Optional<Object> getModContainer(String id) {
        return Optional.empty();
    }

    public static Collection<Object> getAllMods() {
        return List.of();
    }

    public static org.quiltmc.loader.api.metadata.ModMetadata getModMetadata(String id) {
        return null;
    }
}
