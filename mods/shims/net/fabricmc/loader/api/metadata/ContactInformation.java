package net.fabricmc.loader.api.metadata;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;

/** A mod's contact details, keyed the way its descriptor wrote them ("homepage", "issues", "sources"). */
public final class ContactInformation {
    private final Map<String, String> entries;

    public ContactInformation(Map<String, String> entries) {
        this.entries = entries == null ? new LinkedHashMap<>() : new LinkedHashMap<>(entries);
    }

    public Optional<String> get(String key) {
        return Optional.ofNullable(this.entries.get(key));
    }

    public Map<String, String> asMap() {
        return new LinkedHashMap<>(this.entries);
    }
}
