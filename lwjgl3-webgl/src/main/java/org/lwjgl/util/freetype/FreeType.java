package org.lwjgl.util.freetype;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

import java.nio.ByteBuffer;
import java.nio.IntBuffer;
import java.util.HashMap;
import java.util.Map;

import org.lwjgl.PointerBuffer;
import org.lwjgl.system.WebMC;
import org.lwjgl.system.WebMCArena;

/**
 * FreeType, as far as a browser can be FreeType.
 *
 * FreeType reads font files and rasterises them. A browser cannot do that synchronously - the FontFace API
 * is asynchronous and a native cannot await - so the glyphs are drawn with a canvas and a generic font
 * family instead, and this class is the bookkeeping around that: a face, its pixel size, and the glyph
 * slot the client is about to read.
 *
 * The structs are real: FT_Face, FT_GlyphSlot and FT_Bitmap are the generated classes, and they read
 * memory at the offsets the real library uses. So the glyph is rasterised into the arena, the glyph slot's
 * fields are written at those offsets, and the client reads it exactly as it reads a real FreeType slot.
 *
 * What this is not: the client's own font. The shapes are the platform's monospace. Text that is the right
 * size, in the right place, with the right measurements - and not the same letterforms.
 */
public final class FreeType {

    /** The value of FT_ENCODING_UNICODE in the real headers: 'unic' packed into 32 bits. */
    public static final long FT_ENCODING_UNICODEI = 0x756E6963L;

    private static final long LIBRARY = 1L;
    private static final int GLYPH_SLOT_BYTES = 512;

    /** Per face: its pixel size, where its glyph slot lives, and where the last bitmap went. */
    private static final class Face {
        int pixelSize = 16;
        long slot;              // the FT_GlyphSlot the client reads after FT_Load_Glyph
        long bitmapBuffer;      // the 8-bit coverage values themselves
        int bitmapCapacity;
    }

    private static final Map<Long, Face> FACES = new HashMap<>();

    private FreeType() {
    }

    public static int FT_Init_FreeType(PointerBuffer library) {
        library.put(0, LIBRARY);
        return 0;
    }

    public static int FT_Done_Library(long library) {
        FACES.clear();
        return 0;
    }

    /**
     * A face, from the font bytes the client just read.
     *
     * The bytes are deliberately not looked at: they are a font file this shim has no way to parse, and the
     * glyphs come from the canvas. What matters to the client is that a face exists, what its pixel size
     * is, and which characters it covers - and that is what this keeps.
     */
    public static int FT_New_Memory_Face(long library, ByteBuffer file, long faceIndex, PointerBuffer face) {
        Face state = new Face();
        long address = WebMCArena.malloc(FACE_BYTES);
        state.slot = WebMCArena.malloc(GLYPH_SLOT_BYTES);

        FACES.put(address, state);
        // FT_Face.glyph() reads a pointer out of this struct, so it has to be there before the client asks
        WebMCArena.window(address, FACE_BYTES).putLong(GLYPH_FIELD, state.slot);
        face.put(0, address);
        return 0;
    }

    public static int FT_Done_Face(FT_Face face) {
        long address = face == null ? 0L : face.address();
        Face state = FACES.remove(address);
        if (address != 0L) {
            WebMCArena.free(address);
        }
        if (state != null) {
            WebMCArena.free(state.slot);

            if (state.bitmapBuffer != 0L) {
                WebMCArena.free(state.bitmapBuffer);
            }
        }
        return 0;
    }

    public static int FT_Set_Pixel_Sizes(FT_Face face, int width, int height) {
        Face state = FACES.get(face.address());
        if (state != null) {
            state.pixelSize = Math.max(1, height);
        }
        return 0;
    }

    public static int FT_Select_Charmap(FT_Face face, int encoding) {
        return 0;
    }

    public static String FT_Get_Font_Format(FT_Face face) {
        return "TrueType";
    }

    public static String FT_Error_String(int error) {
        return error == 0 ? null : ("FreeType error " + error);
    }

    public static void FT_Set_Transform(FT_Face face, FT_Matrix matrix, FT_Vector delta) {
        // the canvas draws upright glyphs; a client that asks for a rotation gets the rotation it would
        // have got without one
    }

    /** The first character this face covers. ASCII onwards, because that is what the canvas draws. */
    public static long FT_Get_First_Char(FT_Face face, IntBuffer glyphIndex) {
        glyphIndex.put(0, FIRST_CHAR);
        return FIRST_CHAR;
    }

    public static long FT_Get_Next_Char(FT_Face face, long charCode, IntBuffer glyphIndex) {
        long next = charCode + 1;
        if (next > LAST_CHAR) {
            glyphIndex.put(0, 0);
            return 0;
        }
        glyphIndex.put(0, (int) next);
        return next;
    }

    private static final int FIRST_CHAR = 32;
    private static final int LAST_CHAR = 0x2FFF;
    private static final int BITMAP_OFFSET = 152;
    private static final int ADVANCE_OFFSET = 128;
    private static final int BITMAP_LEFT_OFFSET = 192;
    private static final int BITMAP_TOP_OFFSET = 196;
    /** FT_Face is a struct the client dereferences: the handle has to be a real address, not a token. */
    private static final int FACE_BYTES = 256;
    private static final int GLYPH_FIELD = 152;

    /**
     * Rasterise one glyph and fill in the slot the client will read.
     *
     * glyphIndex is the character: FT_Get_First_Char and FT_Get_Next_Char hand out the same number as both
     * the character and its index, so a client that iterates the charmap and then loads glyphs by index is
     * asking for the letter it saw.
     */
    public static int FT_Load_Glyph(FT_Face face, int glyphIndex, int flags) {
        Face state = FACES.get(face.address());
        if (state == null) {
            return 6;                       // FT_Err_Invalid_Argument, which a client reports and survives
        }
        int pixelSize = state.pixelSize;
        int capacity = (pixelSize + 2) * (pixelSize + 2);
        if (state.bitmapBuffer == 0L || state.bitmapCapacity < capacity) {
            if (state.bitmapBuffer != 0L) {
                WebMCArena.free(state.bitmapBuffer);
            }
            state.bitmapBuffer = WebMCArena.malloc(capacity);
            state.bitmapCapacity = capacity;
        }
        byte[] bitmap = new byte[capacity];
        WebMC.freetypeGlyph(glyphIndex, pixelSize, bitmap);
        WebMCArena.write(state.bitmapBuffer, bitmap);
        int width = WebMC.freetypeWidth();
        int rows = WebMC.freetypeRows();
        int pitch = WebMC.freetypePitch();
        int left = WebMC.freetypeLeft();
        int top = WebMC.freetypeTop();
        int advance = WebMC.freetypeAdvance();

        // advance and bitmap are *inlined* in the slot (FT_GlyphSlotRec), so their fields go straight into
        // it: writing a pointer there fills the slot with the address of a struct the client never looks at.
        ByteBuffer slot = WebMCArena.window(state.slot, GLYPH_SLOT_BYTES);
        slot.putLong(ADVANCE_OFFSET, advance);            // FT_Vector.x
        slot.putLong(ADVANCE_OFFSET + 8, 0L);             // FT_Vector.y
        slot.putInt(BITMAP_OFFSET, rows);                 // FT_Bitmap.rows
        slot.putInt(BITMAP_OFFSET + 4, width);            // FT_Bitmap.width
        slot.putInt(BITMAP_OFFSET + 8, pitch);            // FT_Bitmap.pitch
        slot.putLong(BITMAP_OFFSET + 16, state.bitmapBuffer);   // FT_Bitmap.buffer, a real pointer
        slot.putShort(BITMAP_OFFSET + 24, (short) 256);   // num_grays
        slot.put(BITMAP_OFFSET + 26, (byte) 2);           // pixel_mode: FT_PIXEL_MODE_GRAY
        slot.putInt(BITMAP_LEFT_OFFSET, left);
        slot.putInt(BITMAP_TOP_OFFSET, top);
        return 0;
    }
}
