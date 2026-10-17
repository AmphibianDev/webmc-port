package org.lwjgl.glfw;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.Callback;

/** Called when the window's close button is used: in a page, when the tab asks to close. */
public class GLFWWindowCloseCallback extends Callback {

    public GLFWWindowCloseCallback(GLFWWindowCloseCallbackI handler) {
        super(handler);
    }

    public static GLFWWindowCloseCallback create(GLFWWindowCloseCallbackI handler) {
        return new GLFWWindowCloseCallback(handler);
    }

    public static void free(GLFWWindowCloseCallback callback) {
        if (callback != null) {
            callback.free();
        }
    }
}
