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

    /** Whether the WebGL context supports an extension; the browser is the only authority on that. */
    public static native boolean hasGlExtension(String name);

    /** The canvas size, for the GLFW calls that hand Java a pair of ints to fill. */
    public static native int windowWidth();

    public static native int windowHeight();

    /** What the GL context says it is, for the client's log. */
    public static native String glVersion();

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
    /**
     * A buffer upload whose source lives in the arena. JavaScript cannot read the arena, so the bytes
     * travel as a Java array - which CheerpJ hands to JavaScript as a typed array - and the pointer never
     * crosses the boundary at all.
     */
    public static native void bufferDataFromJava(int target, long size, byte[] bytes, int usage);

    /** The same, for a sub-range of a bound buffer. */
    public static native void bufferSubDataFromJava(int target, long offset, byte[] bytes);

    /** A texture upload whose pixels live in the arena. */
    public static native void texImageFromJava(int target, int level, int internalFormat, int width,
                                               int height, int border, int format, int type, byte[] pixels);

    public static native void texSubImageFromJava(int target, int level, int xOffset, int yOffset, int width,
                                                  int height, int format, int type, byte[] pixels);

    /**
     * Pixels *out* of the framebuffer, into a Java array.
     *
     * The array is passed in rather than returned: a Java byte[] reaches JavaScript as an array-like
     * object, and a Uint8Array returned for a Java byte[] fails to convert ("Invalid type conversion
     * attempted"). So JavaScript keeps its own view for WebGL and copies into this one.
     */
    public static native void readPixelsInto(int x, int y, int width, int height, int format, int type,
                                             byte[] into);

    /**
     * Copy results JavaScript wrote into its own heap into the arena, where Java can read them.
     *
     * Every "n" call that returns values through a pointer is in this shape: the JavaScript side has no
     * way to write Java memory, so it writes where it can and this moves the bytes across. Without it the
     * game reads the arena, gets zeros, and binds buffer 0 - which is a silent, confusing nothing.
     */
    public static void copyJsToArena(long jsAddress, long arenaAddress, int bytes) {
        if (jsAddress == 0L || arenaAddress == 0L || bytes <= 0) {
            return;
        }
        byte[] scratch = new byte[bytes];
        nmemReadBytes(jsAddress, scratch, 0, bytes);
        WebMCArena.write(arenaAddress, scratch);
    }

    /**
     * Decode a PNG (and whatever else STB is handed) and leave the pixels in JavaScript's own heap.
     *
     * The image arrives as an address in JavaScript's heap, not as a byte[]: the bytes are pushed across
     * with nmemWriteBytes (which takes an array and returns nothing - that shape is proven) and this call
     * takes only scalars. What comes back is scalars too, because the size is not known until the image is
     * decoded: the address here, then stbWidth/stbHeight/stbComponents/stbScratch.
     */
    public static native void stbLoad(long address, int length);

    /** Where the last decode put its pixels, in JavaScript's heap. */
    public static native long stbScratch();

    public static native int stbWidth();

    public static native int stbHeight();

    public static native int stbComponents();

    /**
     * Rasterise one glyph into a Java array.
     *
     * The array is written from this side, which works; reading a Java array element by element from here
     * does not. glyphPoint is the character, not a font's glyph index - see FreeType.
     */
    public static native void freetypeGlyph(int glyphPoint, int pixelSize, byte[] bitmap);

    /** The metrics of the last rasterised glyph. */
    public static native int freetypeWidth();

    public static native int freetypeRows();

    public static native int freetypePitch();

    public static native int freetypeLeft();

    public static native int freetypeTop();

    public static native int freetypeAdvance();

    /** Give back the scratch the decoder allocated. */
    public static native void stbFreeScratch(long scratch);

    /** Name generation, writing into JavaScript's own heap: copyJsToArena moves the result across. */
    public static native void genBuffers(int count, long pointers);

    /** A buffer bind, reached from Java so the value that arrives is visible. */
    public static native void bindBuffer(int target, int buffer);

    public static native void genVertexArrays(int count, long pointers);

    public static native void log(String message);

    /** And the same for the lines that are already StringBuffered. */
    public static native String logText();
}
