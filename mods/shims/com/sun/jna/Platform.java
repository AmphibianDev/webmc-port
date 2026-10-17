package com.sun.jna;

/**
 * JNA's platform check, answered for a browser.
 *
 * Java-Native-Access cannot exist in a page, and the only question code asks through it is "which desktop
 * am I on". A browser is not Windows, not macOS and not Linux in the sense JNA means, so every answer is
 * false - which is not a stub: it is the truth, and it keeps a mod's desktop branch from running.
 */
public final class Platform {
    private Platform() {
    }

    public static boolean isWindows() {
        return false;
    }

    public static boolean isMac() {
        return false;
    }

    public static boolean isLinux() {
        return false;
    }

    public static boolean isFreeBSD() {
        return false;
    }

    public static boolean isARM() {
        return false;
    }

    public static boolean is64Bit() {
        return true;
    }

    public static int getNativeArch() {
        return 0;
    }

    public static String getNativeLibraryResourcePrefix() {
        return "browser";
    }
}
