package oshi.hardware;

// HANDWRITTEN: see oshi/SystemInfo.java.

public final class PhysicalMemory {
    public long getCapacity() {
        return Runtime.getRuntime().maxMemory();
    }

    public long getClockSpeed() {
        return 0L;
    }

    public String getMemoryType() {
        return "";
    }
}
