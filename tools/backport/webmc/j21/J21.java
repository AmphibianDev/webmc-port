package webmc.j21;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * The Java 21 methods, in terms of Java 17.
 *
 * The backport rewrites a call to a method Java 17 does not have into a call to one of these. Each is the
 * real definition rather than an approximation: `List.getFirst()` *is* `get(0)` with an emptiness check,
 * and `Math.clamp(value, min, max)` *is* `max(min, min(max, value))` - the JDK's own source says as much.
 *
 * Nothing here is called by the client directly: it exists because the client's bytecode was compiled
 * against a Java 21 library, and this is that library's missing pieces.
 */
public final class J21 {

    private J21() {
    }

    // ------------------------------------------------------------------ List, the seven new methods

    public static Object listGetFirst(List<?> list) {
        if (list.isEmpty()) {
            throw new java.util.NoSuchElementException();
        }
        return list.get(0);
    }

    public static Object listGetLast(List<?> list) {
        if (list.isEmpty()) {
            throw new java.util.NoSuchElementException();
        }
        return list.get(list.size() - 1);
    }

    @SuppressWarnings("unchecked")
    public static void listAddFirst(List<?> list, Object element) {
        ((List<Object>) list).add(0, element);
    }

    @SuppressWarnings("unchecked")
    public static void listAddLast(List<?> list, Object element) {
        ((List<Object>) list).add(element);
    }

    public static Object listRemoveFirst(List<?> list) {
        if (list.isEmpty()) {
            throw new java.util.NoSuchElementException();
        }
        return list.remove(0);
    }

    public static Object listRemoveLast(List<?> list) {
        if (list.isEmpty()) {
            throw new java.util.NoSuchElementException();
        }
        return list.remove(list.size() - 1);
    }

    /** Java 21 returns a reversed *view*; a copy is what Java 17 can do, and the client only reads it. */
    public static List<?> listReversed(List<?> list) {
        List<Object> copy = new ArrayList<>(list);
        Collections.reverse(copy);
        return copy;
    }

    // ------------------------------------------------------------------ Math.clamp

    /** Java 21's set exactly: there is no (int,int,int) - an int value promotes to long. */
    public static int clamp(long value, int min, int max) {
        return (int) Math.max(min, Math.min(max, value));
    }

    public static long clamp(long value, long min, long max) {
        return Math.max(min, Math.min(max, value));
    }

    public static float clamp(float value, float min, float max) {
        return Math.max(min, Math.min(max, value));
    }

    public static double clamp(double value, double min, double max) {
        return Math.max(min, Math.min(max, value));
    }

    // ------------------------------------------------------------------ Thread

    public static long threadId(Thread thread) {
        return thread.getId();
    }
}
