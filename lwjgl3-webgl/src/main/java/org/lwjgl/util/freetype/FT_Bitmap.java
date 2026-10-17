package org.lwjgl.util.freetype;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.Struct;

/**
 * FreeType's glyph bitmap: which pixel mode, how many rows, how wide, and where the buffer is.
 *
 * <p>FreeType is deliberately absent in this shim (see FreeType.FT_Init_FreeType), so the client takes its
 * stb_truetype path and never calls these. They are implemented anyway, and honestly, because "the client does not
 * call it yet" is not the same as "this is a stub": the layout is FT_Bitmap's own - {@code int rows, int width,
 * int pitch, char *buffer, short num_grays, char pixel_mode, char palette_mode, void *palette}.
 */
public class FT_Bitmap extends Struct {

    public static final int SIZEOF = 40;
    private static final int ROWS = 0;
    private static final int WIDTH = 4;
    private static final int PITCH = 8;
    private static final int BUFFER = 16;
    private static final int NUM_GRAYS = 24;
    private static final int PIXEL_MODE = 26;

    public FT_Bitmap(long address) {
        super(address);
    }

    public static FT_Bitmap create(long address) {
        return new FT_Bitmap(address);
    }

    public int rows() { return intAt(ROWS); }

    public int width() { return intAt(WIDTH); }

    public int pitch() { return intAt(PITCH); }

    public byte pixel_mode() { return byteAt(PIXEL_MODE); }

    /** The grayscale buffer, as a buffer the client can read. */
    public java.nio.ByteBuffer buffer(int capacity) {
        long pointer = longAt(BUFFER);
        return pointer == 0 ? null : org.lwjgl.system.MemoryUtil.memByteBuffer(pointer, capacity);
    }
}
