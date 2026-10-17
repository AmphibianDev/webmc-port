package org.lwjgl.util.freetype;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.Struct;

/**
 * Where FreeType leaves one rasterised glyph.
 *
 * <p>Only the fields a client reads are expressed, and each is read at the offset FT_GlyphSlotRec gives it: the
 * metrics ({@code bitmap_left}, {@code bitmap_top}), the {@code bitmap}, and the {@code advance} vector. The
 * other fields are there in the struct and simply not reached through this class - the layout is the whole
 * reason the offsets below are written out.
 */
public class FT_GlyphSlot extends Struct {

    public static final int SIZEOF = 120;
    private static final int ADVANCE = 88;
    private static final int BITMAP = 96;
    private static final int BITMAP_LEFT = 104;
    private static final int BITMAP_TOP = 108;

    public FT_GlyphSlot(long address) {
        super(address);
    }

    public static FT_GlyphSlot create(long address) {
        return new FT_GlyphSlot(address);
    }

    public int bitmap_left() { return intAt(BITMAP_LEFT); }

    public int bitmap_top() { return intAt(BITMAP_TOP); }

    public FT_Vector advance() {
        long pointer = address() + ADVANCE;
        return pointer == 0 ? null : new FT_Vector(pointer);
    }

    public FT_Bitmap bitmap() {
        long pointer = address() + BITMAP;
        return pointer == 0 ? null : new FT_Bitmap(pointer);
    }
}
