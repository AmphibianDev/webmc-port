package org.lwjgl;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

/**
 * The version a client is told it is running against. It matters: clients log it, and some branch on
 * it. 3.3.3 is the version this shim's surface was measured from, so that is what it answers.
 */
public final class Version {

    public static final int VERSION_MAJOR = 3;
    public static final int VERSION_MINOR = 3;
    public static final int VERSION_REVISION = 3;
    public static final String VERSION = "3.3.3";

    private Version() {
    }

    public static String getVersion() {
        return VERSION;
    }
}
