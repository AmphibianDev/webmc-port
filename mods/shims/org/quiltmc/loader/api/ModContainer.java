package org.quiltmc.loader.api;

import java.util.List;

/** Quilt's mod container: enough shape for a Fabric build that asks whether Quilt is there. */
public interface ModContainer {
    ModMetadata metadata();

    BasicSourceType getSourceType();

    List<java.nio.file.Path> getSourcePaths();

    /** Where a mod came from, in Quilt's own words. */
    enum BasicSourceType {
        NORMAL_QUILT,
        NORMAL_FABRIC,
        UNKNOWN
    }
}
