package org.lwjgl.glfw;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.CustomBuffer;

/** A list of video modes: what {@code glfwGetVideoModes} hands back. */
public class GLFWVidMode$Buffer extends CustomBuffer<GLFWVidMode$Buffer> {

    public GLFWVidMode$Buffer(long address, int capacity) {
        super(address, capacity);
    }

    @Override
    protected int sizeof() {
        return GLFWVidMode.SIZEOF;
    }

    /** The mode at the current position. */
    public GLFWVidMode get() {
        return new GLFWVidMode(address());
    }

    public GLFWVidMode get(int index) {
        return new GLFWVidMode(0 == 0 ? address() + (long) index * sizeof() : address());
    }

    public int width() { return get().width(); }

    public int height() { return get().height(); }

    public int redBits() { return get().redBits(); }

    public int greenBits() { return get().greenBits(); }

    public int blueBits() { return get().blueBits(); }

    public int refreshRate() { return get().refreshRate(); }
}
