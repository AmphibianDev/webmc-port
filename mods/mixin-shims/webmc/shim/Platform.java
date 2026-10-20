package webmc.shim;

import java.io.RandomAccessFile;
import java.nio.ByteBuffer;

// HANDWRITTEN: stand-ins for platform methods the browser compiler does not have.
//
// The compiler's class library implements a subset of java.base: it has java.nio.ByteBuffer but not
// slice(int, int) (Java 13), java.lang.Thread but not onSpinWait() (Java 9), java.io.RandomAccessFile but
// not getChannel(). A mod or the client calling one of those is normal code that cannot be "fixed" at the
// source - it is already compiled - so the call is pointed at a method here instead during the mod remap.
//
// Two rules for what goes in this class:
//   * if the method can be expressed with what the class library *does* have, it is implemented properly
//     (slice(int, int) is exactly duplicate().position().limit().slice(), which is what the JDK does);
//   * if it cannot - a channel, a socket, a thread barrier - it throws with a message that says so, because
//     a feature that cannot exist in a browser should fail loudly where it is called rather than quietly
//     return something wrong.
public final class Platform {

    private Platform() {
    }

    /** Thread.onSpinWait(): a hint that there is nothing to do. A no-op is the honest implementation. */
    public static void onSpinWait() {
    }

    /** RandomAccessFile.getChannel(): file channels are not available in a browser. */
    public static Object getChannel(RandomAccessFile file) {
        throw new UnsupportedOperationException("file channels are not available in a browser");
    }

    /** ByteBuffer.slice(int, int), which is Java 13: the same thing expressed with what is available. */
    public static ByteBuffer slice(ByteBuffer buffer, int index, int length) {
        ByteBuffer copy = buffer.duplicate();
        copy.position(index);
        copy.limit(index + length);
        return copy.slice();
    }

    /** Class.getPackageName(), which is Java 9. */
    public static String packageName(Class<?> type) {
        return Jdk.packageName(type);
    }
}
