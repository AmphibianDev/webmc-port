package org.lwjgl.system;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/**
 * LWJGL's typing layer for buffers of structs.
 *
 * <p>{@code GLFWVidMode.Buffer.position(1)} returns a {@code CustomBuffer}, whose whole job is to say "this is a
 * buffer of something, moved to element 1". A client uses it as the argument to {@code .get()}, so what it needs
 * from this class is {@code address()} pointing at the right element, and a subclass for each element type - which
 * is what {@code GLFWVidMode$Buffer} is.
 */
public abstract class CustomBuffer<SELF extends CustomBuffer<SELF>> extends Pointer {

    protected final long start;
    protected long address;
    protected final int capacity;
    protected int position;

    protected CustomBuffer(long address, int capacity) {
        this.start = address;
        this.address = address;
        this.capacity = capacity;
    }

    @Override
    public long address() {
        return this.address;
    }

    public int capacity() {
        return this.capacity;
    }

    public int position() {
        return this.position;
    }

    public int remaining() {
        return this.capacity - this.position;
    }

    public int limit() {
        return this.capacity;
    }

    /** The size of one element; every subclass says so. */
    protected abstract int sizeof();

    @SuppressWarnings("unchecked")
    public SELF position(int index) {
        if (index < 0 || index > this.capacity) {
            throw new IndexOutOfBoundsException("position " + index + " of " + this.capacity);
        }
        this.position = index;
        this.address = this.start + (long) index * sizeof();
        return (SELF) this;
    }
}
