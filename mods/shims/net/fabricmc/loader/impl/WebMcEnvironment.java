package net.fabricmc.loader.impl;

/**
 * The two facts the loader needs that only the bake knows: which Minecraft version this client is,
 * and what its root "directory" is called.
 *
 * The bake writes them into this class' constants, so nothing here guesses.
 */
public final class WebMcEnvironment {
    private static String version = "26.2";
    private static String root = "/";

    private WebMcEnvironment() {
    }

    public static void configure(String minecraftVersion) {
        if (minecraftVersion != null && !minecraftVersion.isBlank()) {
            version = minecraftVersion;
        }
    }

    public static String gameVersion() {
        return version;
    }

    public static String root() {
        return root;
    }
}
