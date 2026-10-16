package net.fabricmc.loader.api.metadata;

import java.util.List;

/** Where a mod came from: in this client, the package the bake merged in. */
public interface ModOrigin {
    Kind getKind();

    List<String> getPaths();

    default String getPath() {
        List<String> paths = getPaths();
        return paths.isEmpty() ? null : paths.get(0);
    }

    enum Kind {
        PATH, NESTED, UNKNOWN
    }
}
