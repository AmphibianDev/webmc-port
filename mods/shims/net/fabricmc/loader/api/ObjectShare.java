package net.fabricmc.loader.api;

/**
 * The place mods put objects for each other to find. It is a map, and it behaves like one.
 */
public interface ObjectShare {
    Object put(String key, Object value);

    Object get(String key);

    Object remove(String key);

    boolean containsKey(String key);

    java.util.Map<String, Object> asMap();
}
