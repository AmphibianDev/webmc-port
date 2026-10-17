package net.fabricmc.loader.api.metadata;

import net.fabricmc.loader.api.Version;
import java.util.Collection;
import java.util.Map;
import java.util.Optional;

/**
 * A baked mod's own metadata, with the shape the loader's API declares (0.19+).
 *
 * The shapes matter more than the values here: a mod is compiled against *this* interface, so a member
 * that differs - `getDescription()` returning an Optional where the real API returns a String, say - is
 * a "method not found" in the middle of a compile rather than something a mod can notice and work around.
 */
public interface ModMetadata {
    String getType();

    String getId();

    Collection<String> getProvides();

    Version getVersion();

    ModEnvironment getEnvironment();

    Collection<ModDependency> getDependencies();

    Collection<ModDependency> getDepends();

    Collection<ModDependency> getRecommends();

    Collection<ModDependency> getSuggests();

    Collection<ModDependency> getConflicts();

    Collection<ModDependency> getBreaks();

    String getName();

    String getDescription();

    Collection<Person> getAuthors();

    Collection<Person> getContributors();

    ContactInformation getContact();

    Collection<Person> getLicense();

    Optional<String> getIconPath(int size);

    boolean containsCustomValue(String key);

    CustomValue getCustomValue(String key);

    Map<String, CustomValue> getCustomValues();

    boolean containsCustomElement(String key);
}
