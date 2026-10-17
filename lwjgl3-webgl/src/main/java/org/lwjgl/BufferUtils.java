package org.lwjgl;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.IntBuffer;
import java.nio.ShortBuffer;

import org.lwjgl.system.WebMCArena;

/**
 * Direct buffers made over the shim's arena, so that a buffer created here and one handed out by a GL
 * call are the same kind of thing - both have an address the arena understands.
 */
public final class BufferUtils {

    private BufferUtils() {
    }

    public static ByteBuffer createByteBuffer(int size) {
        return WebMCArena.window(WebMCArena.malloc(size), size);
    }

    public static ByteBuffer createByteBuffer(byte[] data) {
        ByteBuffer buffer = createByteBuffer(data.length);
        buffer.put(data);
        buffer.flip();
        return buffer;
    }

    public static IntBuffer createIntBuffer(int size) {
        return createByteBuffer(size * 4).asIntBuffer();
    }

    public static FloatBuffer createFloatBuffer(int size) {
        return createByteBuffer(size * 4).asFloatBuffer();
    }

    public static ShortBuffer createShortBuffer(int size) {
        return createByteBuffer(size * 2).asShortBuffer();
    }

    public static ByteBuffer createByteBuffer(long address, int size) {
        return WebMCArena.window(address, size);
    }

    /** Kept for clients that ask: the arena's buffers already use the native order. */
    public static ByteOrder nativeOrder() {
        return ByteOrder.nativeOrder();
    }
}
