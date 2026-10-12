package org.lwjgl.system;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;
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
    private static final Map<ByteBuffer, Long> ADDRESSES = new IdentityHashMap<>();

    private WebMCArena() {
    }

    public static synchronized long malloc(long bytes) {
        if (bytes <= 0) {
            bytes = 1;
        }
        for (int index = 0; index < SLABS.size(); index++) {
            Long found = firstFit(index, bytes);
            if (found != null) {
                return found;
            }
        }
        return bumpInNewSlab(bytes);
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
        // the size is not recorded, so this cannot merge regions; a freed region is simply reusable
        // from its start, which is enough for the allocate-and-free patterns a game uses
        int index = slabIndex(address);
        if (index < 0 || index >= SLABS.size()) {
            return;
        }
        FREE.get(index).add(new long[]{offset(address), 0});
    }

    public static synchronized long realloc(long address, long bytes) {
        long fresh = malloc(bytes);
        if (address != 0) {
            buffer(fresh, (int) bytes).put(0, buffer(address, (int) bytes), 0, (int) bytes);
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

    /** A view of the arena at this address: the same memory, a different window. */
    public static synchronized ByteBuffer window(long address, int capacity) {
        ByteBuffer view = slab(address).duplicate().order(ByteOrder.nativeOrder());
        view.position(offset(address));
        view.limit(offset(address) + capacity);
        ByteBuffer slice = view.slice().order(ByteOrder.nativeOrder());
        ADDRESSES.put(slice, address);
        return slice;
    }

    /** The address of a buffer this shim handed out, or -1 when it did not. */
    public static synchronized long addressOf(ByteBuffer buffer) {
        Long known = ADDRESSES.get(buffer);
        return known == null ? -1L : known;
    }

    public static synchronized void remember(ByteBuffer buffer, long address) {
        ADDRESSES.put(buffer, address);
    }

    public static synchronized int slabCount() {
        return SLABS.size();
    }
}
