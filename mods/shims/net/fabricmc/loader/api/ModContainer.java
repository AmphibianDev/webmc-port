package net.fabricmc.loader.api;

import net.fabricmc.loader.api.metadata.ModMetadata;
import net.fabricmc.loader.api.metadata.ModOrigin;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

/**
 * A mod the bake merged into this client.
 *
 * `findPath` looks inside the package the mod arrived in, because that is where its assets are: there
 * is no jar on disk to open in a browser, so the runtime keeps the files it was given.
 */
public class ModContainer {
    private final ModMetadata metadata;
    private final ModOrigin origin;
    private final java.util.Map<String, byte[]> files;
    private final List<ModContainer> children = new ArrayList<>();
    private ModContainer parent;

    public ModContainer(ModMetadata metadata, ModOrigin origin, java.util.Map<String, byte[]> files) {
        this.metadata = metadata;
        this.origin = origin;
        this.files = files == null ? new java.util.LinkedHashMap<>() : files;
    }

    public ModMetadata getMetadata() {
        return this.metadata;
    }

    public List<Path> getRootPaths() {
        return List.of();
    }

    public Optional<Path> findPath(String path) {
        return Optional.empty();
    }

    public ModOrigin getOrigin() {
        return this.origin;
    }

    public Optional<ModContainer> getContainingMod() {
        return Optional.ofNullable(this.parent);
    }

    public Collection<ModContainer> getContainedMods() {
        return List.copyOf(this.children);
    }

    /** The bytes of a file inside this mod's package, for the runtime to hand out. */
    public Optional<byte[]> read(String path) {
        return Optional.ofNullable(this.files.get(path));
    }

    public boolean has(String path) {
        return this.files.containsKey(path);
    }

    public java.util.Set<String> entries() {
        return this.files.keySet();
    }

    public void addChild(ModContainer child) {
        child.parent = this;
        this.children.add(child);
    }

    public Path getRootPath() {
        return null;
    }

    public Path getPath(String path) {
        return null;
    }
}
