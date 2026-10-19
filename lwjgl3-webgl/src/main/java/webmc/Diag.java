package webmc;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

/**
 * Where is the client stuck? A browser JVM shows nothing while Java is inside a native, its stdout does not
 * reach the page, and a stalled client has no output at all - so the only way to see it is to ask from the
 * other side, which is what this is for.
 *
 * The page starts this (it returns immediately) and then starts the client; a daemon thread waits a few
 * minutes and writes every thread's stack to /files/diag.txt, which the page reads back through
 * cheerpjRunLibrary and logs. That is a stack trace for a JVM that cannot print one.
 *
 *     webmc.Diag &lt;seconds-before-dumping&gt;
 */
public final class Diag {

    private static final Path REPORT = Path.of("/files/diag.txt");

    /**
     * The last native calls the shim made, pushed in from JavaScript.
     *
     * This is the one channel that can see the stall from the side that knows: the shim is in the middle of
     * every GL and GLFW call, so the last name it was asked for is the call a stalled client is sitting in.
     * JavaScript calls this through CheerpJ's JVM handle, which every native is given as its first argument.
     */
    private static final java.util.ArrayDeque<String> CALLS = new java.util.ArrayDeque<>();
    private static final Object LOCK = new Object();

    public static void note(String call) {
        synchronized (LOCK) {
            CALLS.addLast(call);
            while (CALLS.size() > 40) {
                CALLS.removeFirst();
            }
        }
    }

    public static String calls() {
        synchronized (LOCK) {
            return String.join(" <- ", CALLS);
        }
    }

    private Diag() {
    }

    public static void main(String[] arguments) {
        start(240);
    }

    /** Started by webmc.Launch, for the same reason as Tail.start. */
    public static void start(int delay) {
        say("DIAG: watchdog started, dumping in " + delay + "s");
        Thread reporter = new Thread(() -> {
            for (int round = 0; round < 40; round++) {
                try {
                    Thread.sleep(30_000L);
                    say("DIAG round " + (round + 1) + ", last native calls: " + calls());
                } catch (Throwable problem) {
                    return;
                }
            }
        }, "webmc-calls");
        reporter.setDaemon(true);
        reporter.start();
        Thread watchdog = new Thread(() -> {
            try {
                Thread.sleep(delay * 1000L);
                dump();
                Thread.sleep(delay * 1000L);
                dump();
            } catch (Throwable problem) {
                say("DIAG FAILED: " + problem);
            }
        }, "webmc-diag");
        watchdog.setDaemon(true);
        watchdog.start();
    }

    /**
     * Print the exception a client died of, and the phase it named, then throw.
     *
     * The client normally hands this to its crash-report builder, which under this JVM throws its own
     * NullPointerException before writing anything - so the cause and the phase are both lost. This is what
     * Main's handler is redirected to (tools/backport/TraceMain.java), and it is the difference between
     * "the client did not start" and a stack trace that names it.
     */
    public static RuntimeException die(Throwable problem, String description) {
        say("CLIENT FAILED: " + description);
        for (Throwable cause = problem; cause != null; cause = cause.getCause()) {
            say("  cause: " + cause.getClass().getName() + ": " + cause.getMessage());
            for (StackTraceElement frame : cause.getStackTrace()) {
                say("    at " + frame);
            }
            if (cause.getCause() == cause) {
                break;
            }
        }
        return new RuntimeException(description, problem);
    }

    /**
     * Say what the client is doing.
     *
     * Called from method entry points that tools/backport/TraceCalls.java rewrites into the client, so the
     * page's log shows the client's own progress through phases that otherwise log nothing at all. Every
     * mark also goes to /files/marks.log, which is the copy that survives a stalled run.
     */
    private static final java.util.ArrayDeque<String> MARKS = new java.util.ArrayDeque<>();

    private static volatile String lastMark;
    private static int markCount;
    private static long lastReport = System.currentTimeMillis();

    public static void mark(String where) {
        synchronized (MARKS) {
            if (where.equals(lastMark)) {
                return;                      // the same method re-entering: one line is enough
            }
            lastMark = where;
            lastMark = where;
            MARKS.addLast(where);
            while (MARKS.size() > 300) {
                MARKS.removeFirst();
            }
            markCount++;
            long now = System.currentTimeMillis();
            if (now - lastReport > 30_000L) {
                lastReport = now;
                // Reported from inside the client's own thread: a watchdog thread does not get scheduled
                // while the render thread is busy (this JVM's threads are cooperative), so the only place
                // a progress report can be written from is the path the client itself is running.
                say("MARKS at " + markCount + ": " + String.join(" <- ", MARKS));
            }
        }
    }

    public static String marks() {
        synchronized (MARKS) {
            return String.join(" <- ", MARKS) + "  (total " + markCount + ")";
        }
    }

    /** Report the latest marks every 30 seconds: the ring buffer is the current state, the log is history. */
    public static void startMarkReporter() {
        Thread reporter = new Thread(() -> {
            for (int round = 0; round < 400; round++) {
                try {
                    Thread.sleep(30_000L);
                    say("MARKS round " + (round + 1) + ": " + marks());
                } catch (Throwable problem) {
                    return;
                }
            }
        }, "webmc-marks");
        reporter.setDaemon(true);
        reporter.start();
    }

    /** The report so far, for the page to display. */
    public static String report() {
        try {
            return Files.exists(REPORT) ? Files.readString(REPORT) : "DIAG: nothing yet";
        } catch (Throwable problem) {
            return "DIAG FAILED: " + problem;
        }
    }

    private static void dump() {
        StringBuilder text = new StringBuilder();
        text.append("DIAG: active=").append(Thread.activeCount()).append('\n');
        int counted = 0;
        // ThreadMXBean, not Thread.getAllStackTraces: the latter answers with an empty map under this JVM
        // (measured), which is a stub rather than a thread model, and the management API is a different path
        // into whatever thread information the runtime does have.
        try {
            java.lang.management.ThreadMXBean bean = java.lang.management.ManagementFactory.getThreadMXBean();
            java.lang.management.ThreadInfo[] infos = bean.dumpAllThreads(true, true);
            counted = infos == null ? 0 : infos.length;
            if (infos != null) {
                for (java.lang.management.ThreadInfo info : infos) {
                    text.append('\n').append(info.getThreadName()).append(" state=")
                        .append(info.getThreadState()).append('\n');
                    for (StackTraceElement frame : info.getStackTrace()) {
                        text.append("    at ").append(frame).append('\n');
                    }
                }
            }
        } catch (Throwable problem) {
            text.append("mxbean failed: ").append(problem).append('\n');
        }
        if (counted == 0) {
            // last resort: at least the thread that is asking, which is not the one that matters but says
            // whether the JVM can produce any stack at all
            text.append("no threads from the mxbean; this thread:\n");
            for (StackTraceElement frame : Thread.currentThread().getStackTrace()) {
                text.append("    at ").append(frame).append('\n');
            }
        }
        try {
            Files.createDirectories(REPORT.getParent());
            Files.writeString(REPORT, text.toString());
            say("DIAG: wrote " + counted + " thread stack(s), " + text.length() + " chars");
        } catch (Throwable problem) {
            say("DIAG FAILED: " + problem);
        }
    }

    private static void say(String line) {
        System.out.println(line);
        System.out.flush();
        try {
            Class<?> webmc = Class.forName("org.lwjgl.system.WebMC");
            webmc.getMethod("log", String.class).invoke(null, line);
        } catch (Throwable ignored) {
            // no shim on the classpath
        }
    }
}
