package org.lwjgl.system;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.HashMap;
import java.util.Map;

/**
 * The memory a shimmed LWJGL needs, without any native memory to have.
 *
 * LWJGL hands out addresses: {@code memAlloc} returns a {@code long}, GL calls are given those
 * addresses, and buffers are *views* of them. A browser JVM has no address space to hand out, so this
 * provides one - a set of direct {@link ByteBuffer} slabs - and encodes an address as
 *
 *     address = ((slabIndex + 1) &lt;&lt; 32) | offsetInSlab
 *
 * which makes {@link #slab(long)} and {@link #offset(long)} a shift and a mask, so every mem* call is
 * O(1). A buffer view is a {@code duplicate()} of the slab: the same memory, seen through a different
 * window, exactly like a real pointer cast.
 *
 * Freeing is a first-fit over a per-slab free list, with a bump pointer for the common case of
 * allocating without freeing. It is not a malloc: it does not coalesce and it never returns memory to
 * the OS. Games allocate and free buffers in patterns real allocators tune for, and this is the
 * simplest thing that can serve them.
 */
public final class WebMCArena {

    private static final long SLAB_BYTES = 1L << 24;       // 16 MiB of direct memory per slab
    private static final List<ByteBuffer> SLABS = new ArrayList<>();
    private static final List<List<long[]>> FREE = new ArrayList<>();   // per slab: {offset, length}
    private static final long[] BUMP = new long[64];                    // per slab: next free offset
    private static final Map<java.nio.Buffer, Long> ADDRESSES = new IdentityHashMap<>();
    private static final Map<Long, Long> SIZES = new HashMap<>();

    private WebMCArena() {
    }

    public static synchronized long malloc(long bytes) {
        if (bytes <= 0) {
            bytes = 1;
        }
        for (int index = 0; index < SLABS.size(); index++) {
            Long found = firstFit(index, bytes);
            if (found != null) {
                SIZES.put(found, bytes);
                return found;
            }
        }
        long address = bumpInNewSlab(bytes);
        SIZES.put(address, bytes);
        return address;
    }

    private static Long firstFit(int slabIndex, long bytes) {
        List<long[]> regions = FREE.get(slabIndex);
        for (int index = 0; index < regions.size(); index++) {
            long[] region = regions.get(index);
            if (region[1] >= bytes) {
                long address = encode(slabIndex, region[0]);
                region[0] += bytes;
                region[1] -= bytes;
                if (region[1] == 0) {
                    regions.remove(index);
                }
                return address;
            }
        }
        return null;
    }

    private static long bumpInNewSlab(long bytes) {
        long capacity = Math.max(SLAB_BYTES, (bytes + SLAB_BYTES - 1) / SLAB_BYTES * SLAB_BYTES);
        ByteBuffer slab = ByteBuffer.allocateDirect((int) capacity).order(ByteOrder.nativeOrder());
        int index = SLABS.size();
        SLABS.add(slab);
        FREE.add(new ArrayList<>());
        if (index >= BUMP.length) {
            throw new OutOfMemoryError("the shim's arena ran out of slab slots; raise the slab count");
        }
        BUMP[index] = bytes;
        return encode(index, 0);
    }

    public static synchronized void free(long address) {
        if (address == 0) {
            return;
        }
        int index = slabIndex(address);
        Long bytes = SIZES.remove(address);
        if (index < 0 || index >= SLABS.size() || bytes == null || bytes <= 0) {
            return;   // never handed out here, or freed twice: not this arena's problem
        }
        List<long[]> regions = FREE.get(index);
        regions.add(new long[]{offset(address), bytes});
        // merge with the neighbouring region when it is already free, so a repeated
        // allocate-and-free does not fragment the slab into unusable slivers
        for (int first = 0; first < regions.size(); first++) {
            long[] left = regions.get(first);
            for (int second = 0; second < regions.size(); second++) {
                long[] right = regions.get(second);
                if (first != second && left[0] + left[1] == right[0]) {
                    left[1] += right[1];
                    regions.remove(second);
                    return;
                }
            }
        }
    }

    public static synchronized long realloc(long address, long bytes) {
        long fresh = malloc(bytes);
        if (address != 0) {
            Long old = SIZES.get(address);
            int copy = (int) Math.min(old == null ? bytes : old, bytes);
            window(fresh, copy).put(0, window(address, copy), 0, copy);
            free(address);
        }
        return fresh;
    }

    /** The slab an address lives in. */
    public static ByteBuffer slab(long address) {
        int index = slabIndex(address);
        if (index < 0 || index >= SLABS.size()) {
            throw new IllegalArgumentException("address 0x" + Long.toHexString(address)
                + " was not handed out by this shim");
        }
        return SLABS.get(index);
    }

    private static int slabIndex(long address) {
        return (int) ((address >>> 32) - 1);
    }

    public static int offset(long address) {
        return (int) (address & 0xFFFFFFFFL);
    }

    private static long encode(int slabIndex, long offset) {
        return ((long) (slabIndex + 1) << 32) | offset;
    }

    /**
     * Copy a region out, for the one place it has to leave Java: a GL call whose data is an address
     * (a buffer upload, a texture) is implemented in JavaScript, and JavaScript cannot read these slabs.
     * One copy at that boundary, and the alternative - two arenas, one of which JavaScript can read - is
     * what this project spent a day discovering is worse.
     */
    public static synchronized byte[] read(long address, int length) {
        byte[] bytes = new byte[length];
        window(address, length).get(0, bytes, 0, length);
        return bytes;
    }

    /** Copy a region in, for the other direction: pixels read back from a GL call. */
    public static synchronized void write(long address, byte[] bytes) {
        window(address, bytes.length).put(0, bytes, 0, bytes.length);
    }

    /** A view of the arena at this address: the same memory, a different window. */
    public static synchronized ByteBuffer window(long address, int capacity) {
        ByteBuffer view = slab(address).duplicate().order(ByteOrder.nativeOrder());
        view.position(offset(address));
        view.limit(offset(address) + capacity);
        ByteBuffer slice = view.slice().order(ByteOrder.nativeOrder());
        ADDRESSES.put(slice, address);
        return slice;
    }

    /** Register a derived view (an IntBuffer over a ByteBuffer, say) with the address it starts at. */
    public static synchronized void remember(java.nio.Buffer buffer, long address) {
        ADDRESSES.put(buffer, address);
    }

    /** The address of a buffer this shim handed out, or -1 when it did not. */
    public static synchronized long addressOf(java.nio.Buffer buffer) {
        Long known = ADDRESSES.get(buffer);
        return known == null ? -1L : known;
    }

    public static synchronized int slabCount() {
        return SLABS.size();
    }
}
