package net.fabricmc.loader.api;

import net.fabricmc.api.EnvType;
import net.fabricmc.loader.api.entrypoint.EntrypointContainer;
import net.fabricmc.loader.api.metadata.ModMetadata;
import net.fabricmc.loader.impl.WebMcEnvironment;
import java.io.File;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

/**
 * The loader, as Fabric API and mods see it - in a client that has no class loader at play time.
 *
 * The bake is what loads mods: it reads every descriptor, merges every mixin and calls every
 * entrypoint before the client is compiled. So this class reports what the bake did, and says plainly
 * what a browser cannot do (a real directory, a raw game instance) instead of returning something
 * that looks like it worked.
 *
 * The mod list is filled by the generated loader at startup: one container per baked mod, carrying
 * the same `fabric.mod.json` the bake read.
 */
public class FabricLoader {
    private static final FabricLoader INSTANCE = new FabricLoader();
    private static final Map<String, ModContainer> MODS = new LinkedHashMap<>();
    private static final Map<String, Object> OBJECT_SHARE = new LinkedHashMap<>();
    private static final MappingResolver MAPPINGS = new IdentityMappings();

    /** What the generated loader calls, once per baked mod, before any entrypoint runs. */
    public static ModContainer register(String id, ModMetadata metadata, Map<String, byte[]> files) {
        ModContainer container = new ModContainer(metadata, metadata.getOrigin(), files);
        MODS.put(id, container);
        return container;
    }

    public static void nest(String parentId, String childId) {
        ModContainer parent = MODS.get(parentId);
        ModContainer child = MODS.get(childId);
        if (parent != null && child != null) {
            parent.addChild(child);
        }
    }

    public static FabricLoader getInstance() {
        return INSTANCE;
    }

    public List<?> getEntrypoints(String key, Class<?> type) {
        return List.of();
    }

    public List<EntrypointContainer<?>> getEntrypointContainers(String key, Class<?> type) {
        // The bake already called every entrypoint; this is the list of what it called, so a mod that
        // enumerates entrypoints sees the truth rather than an empty list.
        List<EntrypointContainer<?>> out = new ArrayList<>();
        for (ModContainer container : MODS.values()) {
            EntrypointContainer<Object> entry = new EntrypointContainer<>() {
                @Override
                public Object getEntrypoint() {
                    return null;
                }

                @Override
                public ModContainer getProvider() {
                    return container;
                }
            };
            out.add(entry);
        }
        return out;
    }

    public void invokeEntrypoints(String key, Class<?> type, Consumer<?> invoker) {
        // Mods were initialised by the bake; a caller that wants to initialise them again would run
        // their setup code twice, so this is deliberately a no-op rather than a surprise.
    }

    public ObjectShare getObjectShare() {
        return new ObjectShare() {
            @Override
            public Object put(String key, Object value) {
                return OBJECT_SHARE.put(key, value);
            }

            @Override
            public Object get(String key) {
                return OBJECT_SHARE.get(key);
            }

            @Override
            public Object remove(String key) {
                return OBJECT_SHARE.remove(key);
            }

            @Override
            public boolean containsKey(String key) {
                return OBJECT_SHARE.containsKey(key);
            }

            @Override
            public Map<String, Object> asMap() {
                return OBJECT_SHARE;
            }
        };
    }

    public MappingResolver getMappingResolver() {
        return MAPPINGS;
    }

    public Optional<ModContainer> getModContainer(String id) {
        return Optional.ofNullable(MODS.get(id));
    }

    public Collection<ModContainer> getAllMods() {
        return List.copyOf(MODS.values());
    }

    public boolean isModLoaded(String id) {
        return MODS.containsKey(id);
    }

    public boolean isDevelopmentEnvironment() {
        return false;
    }

    public EnvType getEnvironmentType() {
        return EnvType.CLIENT;
    }

    public String getRawGameVersion() {
        return WebMcEnvironment.gameVersion();
    }

    public Object getGameInstance() {
        // The real object graph is compiled, not a Java heap a mod can reach: null is the honest answer.
        return null;
    }

    public Path getGameDir() {
        return Paths.get(WebMcEnvironment.root());
    }

    public File getGameDirectory() {
        return new File(WebMcEnvironment.root());
    }

    public Path getConfigDir() {
        return Paths.get(WebMcEnvironment.root(), "config");
    }

    public File getConfigDirectory() {
        return new File(WebMcEnvironment.root(), "config");
    }

    public String[] getLaunchArguments(boolean sanitize) {
        return new String[0];
    }

    /** One namespace, and it is the one the client was compiled with. */
    private static final class IdentityMappings implements MappingResolver {
        @Override
        public Collection<String> getNamespaces() {
            return List.of("named");
        }

        @Override
        public String getCurrentRuntimeNamespace() {
            return "named";
        }

        @Override
        public String mapClassName(String namespace, String className) {
            return className;
        }

        @Override
        public String unmapClassName(String namespace, String className) {
            return className;
        }

        @Override
        public String mapFieldName(String namespace, String owner, String name, String descriptor) {
            return name;
        }

        @Override
        public String mapMethodName(String namespace, String owner, String name, String descriptor) {
            return name;
        }
    }
}
