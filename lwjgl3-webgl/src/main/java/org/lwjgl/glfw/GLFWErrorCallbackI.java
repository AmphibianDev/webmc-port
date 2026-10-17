package org.lwjgl.glfw;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/** What GLFW calls with an error: a code, and a pointer to a NUL-terminated description. */
public interface GLFWErrorCallbackI {
    void invoke(int error, long description);
}
