package oshi.hardware;

// HANDWRITTEN: see oshi/SystemInfo.java.

import java.util.Collections;
import java.util.List;

public final class HardwareAbstractionLayer {

    private final CentralProcessor processor = new CentralProcessor();
    private final GlobalMemory memory = new GlobalMemory();

    public CentralProcessor getProcessor() {
        return processor;
    }

    public GlobalMemory getMemory() {
        return memory;
    }

    /** A browser cannot enumerate the machine's graphics cards, and says so with an empty list. */
    public List<GraphicsCard> getGraphicsCards() {
        return Collections.emptyList();
    }
}
