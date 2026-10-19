package org.lwjgl.system;

// HANDWRITTEN: the class a client looks for by name when it wonders whether allocation is being checked.

/**
 * LWJGL's allocation-checking entry point, which this shim has to *have* even though it does not check.
 *
 * The client asks for {@code org.lwjgl.system.MemoryManage$DebugAllocator} with Class.forName while it
 * sets up its renderer - and a name looked up that way does not appear in any constant pool, so it was
 * invisible to the measurement of what the client references. Without the class the lookup threw
 * ClassNotFoundException and the client failed with "Initializing game" (measured).
 *
 * Allocation here is the arena, checked or not, so this answers with the same allocator the rest of the
 * shim uses.
 */
public final class MemoryManage {

    private MemoryManage() {
    }

    /** The allocator the client is looking for; the arena's own. */
    public static final class DebugAllocator {

        public DebugAllocator() {
        }

        public DebugAllocator(boolean checked) {
        }

        /**
         * Stop tracking an allocation.
         *
         * The client looks this up by reflection (with a MethodHandle built from Class.getMethod and
         * unreflect) and invokes it with an address alone - so it has to be static. Nothing here tracks
         * allocations, so there is nothing to stop doing; the method exists because its absence was a
         * NoSuchMethodException that took the client's renderer initialisation down with it.
         */
        public static void untrack(long address) {
        }

        public long malloc(long size) {
            return WebMCArena.malloc(size);
        }

        public long calloc(long count, long size) {
            long address = WebMCArena.malloc(count * size);
            MemoryUtil.memSet(address, 0, count * size);
            return address;
        }

        public long realloc(long address, long size) {
            return WebMCArena.realloc(address, size);
        }

        public void free(long address) {
            WebMCArena.free(address);
        }

        @Override
        public String toString() {
            return "DebugAllocator(arena)";
        }
    }
}
