package org.lwjgl.stb;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

import java.nio.ByteBuffer;

import org.lwjgl.system.WebMCArena;

/**
 * stbir_resize_uint8, over the arena.
 *
 * The client uses this to build mipmaps, so it is a scale of an 8-bit image with a channel count: bilinear
 * when shrinking or growing, which is what the real library's free function does for this call shape. The
 * two buffers are addresses in the arena and the strides are in bytes.
 */
public final class STBImageResize {

    private STBImageResize() {
    }

    public static int nstbir_resize_uint8(long input, int inputWidth, int inputHeight, int inputStride,
                                          long output, int outputWidth, int outputHeight, int outputStride,
                                          int channels) {
        if (input == 0L || output == 0L || channels <= 0 || outputWidth <= 0 || outputHeight <= 0) {
            return 0;
        }
        ByteBuffer source = WebMCArena.window(input, Math.max(inputStride, inputWidth * channels) * inputHeight);
        ByteBuffer target = WebMCArena.window(output, Math.max(outputStride, outputWidth * channels) * outputHeight);
        for (int y = 0; y < outputHeight; y++) {
            // sample at the centre of the destination pixel, which is what keeps an even shrink even
            float sourceY = (y + 0.5f) * inputHeight / outputHeight - 0.5f;
            int y0 = Math.max(0, Math.min(inputHeight - 1, (int) Math.floor(sourceY)));
            int y1 = Math.min(inputHeight - 1, y0 + 1);
            float fy = Math.max(0f, Math.min(1f, sourceY - y0));
            for (int x = 0; x < outputWidth; x++) {
                float sourceX = (x + 0.5f) * inputWidth / outputWidth - 0.5f;
                int x0 = Math.max(0, Math.min(inputWidth - 1, (int) Math.floor(sourceX)));
                int x1 = Math.min(inputWidth - 1, x0 + 1);
                float fx = Math.max(0f, Math.min(1f, sourceX - x0));
                for (int channel = 0; channel < channels; channel++) {
                    int at00 = y0 * inputStride + x0 * channels + channel;
                    int at10 = y0 * inputStride + x1 * channels + channel;
                    int at01 = y1 * inputStride + x0 * channels + channel;
                    int at11 = y1 * inputStride + x1 * channels + channel;
                    float top = (source.get(at00) & 0xFF) * (1 - fx) + (source.get(at10) & 0xFF) * fx;
                    float bottom = (source.get(at01) & 0xFF) * (1 - fx) + (source.get(at11) & 0xFF) * fx;
                    int value = Math.round(top * (1 - fy) + bottom * fy);
                    target.put(y * outputStride + x * channels + channel, (byte) Math.max(0, Math.min(255, value)));
                }
            }
        }
        return 1;
    }
}
