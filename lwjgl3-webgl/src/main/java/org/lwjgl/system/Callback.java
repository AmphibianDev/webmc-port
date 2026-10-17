package org.lwjgl.system;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/**
 * What every LWJGL callback has: something the JVM can call later.
 *
 * <p>In LWJGL a Callback carries a native function pointer and has to be freed by hand. In a browser there is no
 * function pointer to hand out, so what is carried here is the Java object itself: the natives in this shim take
 * that object and invoke it through CheerpJ's `lib` handle, which is how a JavaScript event reaches Java code.
 * That means there is nothing to free - and `free()` exists so a client that follows LWJGL's rule of freeing its
 * callbacks does not fail doing the right thing.
 */
public abstract class Callback extends Pointer {

    private final transient Object callback;
    private boolean freed;

    protected Callback(Object callback) {
        this.callback = callback;
    }

    /** The Java object the native side invokes. */
    public Object callback() {
        return this.callback;
    }

    @Override
    public long address() {
        // there is no address; a non-zero stand-in says "this callback exists" to code that checks
        return this.callback == null ? 0L : 1L;
    }

    public boolean freed() {
        return this.freed;
    }

    public void free() {
        this.freed = true;
    }
}
