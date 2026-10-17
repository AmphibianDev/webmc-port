package org.lwjgl.openal;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/** Creating the device capabilities. The address is whatever the client passed; there is no device behind it. */
public final class ALC {

    private ALC() {}

    public static ALCCapabilities createCapabilities(long device) {
        return new ALCCapabilities();
    }
}
