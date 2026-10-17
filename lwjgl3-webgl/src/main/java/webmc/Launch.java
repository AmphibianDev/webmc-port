package webmc;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

/**
 * Start a main class and, if it does not start, say why.
 *
 * A browser JVM reports a failed main class as one line - "Unable to initialize main class
 * net.minecraft.client.main.Main" - with no cause, no exception type and no stack trace, because the
 * failure happened in a static initialiser before main was entered. That single line is the most
 * expensive message in this whole route: it says a class could not be loaded and nothing about which
 * class was missing.
 *
 * So the launch goes through here instead: this loads the class, calls main, and prints every cause in
 * the chain with its stack trace. The page passes the real main class as the first argument.
 */
public final class Launch {

    private Launch() {
    }

    public static void main(String[] arguments) {
        if (arguments.length == 0) {
            say("LAUNCH FAILED: no main class was given");
            return;
        }
        String target = arguments[0];
        String[] rest = new String[arguments.length - 1];
        System.arraycopy(arguments, 1, rest, 0, rest.length);
        say("[launch] " + target);
        try {
            Class<?> type = Class.forName(target, true, Launch.class.getClassLoader());
            Method entry = type.getMethod("main", String[].class);
            entry.invoke(null, (Object) rest);
            say("[launch] " + target + " returned");
        } catch (InvocationTargetException wrapped) {
            report(target, wrapped.getCause() == null ? wrapped : wrapped.getCause());
        } catch (Throwable problem) {
            report(target, problem);
        }
    }

    private static void say(String line) {
        // both: a browser JVM's stdout is not always the page's console, and the shim's log always is
        System.out.println(line);
        org.lwjgl.system.WebMC.log(line);
    }

    private static void report(String target, Throwable problem) {
        say("LAUNCH FAILED: " + target + " could not start");
        for (Throwable cause = problem; cause != null; cause = cause.getCause()) {
            say("  " + cause.getClass().getName() + ": " + cause.getMessage());
            StackTraceElement[] trace = cause.getStackTrace();
            for (int index = 0; index < trace.length && index < 12; index++) {
                say("    at " + trace[index]);
            }
            if (cause.getCause() == cause) {
                break;
            }
        }
    }
}
