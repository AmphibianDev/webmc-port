package org.lwjgl.opengl;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.Callback;

/** The ARB spelling of the same callback: one implementation, two names, as in LWJGL. */
public class GLDebugMessageARBCallback extends Callback {

    public GLDebugMessageARBCallback(GLDebugMessageARBCallbackI handler) {
        super(handler);
    }

    public static GLDebugMessageARBCallback create(GLDebugMessageARBCallbackI handler) {
        return new GLDebugMessageARBCallback(handler);
    }

    public GLDebugMessageARBCallbackI handler() {
        return callback() instanceof GLDebugMessageARBCallbackI ? (GLDebugMessageARBCallbackI) callback() : null;
    }
}
