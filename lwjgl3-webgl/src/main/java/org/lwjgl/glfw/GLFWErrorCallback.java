package org.lwjgl.glfw;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.Callback;

/**
 * The callback GLFW calls when something goes wrong, with a code and a message.
 *
 * <p>The client installs one of these at startup and logs what arrives. Our natives do not have a GLFW to fail
 * in, so this exists for the client's own error path: it is invoked by the shim when a WebGL call reports an
 * error, which turns "the game is silent and black" into a message in the client's log.
 */
public class GLFWErrorCallback extends Callback {

    public GLFWErrorCallback(GLFWErrorCallbackI handler) {
        super(handler);
    }

    public static GLFWErrorCallback create(GLFWErrorCallbackI handler) {
        return new GLFWErrorCallback(handler);
    }

    public static GLFWErrorCallback create() {
        return new GLFWErrorCallback(null);
    }

    /** LWJGL's API also allows freeing by class; kept so the client's usual pattern works. */
    public static void free(GLFWErrorCallback callback) {
        if (callback != null) {
            callback.free();
        }
    }

    /** The instance form a client calls when it is done with the callback. */
    public void free() {
        super.free();
    }

    public void close() {
        free();
    }

    public GLFWErrorCallbackI handler() {
        return callback() instanceof GLFWErrorCallbackI ? (GLFWErrorCallbackI) callback() : null;
    }
}
