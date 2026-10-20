package net.fabricmc.loader.api.metadata;

import net.fabricmc.loader.api.Version;
import java.util.Collection;
import java.util.List;

/** One dependency a mod declares, with the kind and the versions it accepts. */
public interface ModDependency {
    Kind getKind();

    String getModId();

    boolean matches(Version version);

    Collection<?> getVersionRequirements();

    List<?> getVersionIntervals();

    /** Which block of a descriptor this came from. */
    enum Kind {
        DEPENDS, RECOMMENDS, SUGGESTS, CONFLICTS, BREAKS
    }
}
