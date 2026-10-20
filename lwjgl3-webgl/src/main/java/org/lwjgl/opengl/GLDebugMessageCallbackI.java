package org.lwjgl.opengl;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/** What GL calls with a message: source, type, id, severity, a length, the text, and the user pointer. */
public interface GLDebugMessageCallbackI {
    void invoke(int source, int type, int id, int severity, int length, long message, long userParam);
}
