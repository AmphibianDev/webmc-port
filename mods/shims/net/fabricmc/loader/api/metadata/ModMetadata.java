package net.fabricmc.loader.api.metadata;

import net.fabricmc.loader.api.Version;
import java.util.Collection;
import java.util.Map;
import java.util.Optional;

/** A baked mod's own metadata: exactly the fields its fabric.mod.json carried. */
public interface ModMetadata {
    String getType();

    String getId();

    Collection<String> getProvides();

    Version getVersion();

    ModOrigin getOrigin();

    Map<String, Collection<String>> getDepends();

    Map<String, Collection<String>> getRecommends();

    Map<String, Collection<String>> getSuggests();

    Map<String, Collection<String>> getConflicts();

    Map<String, Collection<String>> getBreaks();

    Map<String, String> getContact();

    Optional<String> getDescription();

    Collection<Person> getAuthors();

    Collection<Person> getContributors();

    Collection<Person> getLicense();

    Collection<String> getIcon(int size);

    default Optional<String> getIconPath(int size) {
        Collection<String> icons = getIcon(size);
        return icons.isEmpty() ? Optional.empty() : Optional.of(icons.iterator().next());
    }

    boolean containsCustomValue(String key);

    CustomValue getCustomValue(String key);

    Map<String, CustomValue> getCustomValues();

    default boolean containsCustomElement(String key) {
        return containsCustomValue(key);
    }

    String getName();
}
