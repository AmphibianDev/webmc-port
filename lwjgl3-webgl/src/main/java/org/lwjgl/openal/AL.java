package org.lwjgl.openal;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/** Creating the context capabilities, which are as quiet as the device they came from. */
public final class AL {

    private AL() {}

    public static ALCapabilities createCapabilities(ALCCapabilities device) {
        return new ALCapabilities();
    }
}
