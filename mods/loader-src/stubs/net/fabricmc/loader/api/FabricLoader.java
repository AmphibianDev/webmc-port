package net.fabricmc.loader.api;

import java.util.List;
import net.fabricmc.eaglerbridge.EaglerFabricLoader;

/**
 * The Fabric loader API, answered from the build plan instead of from a mod folder.
 *
 * A browser client was built with a fixed mod set, so "is this mod loaded?" has a
 * definite answer that the loader already knows: it is the list of jars that were baked
 * in. Mods that guard optional integrations with isModLoaded() therefore behave the way
 * they do on the desktop - the integration is off because the mod really is not there.
 *
 * Only the queries a baked mod can meaningfully make are implemented. Anything that
 * needs a mod's jar (its metadata, its file paths) does not exist in an AOT client and
 * returns an empty result rather than pretending.
 */
public final class FabricLoader {

    private static final FabricLoader INSTANCE = new FabricLoader();

    private FabricLoader() {
    }

    public static FabricLoader getInstance() {
        return INSTANCE;
    }

    public boolean isModLoaded(String id) {
        return EaglerFabricLoader.isLoaded(id) || "minecraft".equals(id)
                || "java".equals(id) || "webmc-loader".equals(id);
    }

    /** The ids this client was built with, in load order. */
    public List<String> loadedModIds() {
        return EaglerFabricLoader.loadedMods();
    }

    public boolean isDevelopmentEnvironment() {
        return false;
    }
}
