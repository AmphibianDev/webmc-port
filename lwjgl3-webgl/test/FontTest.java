package webmc;

import java.nio.ByteBuffer;
import java.nio.IntBuffer;

import org.lwjgl.PointerBuffer;
import org.lwjgl.system.MemoryStack;
import org.lwjgl.util.freetype.FT_Face;
import org.lwjgl.util.freetype.FT_GlyphSlot;
import org.lwjgl.util.freetype.FreeType;

/** Does the FreeType shim produce a glyph, and is it a glyph rather than a blank? */
public final class FontTest {
    public static void main(String[] args) {
        try (MemoryStack stack = MemoryStack.stackPush()) {
            PointerBuffer library = stack.mallocPointer(1);
            if (FreeType.FT_Init_FreeType(library) != 0) {
                throw new IllegalStateException("FT_Init_FreeType failed");
            }
            PointerBuffer facePointer = stack.mallocPointer(1);
            ByteBuffer font = MemoryStack.stackGet().malloc(16);      // the bytes are not read: see FreeType
            if (FreeType.FT_New_Memory_Face(library.get(0), font, 0L, facePointer) != 0) {
                throw new IllegalStateException("FT_New_Memory_Face failed");
            }
            FT_Face face = FT_Face.create(facePointer.get(0));
            FreeType.FT_Set_Pixel_Sizes(face, 0, 24);
            IntBuffer indexBuffer = stack.mallocInt(1);
            long first = FreeType.FT_Get_First_Char(face, indexBuffer);
            System.out.println("  charmap starts at '" + (char) first + "' index " + indexBuffer.get(0));
            long next = FreeType.FT_Get_Next_Char(face, first, indexBuffer);
            if (next != first + 1 || indexBuffer.get(0) != first + 1) {
                throw new IllegalStateException("the charmap does not advance");
            }
            int loaded = FreeType.FT_Load_Glyph(face, 'A', 0);
            FT_GlyphSlot slot = face.glyph();
            int width = slot.bitmap().width();
            int rows = slot.bitmap().rows();
            int pitch = slot.bitmap().pitch();
            int left = slot.bitmap_left();
            int top = slot.bitmap_top();
            long advance = slot.advance().x();
            ByteBuffer coverage = slot.bitmap().buffer(width * rows);
            int painted = 0;
            int darkest = 0;
            for (int index = 0; index < width * rows; index++) {
                int value = coverage.get(index) & 0xFF;
                if (value > 32) painted++;
                if (value > darkest) darkest = value;
            }
            System.out.println("  'A' loaded=" + loaded + " bitmap " + width + "x" + rows + " pitch=" + pitch
                + " left=" + left + " top=" + top + " advance=" + advance);
            System.out.println("  painted pixels: " + painted + " of " + (width * rows) + ", darkest " + darkest);
            if (loaded != 0) throw new IllegalStateException("FT_Load_Glyph returned " + loaded);
            if (width < 4 || rows < 4) throw new IllegalStateException("the glyph has no size");
            if (painted < 8) throw new IllegalStateException("the glyph has no ink");
            if (advance < 4) throw new IllegalStateException("the advance is too small");
            FreeType.FT_Done_Face(face);
            System.out.println("FONT OK");
        }
    }
}
