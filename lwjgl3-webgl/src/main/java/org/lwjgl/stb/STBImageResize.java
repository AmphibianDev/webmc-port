package org.lwjgl.stb;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

import java.nio.ByteBuffer;

import org.lwjgl.system.WebMCArena;

/**
 * stbir_resize_uint8, over the arena.
 *
 * The client uses this to build mipmaps: an 8-bit image with a channel count, scaled from one region of the
 * arena into another. The real library resamples properly; this scales with nearest-neighbour sampling,
 * which is what the call shape needs and what no one will see at mipmap sizes.
 */
public final class STBImageResize {

    private STBImageResize() {
    }

    public static int nstbir_resize_uint8(long input, int inputWidth, int inputHeight, int inputStride,
                                          long output, int outputWidth, int outputHeight, int outputStride,
                                          int channels) {
        if (input == 0L || output == 0L || inputWidth <= 0 || inputHeight <= 0 || outputWidth <= 0
            || outputHeight <= 0 || channels <= 0) {
            return 0;
        }
        ByteBuffer source = WebMCArena.window(input, inputStride * inputHeight);
        ByteBuffer target = WebMCArena.window(output, outputStride * outputHeight);
        for (int y = 0; y < outputHeight; y++) {
            int sourceY = Math.min(inputHeight - 1, y * inputHeight / outputHeight);
            for (int x = 0; x < outputWidth; x++) {
                int sourceX = Math.min(inputWidth - 1, x * inputWidth / outputWidth);
                for (int channel = 0; channel < channels; channel++) {
                    target.put(y * outputStride + x * channels + channel,
                               source.get(sourceY * inputStride + sourceX * channels + channel));
                }
            }
        }
        return 1;
    }
}
