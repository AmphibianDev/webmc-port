package org.lwjgl.glfw;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.Struct;

/**
 * One video mode, as GLFW describes it: six ints in a row.
 *
 * <p>The layout is written here, next to the fields it describes, because that is the only place it can be
 * checked: {@code width, height, redBits, greenBits, blueBits, refreshRate}, in that order, {@code int} each -
 * GLFW's own {@code GLFWvidmode}. The client asks the primary monitor for one of these and reads the fields out.
 */
public class GLFWVidMode extends Struct {

    public static final int SIZEOF = 6 * Integer.BYTES;
    private static final int WIDTH = 0;
    private static final int HEIGHT = 4;
    private static final int RED_BITS = 8;
    private static final int GREEN_BITS = 12;
    private static final int BLUE_BITS = 16;
    private static final int REFRESH_RATE = 20;

    public GLFWVidMode(long address) {
        super(address);
    }

    /** The mode at an address a native call handed back. */
    public static GLFWVidMode create(long address) {
        return new GLFWVidMode(address);
    }

    public int width() { return intAt(WIDTH); }

    public int height() { return intAt(HEIGHT); }

    public int redBits() { return intAt(RED_BITS); }

    public int greenBits() { return intAt(GREEN_BITS); }

    public int blueBits() { return intAt(BLUE_BITS); }

    public int refreshRate() { return intAt(REFRESH_RATE); }
}
