package org.quiltmc.loader.api.metadata;

import org.quiltmc.loader.api.Version;

/** Quilt's mod metadata, only as far as a compatibility check reads it. */
public interface ModMetadata {
    String id();

    String name();

    Version version();

    String description();

    java.util.Collection<String> licenses();

    java.util.Map<String, String> contact();
}
