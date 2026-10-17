package org.lwjgl.opengl;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.WebMC;

/**
 * The capabilities of the current context, created once and handed to the client.
 *
 * <p>The client calls this after making the context current. The answers come from the real WebGL 2 context
 * through the shim's native layer, so nothing here is a guess: an extension is reported as present only if the
 * browser reports it, and everything WebGL 2 cannot do is reported absent (see GLCapabilities).
 */
public final class GL {

    private static GLCapabilities capabilities;

    private GL() {}

    public static GLCapabilities createCapabilities() {
        return getCapabilities();
    }

    public static GLCapabilities getCapabilities() {
        if (capabilities == null) {
            boolean anisotropic = WebMC.hasGlExtension("EXT_texture_filter_anisotropic");
            capabilities = new GLCapabilities(anisotropic, WebMC.glVersion());
            WebMC.log("GL capabilities: " + capabilities + ", anisotropic filtering: " + anisotropic);
        }
        return capabilities;
    }
}
