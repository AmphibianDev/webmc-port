package net.fabricmc.loader.api.metadata;

/** A person in a mod's metadata: an author, a contributor, a maintainer. */
public interface Person {
    String getName();

    String getContact(String key);
}
