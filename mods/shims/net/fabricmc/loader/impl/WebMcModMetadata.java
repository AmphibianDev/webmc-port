package net.fabricmc.loader.impl;

import net.fabricmc.loader.api.Version;
import net.fabricmc.loader.api.metadata.CustomValue;
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
    public ModOrigin getOrigin() {
        return ModOriginImpl.INSTANCE;
    }

    private Map<String, Collection<String>> dependencyMap(String key) {
        Map<String, Collection<String>> out = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : map(key).entrySet()) {
            Collection<String> ranges = new ArrayList<>();
            if (entry.getValue() instanceof List<?> items) {
                for (Object item : items) {
                    ranges.add(String.valueOf(item));
                }
            } else {
                ranges.add(String.valueOf(entry.getValue()));
            }
            out.put(entry.getKey(), ranges);
        }
        return out;
    }

    @Override
    public Map<String, Collection<String>> getDepends() {
        return dependencyMap("depends");
    }

    @Override
    public Map<String, Collection<String>> getRecommends() {
        return dependencyMap("recommends");
    }

    @Override
    public Map<String, Collection<String>> getSuggests() {
        return dependencyMap("suggests");
    }

    @Override
    public Map<String, Collection<String>> getConflicts() {
        return dependencyMap("conflicts");
    }

    @Override
    public Map<String, Collection<String>> getBreaks() {
        return dependencyMap("breaks");
    }

    @Override
    public Map<String, String> getContact() {
        Map<String, String> out = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : map("contact").entrySet()) {
            out.put(entry.getKey(), String.valueOf(entry.getValue()));
        }
        return out;
    }

    @Override
    public Optional<String> getDescription() {
        Object value = this.json.get("description");
        return value == null ? Optional.empty() : Optional.of(String.valueOf(value));
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
    public Collection<String> getIcon(int size) {
        Object icon = this.json.get("icon");
        if (icon instanceof String path) {
            return List.of(path);
        }
        if (icon instanceof Map<?, ?> sizes) {
            List<String> out = new ArrayList<>();
            for (Object value : sizes.values()) {
                out.add(String.valueOf(value));
            }
            return out;
        }
        return List.of();
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
        public String getContact(String key) {
            return null;
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
