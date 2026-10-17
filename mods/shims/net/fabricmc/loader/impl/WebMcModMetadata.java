package net.fabricmc.loader.impl;

import net.fabricmc.loader.api.Version;
import net.fabricmc.loader.api.metadata.ContactInformation;
import net.fabricmc.loader.api.metadata.CustomValue;
import net.fabricmc.loader.api.metadata.ModDependency;
import net.fabricmc.loader.api.metadata.ModEnvironment;
import net.fabricmc.loader.api.metadata.ModMetadata;
import net.fabricmc.loader.api.metadata.ModOrigin;
import net.fabricmc.loader.api.metadata.Person;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * A mod's metadata, taken straight from the `fabric.mod.json` the bake read.
 *
 * Nothing is invented and nothing is dropped: a field the descriptor did not have is empty here, and
 * a field it did have can be read by any mod that asks - which is what `getCustomValue` and the
 * dependency maps are for.
 */
public final class WebMcModMetadata implements ModMetadata {
    private final Map<String, Object> json;
    private final String id;

    public WebMcModMetadata(Map<String, Object> json) {
        this.json = json == null ? new LinkedHashMap<>() : json;
        this.id = string(this.json.get("id"), "unknown");
    }

    private static String string(Object value, String fallback) {
        return value == null ? fallback : String.valueOf(value);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(String key) {
        Object value = this.json.get(key);
        return value instanceof Map ? (Map<String, Object>) value : Map.of();
    }

    private Collection<String> list(String key) {
        Object value = this.json.get(key);
        List<String> out = new ArrayList<>();
        if (value instanceof List<?> items) {
            for (Object item : items) {
                out.add(String.valueOf(item));
            }
        } else if (value instanceof Map<?, ?> entries) {
            out.addAll(entries.keySet().stream().map(String::valueOf).toList());
        } else if (value != null) {
            out.add(String.valueOf(value));
        }
        return out;
    }

    @Override
    public String getType() {
        return "fabric";
    }

    @Override
    public String getId() {
        return this.id;
    }

    @Override
    public Collection<String> getProvides() {
        return list("provides");
    }

    @Override
    public Version getVersion() {
        return Version.parse(string(this.json.get("version"), "0.0.0"));
    }

    @Override
    public ModEnvironment getEnvironment() {
        Object value = this.json.get("environment");
        if (value == null) {
            return ModEnvironment.UNIVERSAL;
        }
        return switch (String.valueOf(value).toLowerCase()) {
            case "client" -> ModEnvironment.CLIENT;
            case "server" -> ModEnvironment.SERVER;
            default -> ModEnvironment.UNIVERSAL;
        };
    }

    private Collection<ModDependency> dependencies(String key, ModDependency.Kind kind) {
        List<ModDependency> out = new ArrayList<>();
        for (Map.Entry<String, Object> entry : map(key).entrySet()) {
            List<String> ranges = new ArrayList<>();
            if (entry.getValue() instanceof List<?> items) {
                for (Object item : items) {
                    ranges.add(String.valueOf(item));
                }
            } else {
                ranges.add(String.valueOf(entry.getValue()));
            }
            out.add(new WebMcModDependency(kind, entry.getKey(), ranges));
        }
        return out;
    }

    @Override
    public Collection<ModDependency> getDependencies() {
        List<ModDependency> all = new ArrayList<>();
        for (ModDependency.Kind kind : ModDependency.Kind.values()) {
            all.addAll(dependencies(kind.name().toLowerCase(), kind));
        }
        return all;
    }

    @Override
    public Collection<ModDependency> getDepends() {
        return dependencies("depends", ModDependency.Kind.DEPENDS);
    }

    @Override
    public Collection<ModDependency> getRecommends() {
        return dependencies("recommends", ModDependency.Kind.RECOMMENDS);
    }

    @Override
    public Collection<ModDependency> getSuggests() {
        return dependencies("suggests", ModDependency.Kind.SUGGESTS);
    }

    @Override
    public Collection<ModDependency> getConflicts() {
        return dependencies("conflicts", ModDependency.Kind.CONFLICTS);
    }

    @Override
    public Collection<ModDependency> getBreaks() {
        return dependencies("breaks", ModDependency.Kind.BREAKS);
    }

    @Override
    public ContactInformation getContact() {
        Map<String, String> out = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : map("contact").entrySet()) {
            out.put(entry.getKey(), String.valueOf(entry.getValue()));
        }
        return new ContactInformation(out);
    }

    @Override
    public String getDescription() {
        Object value = this.json.get("description");
        return value == null ? "" : String.valueOf(value);
    }

    private Collection<Person> people(String key) {
        List<Person> out = new ArrayList<>();
        Object value = this.json.get(key);
        if (value instanceof List<?> items) {
            for (Object item : items) {
                out.add(person(item));
            }
        } else if (value != null) {
            out.add(person(value));
        }
        return out;
    }

    private static Person person(Object value) {
        if (value instanceof Map<?, ?> map) {
            Object name = map.get("name");
            return new SimplePerson(name == null ? "unknown" : String.valueOf(name));
        }
        return new SimplePerson(String.valueOf(value));
    }

    @Override
    public Collection<Person> getAuthors() {
        return people("authors");
    }

    @Override
    public Collection<Person> getContributors() {
        return people("contributors");
    }

    @Override
    public Collection<Person> getLicense() {
        return list("license").stream().map(SimplePerson::new).map(person -> (Person) person).toList();
    }

    @Override
    public Optional<String> getIconPath(int size) {
        Object icon = this.json.get("icon");
        if (icon instanceof String path) {
            return Optional.of(path);
        }
        if (icon instanceof Map<?, ?> sizes) {
            for (Object value : sizes.values()) {
                return Optional.of(String.valueOf(value));
            }
        }
        return Optional.empty();
    }

    @Override
    public boolean containsCustomValue(String key) {
        return map("custom").containsKey(key);
    }

    @Override
    public CustomValue getCustomValue(String key) {
        Object value = map("custom").get(key);
        return value == null ? null : CustomValue.Impl.of(value);
    }

    @Override
    public boolean containsCustomElement(String key) {
        return containsCustomValue(key);
    }

    @Override
    public Map<String, CustomValue> getCustomValues() {
        Map<String, CustomValue> out = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : map("custom").entrySet()) {
            out.put(entry.getKey(), CustomValue.Impl.of(entry.getValue()));
        }
        return out;
    }

    @Override
    public String getName() {
        return string(this.json.get("name"), this.id);
    }

    private record SimplePerson(String name) implements Person {
        @Override
        public String getName() {
            return this.name;
        }

        @Override
        public ContactInformation getContact() {
            return new ContactInformation(Map.of());
        }
    }

    /** One declared dependency, with the version strings its descriptor gave. */
    private record WebMcModDependency(ModDependency.Kind getKind, String getModId, List<String> ranges)
            implements ModDependency {
        @Override
        public boolean matches(net.fabricmc.loader.api.Version version) {
            for (String range : this.ranges) {
                if (range.equals("*") || range.equals(version.getFriendlyString())) {
                    return true;
                }
            }
            return false;
        }

        @Override
        public Collection<?> getVersionRequirements() {
            return this.ranges;
        }

        @Override
        public List<?> getVersionIntervals() {
            return this.ranges;
        }
    }

    private enum ModOriginImpl implements ModOrigin {
        INSTANCE;

        @Override
        public Kind getKind() {
            return Kind.UNKNOWN;
        }

        @Override
        public List<String> getPaths() {
            return List.of();
        }
    }
}
