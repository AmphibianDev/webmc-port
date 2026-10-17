package org.lwjgl.glfw;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.Callback;

/** Called when a monitor is plugged or unplugged. In a page there is one monitor and it never changes. */
public class GLFWMonitorCallback extends Callback {

    public GLFWMonitorCallback(GLFWMonitorCallbackI handler) {
        super(handler);
    }

    public static GLFWMonitorCallback create(GLFWMonitorCallbackI handler) {
        return new GLFWMonitorCallback(handler);
    }

    public static void free(GLFWMonitorCallback callback) {
        if (callback != null) {
            callback.free();
        }
    }
}
