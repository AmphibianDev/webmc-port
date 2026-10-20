package org.lwjgl.system;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

/** LWJGL's base class for things that can produce an address. */
public abstract class Pointer {

    public static final int POINTER_SIZE = 8;
    public static final int POINTER_SHIFT = 3;

    public abstract long address();
}
