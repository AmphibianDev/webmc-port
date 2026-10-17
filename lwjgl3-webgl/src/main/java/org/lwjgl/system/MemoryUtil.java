package org.lwjgl.system;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.IntBuffer;

/**
 * The 26 members of LWJGL's MemoryUtil that a Minecraft client actually calls, over {@link WebMCArena}.
 *
 * Addresses are the arena's, so no address ever reaches a native call: there are none. The one thing
 * that cannot be faked is a buffer that did not come from here - {@code ByteBuffer.allocateDirect}
 * called by the client itself, say - which is handled by copying it into the arena once, since the
 * client is about to hand its address to a GL call that will read it.
 */
public final class MemoryUtil {

    private static final MemoryAllocator ALLOCATOR = new MemoryAllocator();

    private MemoryUtil() {
    }

    // ---- allocation ------------------------------------------------------------------------------

    public static long nmemAlloc(long size) {
        return WebMCArena.malloc(size);
    }

    public static long nmemCalloc(long count, long size) {
        long address = WebMCArena.malloc(count * size);
        memSet(address, (byte) 0, count * size);
        return address;
    }

    public static void nmemFree(long address) {
        WebMCArena.free(address);
    }

    public static ByteBuffer memAlloc(int size) {
        return WebMCArena.window(WebMCArena.malloc(size), size);
    }

    public static ByteBuffer memRealloc(ByteBuffer buffer, int size) {
        return WebMCArena.window(WebMCArena.realloc(memAddress0(buffer), size), size);
    }

    public static void memFree(ByteBuffer buffer) {
        long address = WebMCArena.addressOf(buffer);
        if (address >= 0) {
            WebMCArena.free(address);
        }
    }

    public static IntBuffer memAllocInt(int count) {
        return memIntBuffer(nmemAlloc((long) count * 4), count);
    }

    public static FloatBuffer memAllocFloat(int count) {
        return memFloatBuffer(nmemAlloc((long) count * 4), count);
    }

    public static MemoryAllocator getAllocator(boolean ignored) {
        return ALLOCATOR;
    }

    // ---- views ------------------------------------------------------------------------------------

    public static ByteBuffer memByteBufferLegacy(long address, int capacity) {
        return WebMCArena.window(address, capacity);
    }

    public static IntBuffer memIntBuffer(long address, int capacity) {
        IntBuffer view = WebMCArena.window(address, capacity * 4).asIntBuffer();
        WebMCArena.remember(view, address);
        return view;
    }

    public static FloatBuffer memFloatBuffer(long address, int capacity) {
        FloatBuffer view = WebMCArena.window(address, capacity * 4).asFloatBuffer();
        WebMCArena.remember(view, address);
        return view;
    }

    public static ByteBuffer memSlice(ByteBuffer buffer, int offset, int capacity) {
        long address = memAddress0(buffer);
        return memByteBuffer(address + offset, capacity);
    }

    public static long memAddress(ByteBuffer buffer) {
        return memAddress0(buffer);
    }

    /**
     * The address of any buffer. One this shim handed out knows its own address; anything else - a
     * direct buffer the client allocated itself - is copied into the arena once and remembered, which
     * is correct as long as the client is passing it to be read.
     */
    public static long memAddress0(java.nio.Buffer buffer) {
        long known = WebMCArena.addressOf(buffer);
        if (known >= 0) {
            return known;
        }
        // a buffer the client allocated itself: copy it into the arena once, then remember it. It is
        // about to be handed to a GL call that will read these bytes.
        ByteBuffer source;
        if (buffer instanceof ByteBuffer) {
            source = ((ByteBuffer) buffer).duplicate();
        } else if (buffer instanceof IntBuffer) {
            source = ByteBuffer.allocate(((IntBuffer) buffer).remaining() * 4);
            source.asIntBuffer().put((IntBuffer) buffer.duplicate());
        } else if (buffer instanceof FloatBuffer) {
            source = ByteBuffer.allocate(((FloatBuffer) buffer).remaining() * 4);
            source.asFloatBuffer().put((FloatBuffer) buffer.duplicate());
        } else {
            throw new UnsupportedOperationException("memAddress0 on a " + buffer.getClass().getName()
                + ": the shim knows byte, int and float buffers");
        }
        int size = Math.max(1, source.remaining());
        byte[] copy = new byte[size];
        source.rewind();
        source.get(copy, 0, Math.min(size, source.remaining()));
        long address = WebMCArena.malloc(size);
        WebMCArena.window(address, size).put(copy);
        WebMCArena.remember(buffer, address);
        return address;
    }

    // ---- access -----------------------------------------------------------------------------------

    public static byte memGetByte(long address) {
        return WebMCArena.slab(address).get(WebMCArena.offset(address));
    }

    public static int memGetInt(long address) {
        return WebMCArena.slab(address).getInt(WebMCArena.offset(address));
    }

    public static void memPutByte(long address, byte value) {
        WebMCArena.slab(address).put(WebMCArena.offset(address), value);
    }

    public static void memPutInt(long address, int value) {
        WebMCArena.slab(address).putInt(WebMCArena.offset(address), value);
    }

    public static void memSet(long address, byte value, long bytes) {
        ByteBuffer window = WebMCArena.window(address, (int) bytes);
        for (int index = 0; index < bytes; index++) {
            window.put(index, value);
        }
    }

    public static void memCopy(long source, long destination, long bytes) {
        ByteBuffer from = WebMCArena.window(source, (int) bytes);
        ByteBuffer to = WebMCArena.window(destination, (int) bytes);
        to.put(0, from, 0, (int) bytes);
    }

    /** A NUL-terminated UTF-8 string, which is what a GL call like glShaderSource is handed. */
    public static String memUTF8(long address) {
        ByteBuffer slab = WebMCArena.slab(address);
        int start = WebMCArena.offset(address);
        int end = start;
        while (slab.get(end) != 0) {
            end++;
        }
        byte[] bytes = new byte[end - start];
        for (int index = 0; index < bytes.length; index++) {
            bytes[index] = slab.get(start + index);
        }
        return new String(bytes, java.nio.charset.StandardCharsets.UTF_8);
    }

    /** The allocator object a client can ask for and then use directly. */
    public static final class MemoryAllocator {

        public long malloc(long size) {
            return nmemAlloc(size);
        }

        public long calloc(long count, long size) {
            return nmemCalloc(count, size);
        }

        public long realloc(long address, long size) {
            return WebMCArena.realloc(address, size);
        }

        public void free(long address) {
            nmemFree(address);
        }

        public void free(java.nio.Buffer buffer) {
            WebMCArena.free(memAddress0(buffer));
        }
    }

    /** Kept because clients name it; the arena already orders buffers natively. */
    public static ByteBuffer memByteBuffer(long address, int capacity, ByteOrder ignored) {
        return memByteBuffer(address, capacity);
    }

    public static void memPutFloat(long address, float value) {
        WebMC.nmemPutFloat(address, value);
    }

    public static float memGetFloat(long address) {
        return WebMC.nmemGetFloat(address);
    }

    // demanded by the client; the JavaScript behind it is in natives/webmc-lwjgl3.js
    public static void memPutShort(long arg0, short arg1) { throw new UnsupportedOperationException("org.lwjgl.system.MemoryUtil.memPutShort"); }


    public static short memGetShort(long address) {
        return WebMC.nmemGetShort(address);
    }

    public static long memGetLong(long address) {
        return WebMC.nmemGetLong(address);
    }

    public static void memPutLong(long address, long value) {
        WebMC.nmemPutLong(address, value);
    }

    /** The bytes at an address, as a direct buffer: how a struct hands back an array field. */
    public static ByteBuffer memByteBuffer(long address, int capacity) {
        return memByteBuffer(address, capacity, ByteOrder.nativeOrder());
    }
}
