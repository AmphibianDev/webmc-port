package net.fabricmc.loader.api.metadata;

import net.fabricmc.api.EnvType;

/**
 * Which side a mod says it belongs to.
 *
 * A client build only ever loads CLIENT and UNIVERSAL mods; a mod marked SERVER is reported as not being
 * for this side rather than quietly half-loaded.
 */
public enum ModEnvironment {
    UNIVERSAL,
    CLIENT,
    SERVER;

    public boolean matches(EnvType environment) {
        return switch (this) {
            case UNIVERSAL -> true;
            case CLIENT -> environment == EnvType.CLIENT;
            case SERVER -> environment == EnvType.SERVER;
        };
    }
}
