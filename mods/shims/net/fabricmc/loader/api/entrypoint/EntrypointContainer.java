package net.fabricmc.loader.api.entrypoint;

import net.fabricmc.loader.api.ModContainer;

/** One entrypoint, and the mod it belongs to. */
public interface EntrypointContainer<T> {
    T getEntrypoint();

    ModContainer getProvider();
}
