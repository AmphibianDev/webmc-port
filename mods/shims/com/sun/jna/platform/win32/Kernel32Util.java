package com.sun.jna.platform.win32;

import java.util.List;

/** JNA's Windows helpers. Nothing here can work in a page, and nothing pretends to. */
public final class Kernel32Util {
    private Kernel32Util() {
    }

    public static List<?> getModules(int processId) {
        throw new UnsupportedOperationException("this client runs in a browser: it has no Windows module list");
    }
}
