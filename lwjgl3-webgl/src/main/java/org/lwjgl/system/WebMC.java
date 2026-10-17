package org.lwjgl.system;

// HANDWRITTEN: the door between Java and the JavaScript that actually implements LWJGL here.

/**
 * The memory and platform calls that have to happen on the JavaScript side.
 *
 * Under CheerpJ a Java `native` method is implemented by a JavaScript function named
 * `Java_<package>_<Class>_<method>`, found in a file under the JVM's `java.library.path`. The
 * implementation in `lwjgl3-webgl/natives/webmc-lwjgl3.js` owns the arena, so an address in Java is an
 * offset in that file's buffer - which is why every read and write goes through here rather than
 * through a Java-side arena: the two sides can never disagree about what an address means.
 */
public final class WebMC {

    private WebMC() {
    }

    public static native long nmemAlloc(long size);

    public static native void nmemFree(long address);

    public static native void nmemSet(long address, byte value, long bytes);

    public static native void nmemCopy(long source, long destination, long bytes);

    public static native byte nmemGetByte(long address);

    public static native short nmemGetShort(long address);

    public static native int nmemGetInt(long address);

    public static native long nmemGetLong(long address);

    public static native float nmemGetFloat(long address);

    public static native double nmemGetDouble(long address);

    public static native void nmemPutByte(long address, byte value);

    public static native void nmemPutShort(long address, short value);

    public static native void nmemPutInt(long address, int value);

    public static native void nmemPutLong(long address, long value);

    public static native void nmemPutFloat(long address, float value);

    public static native void nmemPutDouble(long address, double value);

    /** A NUL-terminated string, as a GL call like glShaderSource is handed. */
    public static native String nmemASCII(long address);

    public static native String nmemUTF8(long address);

    /** Push a Java byte[] into the arena: what a buffer the client filled in Java needs. */
    public static native void nmemWriteBytes(long address, byte[] bytes, int offset);

    /** And the other way, for a pointer a native filled. */
    public static native void nmemReadBytes(long address, byte[] bytes, int offset, int length);

    public static native long nmemSlice(long address, int offset, int capacity);

    public static native long arenaUsed();

    public static native long arenaCapacity();

    /**
     * Java's own log line, delivered where a page (and CI) can read it.
     *
     * System.out goes to the browser console under CheerpJ, which nobody can read from a script or from a
     * shell - so every "BOOT FAILED" this program printed was invisible to the thing running it. This is
     * the one line of plumbing that fixes that.
     */
    public static native void log(String message);

    /** And the same for the lines that are already StringBuffered. */
    public static native String logText();
}
