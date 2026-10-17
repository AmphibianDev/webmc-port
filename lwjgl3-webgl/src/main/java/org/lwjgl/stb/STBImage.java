package org.lwjgl.stb;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

import java.nio.ByteBuffer;
import java.nio.IntBuffer;

import org.lwjgl.system.WebMC;
import org.lwjgl.system.WebMCArena;

/**
 * The three members of STB image that a client calls, over the PNG decoder in JavaScript.
 *
 * A browser has no synchronous image decode - an &lt;img&gt;, createImageBitmap and DecompressionStream are all
 * asynchronous and a native cannot await - so the decoder is written out in natives/webmc-lwjgl3.js: an
 * inflate and the PNG filters, which is all Minecraft's textures need.
 *
 * The bytes travel to JavaScript through its own heap (it can read that, and it cannot read the arena), the
 * pixels come back the same way, and this copies them where the rest of the shim keeps memory. The shapes
 * of those calls are the ones the boundary allows: an array in with no return value, and scalars.
 */
public final class STBImage {

    /** Why the last load failed, in STB's own idiom. */
    private static String failure = "";

    private STBImage() {
    }

    public static ByteBuffer stbi_load_from_memory(ByteBuffer data, IntBuffer x, IntBuffer y, IntBuffer channels,
                                                   int requestedChannels) {
        byte[] input = new byte[data.remaining()];
        data.duplicate().get(input);
        if (input.length == 0) {
            failure = "no bytes to decode";
            return null;
        }
        long handover = WebMC.nmemAlloc(input.length);
        try {
            WebMC.nmemWriteBytes(handover, input, 0);
            WebMC.stbLoad(handover, input.length);
        } finally {
            WebMC.nmemFree(handover);
        }
        int width = WebMC.stbWidth();
        int height = WebMC.stbHeight();
        int components = WebMC.stbComponents();
        long scratch = WebMC.stbScratch();
        if (width <= 0 || height <= 0 || scratch == 0L) {
            failure = "the image could not be decoded";
            return null;
        }
        int size = width * height * components;
        long address = WebMCArena.malloc(size);
        WebMC.copyJsToArena(scratch, address, size);
        WebMC.stbFreeScratch(scratch);
        if (x != null && x.capacity() > 0) {
            x.put(0, width);
        }
        if (y != null && y.capacity() > 0) {
            y.put(0, height);
        }
        if (channels != null && channels.capacity() > 0) {
            channels.put(0, components);
        }
        failure = "";
        return WebMCArena.window(address, size);
    }

    public static String stbi_failure_reason() {
        return failure;
    }

    public static void nstbi_image_free(long address) {
        WebMCArena.free(address);
    }

    public static void stbi_image_free(ByteBuffer data) {
        if (data != null) {
            WebMCArena.free(WebMCArena.addressOf(data));
        }
    }
}
