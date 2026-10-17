package org.quiltmc.loader.api;

/** Thrown when a version string is not a version. */
public class VersionFormatException extends Exception {
    private static final long serialVersionUID = 1L;

    public VersionFormatException(String message) {
        super(message);
    }

    public VersionFormatException(String message, Throwable cause) {
        super(message, cause);
    }
}
