package oshi.hardware;

// HANDWRITTEN: see oshi/SystemInfo.java.

import java.util.Collections;
import java.util.List;

public final class GlobalMemory {

    private final VirtualMemory virtual = new VirtualMemory();

    public VirtualMemory getVirtualMemory() {
        return virtual;
    }

    /** The physical memory sticks are not visible to a page, so this list is empty rather than invented. */
    public List<PhysicalMemory> getPhysicalMemory() {
        return Collections.emptyList();
    }
}
