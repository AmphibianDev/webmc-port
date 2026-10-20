package org.lwjgl.stb;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

/**
 * stbi_write_png, which is how a client saves a screenshot.
 *
 * It is not implemented, and it says so by returning 0 - the same answer the real library gives when it
 * cannot write. The caller checks that value, so a screenshot fails as a missing file rather than a file
 * full of nothing. Writing a PNG encoder would be a feature, not a shim of one.
 */
public final class STBImageWrite {

    private STBImageWrite() {
    }

    public static int nstbi_write_png_to_func(long context, long callback, int width, int height, int channels,
                                              long data, int stride) {
        return 0;
    }
}
