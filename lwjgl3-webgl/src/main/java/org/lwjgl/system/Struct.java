package org.lwjgl.system;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/**
 * The one thing every LWJGL struct has: an address, and a way to read fields out of it.
 *
 * <p>LWJGL's own structs extend a large generated base class. This is the part of it that a client actually
 * touches - {@code width()}, {@code height()}, a pointer field, the address - because a struct the client holds
 * is always a view over memory: GLFW filled it in, and the client reads fields back out. The layout of each one
 * is written in the class that owns it, next to the field it describes, rather than in a table somewhere else.
 */
public abstract class Struct extends Pointer {

    private final long address;

    protected Struct(long address) {
        this.address = address;
    }

    @Override
    public long address() {
        return this.address;
    }

    /** An int field at an offset, as the struct's layout says. */
    protected int intAt(int offset) {
        return MemoryUtil.memGetInt(this.address + offset);
    }

    protected short shortAt(int offset) {
        return MemoryUtil.memGetShort(this.address + offset);
    }

    protected byte byteAt(int offset) {
        return MemoryUtil.memGetByte(this.address + offset);
    }

    protected long longAt(int offset) {
        return MemoryUtil.memGetLong(this.address + offset);
    }

    protected void putIntAt(int offset, int value) {
        MemoryUtil.memPutInt(this.address + offset, value);
    }

    protected void putLongAt(int offset, long value) {
        MemoryUtil.memPutLong(this.address + offset, value);
    }

    /** The bytes a pointer field points at, as a buffer the caller can read. */
    protected java.nio.ByteBuffer bytesAt(long target, int length) {
        return MemoryUtil.memByteBuffer(target, length);
    }
}
