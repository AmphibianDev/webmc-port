package org.lwjgl;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;

import org.lwjgl.system.MemoryStack;
import org.lwjgl.system.MemoryUtil;
import org.lwjgl.system.Pointer;
import org.lwjgl.system.WebMCArena;

/**
 * An array of addresses, which is how LWJGL passes things like {@code glGenBuffers}' output.
 *
 * It is a view over arena memory with eight bytes per element, so the addresses a client reads out of
 * it are the same ones the shim understands. Elements are read at an absolute index, and all "Cursor"
 * methods the client uses are this class's own small bookkeeping.
 */
public class PointerBuffer extends Pointer {

    private final ByteBuffer container;
    private final long address;
    private int position;
    private int limit;
    private final int capacity;

    /** Public because a MemoryStack builds one, and callers may want a view of their own memory. */
    public PointerBuffer(ByteBuffer container, long address, int capacity) {
        this.container = container.order(ByteOrder.nativeOrder());
        this.address = address;
        this.capacity = capacity;
        this.limit = capacity;
    }

    /** A buffer of {@code count} addresses, allocated on the stack if given one. */
    public static PointerBuffer allocateDirect(int count, MemoryStack stack) {
        if (stack != null) {
            ByteBuffer window = stack.malloc(count * Pointer.POINTER_SIZE);
            return new PointerBuffer(window, MemoryUtil.memAddress0(window), count);
        }
        ByteBuffer window = ByteBuffer.allocateDirect(count * Pointer.POINTER_SIZE).order(ByteOrder.nativeOrder());
        return new PointerBuffer(window, MemoryUtil.memAddress0(window), count);
    }

    public static PointerBuffer create(int count) {
        return allocateDirect(count, null);
    }

    public static PointerBuffer allocateDirect(int count) {
        return allocateDirect(count, null);
    }

    @Override
    public long address() {
        return address;
    }

    public long address0() {
        return address;
    }

    public int capacity() {
        return capacity;
    }

    public int limit() {
        return limit;
    }

    public PointerBuffer limit(int value) {
        limit = Math.min(value, capacity);
        return this;
    }

    public int position() {
        return position;
    }

    public int remaining() {
        return limit - position;
    }

    public PointerBuffer position(int value) {
        position = Math.max(0, Math.min(value, limit));
        return this;
    }

    public long get() {
        long value = get(position);
        position++;
        return value;
    }

    public long get(int index) {
        return container.getLong(index * Pointer.POINTER_SIZE);
    }

    public PointerBuffer put(long value) {
        put(position++, value);
        return this;
    }

    public PointerBuffer put(int index, long value) {
        ensure(index);
        container.putLong(index * Pointer.POINTER_SIZE, value);
        return this;
    }

    /** Copy the addresses of another buffer into this one, which is what glDeleteBuffers expects. */
    public PointerBuffer put(ByteBuffer other) {
        while (other.hasRemaining()) {
            put(other.getLong());
        }
        return this;
    }

    public PointerBuffer put(PointerBuffer other) {
        for (int index = 0; index < other.remaining(); index++) {
            put(other.get(index));
        }
        return this;
    }

    /** A view of the memory at element {@code index}, as floats: used for uniform values. */
    public FloatBuffer getFloatBuffer(int index, int capacity) {
        long target = get(index);
        return MemoryUtil.memFloatBuffer(target, capacity);
    }

    /** A sub-buffer of addresses starting at element {@code index}. */
    public PointerBuffer getPointerBuffer(int index) {
        long target = get(index);
        ByteBuffer window = WebMCArena.window(target, this.capacity * Pointer.POINTER_SIZE);
        return new PointerBuffer(window, target, this.capacity);
    }

    private void ensure(int index) {
        if (index < 0 || index >= capacity) {
            throw new IndexOutOfBoundsException("element " + index + " of " + capacity);
        }
    }

    @Override
    public String toString() {
        return "PointerBuffer[" + position + "/" + limit + " of " + capacity + "]";
    }
}
