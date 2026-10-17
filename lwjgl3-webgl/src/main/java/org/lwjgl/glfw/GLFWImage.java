package org.lwjgl.glfw;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.MemoryStack;
import org.lwjgl.system.Struct;

/** An image GLFW can put on a window: width, height, and a pointer to RGBA bytes. */
public class GLFWImage extends Struct {

    public static final int SIZEOF = 16;
    private static final int WIDTH = 0;
    private static final int HEIGHT = 4;
    private static final int PIXELS = 8;

    public GLFWImage(long address) {
        super(address);
    }

    public int width() { return intAt(WIDTH); }

    public int height() { return intAt(HEIGHT); }

    /** The pixel pointer, as an address. */
    public long pixelsAddress() { return longAt(PIXELS); }

    public GLFWImage width(int value) { putIntAt(WIDTH, value); return this; }

    public GLFWImage height(int value) { putIntAt(HEIGHT, value); return this; }

    public GLFWImage pixels(long value) { putLongAt(PIXELS, value); return this; }

    /** A buffer of images on the stack, as the client asks for it. */
    public static GLFWImage$Buffer malloc(int count, MemoryStack stack) {
        long address = stack.nmalloc(count * SIZEOF);
        return new GLFWImage$Buffer(address, count);
    }
}
