package org.quiltmc.loader.api;

import java.util.Collection;

/** One contributor to a Quilt mod. */
public interface ModContributor {
    String name();

    Collection<String> roles();

    String role();
}
