package org.quiltmc.loader.api;

import java.util.Collection;
import java.util.List;

/** Quilt's mod metadata: what a compatibility check reads. */
public interface ModMetadata {
    String id();

    String name();

    Version version();

    Collection<ModContributor> contributors();
}
