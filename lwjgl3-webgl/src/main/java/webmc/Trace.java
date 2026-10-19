package webmc;

import java.net.URL;
import java.net.URLClassLoader;
import java.util.ArrayDeque;

/**
 * A class loader that writes down what it loads.
 *
 * When a client stops with no exception and the CPU idle, there is no thread stack to read (this JVM
 * answers Thread.getAllStackTraces with an empty map and ThreadMXBean with no threads), so the question
 * "where is it?" has to be answered differently: by watching what it is doing. Every class the client loads
 * goes through here, and the last few names before the stall say where it stopped - a class it is still
 * initialising, or one it asked for and never got.
 *
 * It is a ClassLoader of its own so that the client's classes really do come through it (delegating to the
 * application loader would let the parent load them and hide everything).
 */
public final class Trace extends URLClassLoader {

    private static final int KEEP = 120;
    private static final ArrayDeque<String> RECENT = new ArrayDeque<>();
    private static final Object LOCK = new Object();
    private static volatile int count;

    public Trace(URL[] urls) {
        super(urls, ClassLoader.getPlatformClassLoader());
    }

    @Override
    protected Class<?> loadClass(String name, boolean resolve) throws ClassNotFoundException {
        // Note it before delegating: a class that is *being* initialised and never finishes is exactly the
        // interesting case, and it would never appear if this logged on the way out.
        synchronized (LOCK) {
            RECENT.addLast(name);
            while (RECENT.size() > KEEP) {
                RECENT.removeFirst();
            }
            count++;
            if (count % 200 == 0) {
                System.out.println("TRACE: " + count + " classes loaded, at " + name);
                System.out.flush();
            }
        }
        return super.loadClass(name, resolve);
    }

    static int loaded() {
        return count;
    }

    static String recent() {
        synchronized (LOCK) {
            return String.join(" <- ", RECENT);
        }
    }
}
