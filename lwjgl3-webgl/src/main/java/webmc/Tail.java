package webmc;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * Read the client's own log back to us.
 *
 * A stalled client prints nothing more, and this JVM will not give up a thread stack
 * (Thread.getAllStackTraces answers with an empty map and ThreadMXBean with no threads), so the only
 * witness left is the file the client writes itself: /files/game/logs/latest.log. A daemon thread here
 * tails it to stdout, which is a channel that does reach the page.
 *
 *     webmc.Tail &lt;seconds-between-reads&gt; &lt;reads&gt;
 */
public final class Tail {

    /**
     * The stream as it was *before* the client started.
     *
     * Minecraft replaces System.out with a log4j-backed stream while it initialises, and prints through a
     * daemon thread then go to a stream that only reaches the client's own log file - i.e. to the one place
     * this is trying to read from. Holding the original is what keeps the tail visible.
     */
    private static final java.io.PrintStream OUT = System.out;

    private Tail() {
    }

    public static void main(String[] arguments) {
        start(30, 40);
    }

    /** Started by webmc.Launch from inside the client's own run: a second cheerpjRunMain before it stops
     *  the client from starting at all (measured: with two extra runs the client never logged a line). */
    public static void start(int interval, int reads) {
        latest("TAIL: watching /files/game/logs/latest.log every " + interval + "s");
        Thread thread = new Thread(() -> {
            for (int index = 0; index < reads; index++) {
                try {
                    Thread.sleep(interval * 1000L);
                    tail(index + 1);
                } catch (Throwable problem) {
                    latest("TAIL FAILED: " + problem);
                    return;
                }
            }
        }, "webmc-tail");
        thread.setDaemon(true);
        thread.start();
    }

    private static void tail(int round) {
        StringBuilder out = new StringBuilder();
        out.append("=== client log, read ").append(round).append(" ===\n");
        // The client's crash report is the thing that names the phase it failed in - "Description:
        // Initializing game" - and it is written even when the report builder itself goes wrong.
        try {
            Path reports = Path.of("/files/game/crash-reports");
            if (Files.isDirectory(reports)) {
                java.util.List<Path> found = new java.util.ArrayList<>();
                try (java.util.stream.Stream<Path> stream = Files.list(reports)) {
                    stream.filter(Files::isRegularFile).forEach(found::add);
                }
                found.sort(java.util.Comparator.comparing(Path::toString));
                if (found.isEmpty()) {
                    out.append("  crash-reports: empty\n");
                }
                for (Path report : found) {
                    out.append("  === ").append(report.getFileName()).append(" ===\n");
                    out.append(Files.readString(report));
                    out.append('\n');
                }
            } else {
                out.append("  /files/game/crash-reports does not exist\n");
            }
        } catch (Throwable problem) {
            out.append("  crash-reports failed: ").append(problem).append('\n');
        }
        for (String candidate : new String[] { "/files/game/logs/latest.log", "/files/game/logs/debug.log" }) {
            Path log = Path.of(candidate);
            try {
                if (!Files.exists(log)) {
                    out.append("  ").append(candidate).append(" does not exist\n");
                    continue;
                }
                List<String> lines = Files.readAllLines(log);
                out.append("  ").append(candidate).append(": ").append(lines.size()).append(" line(s)\n");
                int from = Math.max(0, lines.size() - 25);
                for (int index = from; index < lines.size(); index++) {
                    out.append("  | ").append(lines.get(index)).append('\n');
                }
            } catch (Throwable problem) {
                out.append("  ").append(candidate).append(" failed: ").append(problem).append('\n');
            }
        }
        latest(out.toString());
    }

    /** Through the saved stream and through the shim's log, which is the channel the page definitely reads. */
    private static void latest(String text) {
        for (String line : text.split("\n")) {
            OUT.println(line);
            try {
                Class<?> webmc = Class.forName("org.lwjgl.system.WebMC");
                webmc.getMethod("log", String.class).invoke(null, line);
            } catch (Throwable ignored) {
                // no shim on the classpath
            }
        }
        OUT.flush();
        System.out.flush();
    }
}
