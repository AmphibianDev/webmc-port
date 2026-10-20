package org.lwjgl.openal;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/**
 * What an OpenAL *device* can do. There is no device here (ALC10.alcOpenDevice answers 0, which is how a machine
 * without a sound card answers), so every one of these is false and the client turns sound off - its own path,
 * the same one it takes on a laptop with audio disabled.
 */
public final class ALCCapabilities {

    public final boolean OpenALC10 = false;
    public final boolean OpenALC11 = false;
    public final boolean ALC_EXT_CAPTURE = false;
    public final boolean ALC_ENUMERATE_ALL_EXT = false;
    public final boolean ALC_ENUMERATION_EXT = false;
    public final boolean ALC_EXT_DEDICATED = false;
    public final boolean ALC_EXT_disconnect = false;
    public final boolean ALC_EXT_EFX = false;
    public final boolean ALC_EXT_thread_local_context = false;
    public final boolean ALC_SOFT_HRTF = false;
    public final boolean ALC_SOFT_device_clock = false;
    public final boolean ALC_SOFT_loopback = false;
    public final boolean ALC_SOFT_pause_device = false;
    public final boolean ALC_SOFT_reopen_device = false;

    public String toString() {
        return "ALCCapabilities(no device)";
    }
}
