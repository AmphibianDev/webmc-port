package org.slf4j;

/** Marker factory. The console logger does not mark anything, so these are honest no-ops. */
public interface IMarkerFactory {
    Marker getMarker(String name);

    boolean exists(String name);

    boolean detachMarker(String name);

    Marker getDetachedMarker(String name);
}
