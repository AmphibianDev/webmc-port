package org.lwjgl.system;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

import java.nio.ByteBuffer;
import java.nio.FloatBuffer;
import java.nio.IntBuffer;
import java.util.ArrayList;
import java.util.List;

import org.lwjgl.PointerBuffer;

/**
 * LWJGL's scratch allocator: things a client allocates for the duration of a call and then forgets.
 *
 * Each frame records what it handed out and releases all of it on close, which is what makes
 * {@code try (MemoryStack stack = stackPush()) { ... }} behave. Frames are thread-local because a
 * client may allocate from more than one thread, and the arena is shared and synchronised.
 */
public final class MemoryStack implements AutoCloseable {

    private static final ThreadLocal<MemoryStack> CURRENT = ThreadLocal.withInitial(MemoryStack::new);

    private final List<long[]> frames = new ArrayList<>();   // each frame: a list of {address, bytes}
    private long[][] allocations = new long[16][];
    private int count;

    private MemoryStack() {
    }

    public static MemoryStack stackPush() {
        MemoryStack stack = CURRENT.get();
        stack.frames.add(new long[0]);
        stack.allocations = new long[Math.max(16, stack.allocations.length)][];
        stack.count = 0;
        return stack;
    }

    public static MemoryStack stackGet() {
        return CURRENT.get();
    }

    public static void stackPop() {
        CURRENT.get().close();
    }

    private long allocate(long bytes) {
        long address = WebMCArena.malloc(bytes);
        if (count == allocations.length) {
            long[][] grown = new long[allocations.length * 2][];
            System.arraycopy(allocations, 0, grown, 0, allocations.length);
            allocations = grown;
        }
        allocations[count++] = new long[]{address, bytes};
        return address;
    }

    public ByteBuffer malloc(int size) {
        return WebMCArena.window(allocate(size), size);
    }

    public ByteBuffer calloc(int size) {
        long address = allocate(size);
        MemoryUtil.memSet(address, (byte) 0, size);
        return WebMCArena.window(address, size);
    }

    public IntBuffer mallocInt(int count) {
        return MemoryUtil.memIntBuffer(allocate((long) count * 4), count);
    }

    public IntBuffer callocInt(int count) {
        IntBuffer buffer = mallocInt(count);
        for (int index = 0; index < count; index++) {
            buffer.put(index, 0);
        }
        return buffer;
    }

    public FloatBuffer mallocFloat(int count) {
        return MemoryUtil.memFloatBuffer(allocate((long) count * 4), count);
    }

    public PointerBuffer mallocPointer(int count) {
        return PointerBuffer.allocateDirect(count, this);
    }

    /** Free everything this frame handed out. */
    @Override
    public void close() {
        for (int index = 0; index < count; index++) {
            WebMCArena.free(allocations[index][0]);
        }
        count = 0;
        if (!frames.isEmpty()) {
            frames.remove(frames.size() - 1);
        }
    }
}
