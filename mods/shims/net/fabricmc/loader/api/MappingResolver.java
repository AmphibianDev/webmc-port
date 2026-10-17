package net.fabricmc.loader.api;

import java.util.Collection;

/**
 * The mapping resolver. In a compiled browser client there is exactly one namespace - the names the
 * client was compiled with - so every mapping is the identity and the namespace is "named".
 *
 * That is not a stub: it is what the loader would answer in a development environment, and reporting
 * it honestly lets Fabric API's own remapping logic take the short path.
 */
public interface MappingResolver {
    Collection<String> getNamespaces();

    String getCurrentRuntimeNamespace();

    String mapClassName(String namespace, String className);

    String unmapClassName(String targetNamespace, String className);

    String mapFieldName(String namespace, String owner, String name, String descriptor);

    String mapMethodName(String namespace, String owner, String name, String descriptor);
}
