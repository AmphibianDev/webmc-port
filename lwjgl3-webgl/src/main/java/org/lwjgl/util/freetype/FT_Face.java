package org.lwjgl.util.freetype;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.Struct;

/** A loaded font face; the only thing a client reads from it here is the current glyph slot. */
public class FT_Face extends Struct {

    public static final int SIZEOF = 248;
    private static final int GLYPH = 168;

    public FT_Face(long address) {
        super(address);
    }

    public static FT_Face create(long address) {
        return new FT_Face(address);
    }

    public FT_GlyphSlot glyph() {
        return new FT_GlyphSlot(address() + GLYPH);
    }
}
