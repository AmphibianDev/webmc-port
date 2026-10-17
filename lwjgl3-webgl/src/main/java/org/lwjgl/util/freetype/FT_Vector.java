package org.lwjgl.util.freetype;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.MemoryStack;
import org.lwjgl.system.Struct;

/** A position or a delta in 26.6 fixed point: two longs, in that order. */
public class FT_Vector extends Struct {

    public static final int SIZEOF = 16;

    public FT_Vector(long address) {
        super(address);
    }

    public static FT_Vector create(long address) {
        return new FT_Vector(address);
    }

    /** Memory for one vector, from the stack, the way the client asks for it. */
    public static FT_Vector malloc(MemoryStack stack) {
        return new FT_Vector(stack.nmalloc(SIZEOF));
    }

    public long x() { return longAt(0); }

    public long y() { return longAt(8); }

    public FT_Vector set(long x, long y) {
        putLongAt(0, x);
        putLongAt(8, y);
        return this;
    }
}
