package org.lwjgl.glfw;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/** Called when files are dropped on the window; a page reports drops as well. */
public class GLFWDropCallback {

    private GLFWDropCallback() {}

    public static String getName(long names, int index) {
        // an array of NUL-terminated strings, as GLFW passes it
        long pointer = org.lwjgl.system.MemoryUtil.memGetLong(names + (long) index * 8L);
        return pointer == 0 ? null : org.lwjgl.system.MemoryUtil.memUTF8(pointer);
    }
}
