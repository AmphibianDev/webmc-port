package oshi;

// HANDWRITTEN: a stand-in for OSHI, which cannot work in a browser.

import oshi.hardware.HardwareAbstractionLayer;

/**
 * OSHI, as much of it as a Minecraft client touches, without JNA.
 *
 * The real library reads hardware information through JNA, and there is no native library here: it logs
 * "Did not JNA classes", starts walking its groups and then stops answering. Minecraft waits on it during
 * startup - measured, with the client's last line being OSHI's own "Failed retrieving info for group
 * processor" and the CPU idle - and it also calls it when building a crash report, so with the library
 * simply absent the report builder throws instead.
 *
 * So this answers the eight classes the client asks for, quickly and honestly: the JVM's own view of the
 * machine where it exists, and empty values where a browser genuinely cannot know (graphics cards, memory
 * type, CPU microarchitecture). Nothing here blocks, calls a native, or claims to know more than it does.
 */
public final class SystemInfo {

    private final HardwareAbstractionLayer hardware = new HardwareAbstractionLayer();

    public HardwareAbstractionLayer getHardware() {
        return hardware;
    }
}
