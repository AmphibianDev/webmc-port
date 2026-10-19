package org.lwjgl.glfw;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.CustomBuffer;

/** A list of images, for {@code glfwSetWindowIcon}. */
public class GLFWImage$Buffer extends CustomBuffer<GLFWImage$Buffer> {

    public GLFWImage$Buffer(long address, int capacity) {
        super(address, capacity);
    }

    @Override
    protected int sizeof() {
        return GLFWImage.SIZEOF;
    }

    public GLFWImage get() {
        return new GLFWImage(address());
    }

    public GLFWImage get(int index) {
        return new GLFWImage(address() + (long) index * sizeof());
    }

    public GLFWImage$Buffer position(int index) {
        super.position(index);
        return this;
    }

    public GLFWImage$Buffer width(int value) { get().width(value); return this; }

    public GLFWImage$Buffer height(int value) { get().height(value); return this; }

    public GLFWImage$Buffer pixels(java.nio.ByteBuffer buffer) {
        get().pixels(org.lwjgl.system.MemoryUtil.memAddress(buffer));
        return this;
    }
}
