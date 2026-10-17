package org.slf4j;

import java.util.Iterator;

/** The marker a logging call may carry. Fabric API's modules use it; the console logger ignores it. */
public interface Marker extends java.io.Serializable {
    String getName();

    void add(Marker reference);

    boolean remove(Marker reference);

    boolean hasChildren();

    boolean hasReferences();

    Iterator<Marker> iterator();

    boolean contains(Marker other);

    boolean contains(String name);
}
