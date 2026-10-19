package org.lwjgl.opengl;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/** The ARB form of a debug message callback: same arguments, different name. */
public interface GLDebugMessageARBCallbackI {
    void invoke(int source, int type, int id, int severity, int length, long message, long userParam);
}
