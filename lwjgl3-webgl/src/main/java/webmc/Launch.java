package webmc;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

/**
 * Start a main class and, if it does not start, say why.
 *
 * A browser JVM reports a failed main class as one line - "Unable to initialize main class
 * net.minecraft.client.main.Main" - with no cause, no exception type and no stack trace, because the
 * failure happened in a static initialiser before main was entered. That single line is the most
 * expensive message in this route: it says a class could not be loaded and nothing about which one.
 *
 * This class is deliberately dependency-free (System.out only) and is written *into the client jar* by
 * tools/jvm_bundle.py rather than shipped in the shim's jar: CheerpJ's class loader refused to find it
 * in our own jar on the classpath, and the client's jar is one it demonstrably reads.
 */
public final class Launch {

    private Launch() {
    }

    public static void main(String[] arguments) {
        if (arguments.length == 0) {
            System.out.println("LAUNCH FAILED: no main class was given");
            return;
        }
        String target = arguments[0];
        String[] rest = new String[arguments.length - 1];
        System.arraycopy(arguments, 1, rest, 0, rest.length);
        System.out.println("LAUNCH: " + target);
        try {
            Class<?> type = Class.forName(target, true, Launch.class.getClassLoader());
            Method entry = type.getMethod("main", String[].class);
            entry.invoke(null, (Object) rest);
            System.out.println("LAUNCH: " + target + " returned");
        } catch (InvocationTargetException wrapped) {
            report(target, wrapped.getCause() == null ? wrapped : wrapped.getCause());
        } catch (Throwable problem) {
            report(target, problem);
        }
    }

    private static void report(String target, Throwable problem) {
        System.out.println("LAUNCH FAILED: " + target + " could not start");
        for (Throwable cause = problem; cause != null; cause = cause.getCause()) {
            System.out.println("  cause: " + cause.getClass().getName() + ": " + cause.getMessage());
            StackTraceElement[] trace = cause.getStackTrace();
            for (int index = 0; index < trace.length && index < 14; index++) {
                System.out.println("    at " + trace[index]);
            }
            if (cause.getCause() == cause) {
                break;
            }
        }
        System.out.flush();
    }
}
