package oshi.hardware;

// HANDWRITTEN: see oshi/SystemInfo.java.

/** What the JVM is allowed to use, which is the only memory figure a browser page really has. */
public final class VirtualMemory {

    private static final long MAX = Runtime.getRuntime().maxMemory() / 1048576L;
    private static final long USED = (Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory()) / 1048576L;

    public long getVirtualMax() {
        return MAX;
    }

    public long getVirtualInUse() {
        return USED;
    }

    public long getSwapTotal() {
        return 0L;
    }

    public long getSwapUsed() {
        return 0L;
    }
}
