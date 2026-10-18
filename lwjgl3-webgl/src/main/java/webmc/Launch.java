package webmc;

import java.io.InputStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

/**
 * Prepare the filesystem a browser JVM cannot prepare, then start a main class - and if it does not start,
 * say why.
 *
 * Two jobs, both from what this route measured rather than assumed:
 *
 * <ul>
 *   <li>A browser JVM reports a failed main class as one line ("Unable to initialize main class ...") with
 *       no cause and no stack trace, because the failure happens in a static initialiser. This prints every
 *       cause in the chain instead.
 *   <li>CheerpJ's /str mount - what a page can write - is flat: a path with a directory in it is refused
 *       with "Directories are not supported", silently. Minecraft's assets are a tree. Java, however, can
 *       create real directories under /files, so the assets are fetched here, from the page's own site, and
 *       written where the client asks for them.
 * </ul>
 *
 * It is deliberately dependency-free and is written into the client jar by tools/jvm_bundle.py, because
 * CheerpJ would not load it from our own jar.
 */
public final class Launch {

    private Launch() {
    }

    public static void main(String[] arguments) {
        if (arguments.length == 0) {
            System.out.println("LAUNCH FAILED: no main class was given");
            return;
        }
        // Arguments, not system properties: a property set by the page did not reach this JVM, and an
        // argument cannot fail to arrive. webmc.Launch <target> <assets-url|-> <client args...>
        String target = arguments[0];
        String assetsUrl = arguments.length > 1 ? arguments[1] : "-";
        String[] rest = new String[Math.max(0, arguments.length - 2)];
        System.arraycopy(arguments, Math.min(2, arguments.length), rest, 0, rest.length);

        try {
            prepare(assetsUrl);
        } catch (Throwable problem) {
            say("PREPARE FAILED: " + problem);
        }

        // The watchdogs belong to this run, not to runs of their own: a cheerpjRunMain before the client's
        // keeps the client from starting (measured - the client stopped logging entirely once they were
        // separate). They are here to witness a stall, so they have to share its run.
        try {
            Diag.start(240);
            Tail.start(45, 40);
        } catch (Throwable problem) {
            say("watchdogs failed: " + problem);
        }

        say("LAUNCH: " + target);
        try {
            Class<?> type = Class.forName(target, true, Launch.class.getClassLoader());
            Method entry = type.getMethod("main", String[].class);
            // and a reporter, so the last classes loaded before a stall are readable afterwards
            Thread reporter = new Thread(() -> {
                for (int round = 0; round < 60; round++) {
                    try {
                        Thread.sleep(30_000L);
                        say("TRACE round " + (round + 1) + ", " + Trace.loaded() + " class(es), last: "
                            + Trace.recent());
                    } catch (Throwable ignored) {
                        return;
                    }
                }
            }, "webmc-trace");
            reporter.setDaemon(true);
            reporter.start();
            entry.invoke(null, (Object) rest);
            System.out.println("LAUNCH: " + target + " returned");
        } catch (InvocationTargetException wrapped) {
            report(target, wrapped.getCause() == null ? wrapped : wrapped.getCause());
        } catch (Throwable problem) {
            report(target, problem);
        }
    }

    /**
     * Fetch the asset tree the page cannot mount, into the filesystem Java can write.
     *
     * webmc.assets is the page's URL for the bundle's assets and webmc.assets_root is where they go; the
     * listing is one flat file of paths, so nothing has to guess what the tree contains. Skipped when the
     * index is already there, which is what makes a reload cheap.
     */
    private static void prepare(String base) throws Exception {
        String root = "/files/assets";
        // The page mounts each asset flat (/str/asset-N) beside a flat listing, because it cannot write a
        // directory and this JVM cannot fetch: reading a flat file works, writing a tree works, and the two
        // halves meet here.
        Path map = Path.of("/str/assets-map.txt");
        if (Files.exists(map)) {
            if (Files.exists(Path.of(root, "indexes"))) {
                say("PREPARE: assets already at " + root);
                return;
            }
            prepareFromListing(map, Path.of(root));
            return;
        }
        if (base == null || base.isEmpty() || base.equals("-")) {
            return;
        }
        Path assets = Path.of(root);
        if (Files.exists(assets.resolve("indexes"))) {
            say("PREPARE: assets already at " + assets);
            return;
        }
        String listing = read(base + "list.txt");
        if (listing == null || listing.isEmpty()) {
            say("PREPARE FAILED: " + base + "list.txt is empty");
            return;
        }
        int written = 0;
        int failed = 0;
        long started = System.currentTimeMillis();
        for (String line : listing.split("\n")) {
            String path = line.trim();
            if (path.isEmpty()) {
                continue;
            }
            Path destination = assets.resolve(path);
            try {
                Files.createDirectories(destination.getParent());
                try (InputStream stream = new URL(base + path).openStream()) {
                    Files.copy(stream, destination, StandardCopyOption.REPLACE_EXISTING);
                }
                written++;
            } catch (Throwable problem) {
                failed++;
                if (failed <= 3) {
                    say("  asset " + path + ": " + problem);
                }
            }
        }
        say("PREPARE: " + written + " asset(s) into " + assets + ", " + failed + " failed, "
            + (System.currentTimeMillis() - started) + " ms");
    }

    /** Copy what the page mounted flat into the tree the client asks for. */
    private static void prepareFromListing(Path map, Path assets) throws Exception {
        int written = 0;
        int failed = 0;
        long started = System.currentTimeMillis();
        for (String line : Files.readAllLines(map)) {
            String[] parts = line.trim().split(" ", 2);
            if (parts.length != 2) {
                continue;
            }
            Path source = Path.of("/str").resolve(parts[0]);
            Path destination = assets.resolve(parts[1]);
            try {
                Files.createDirectories(destination.getParent());
                Files.copy(source, destination, StandardCopyOption.REPLACE_EXISTING);
                written++;
            } catch (Throwable problem) {
                failed++;
                if (failed <= 3) {
                    say("  asset " + parts[1] + ": " + problem);
                }
            }
        }
        say("PREPARE: " + written + " asset(s) into " + assets + ", " + failed + " failed, "
            + (System.currentTimeMillis() - started) + " ms");
    }

    /** Both to the console and to a file: a browser JVM's stdout is not always flushed, and evidence that
     *  landed nowhere is not evidence. */
    private static void say(String line) {
        // Three ways out, because a browser JVM routes stdout to nowhere in this build: the shim's log is
        // the one channel that always reaches the page's console (that is how the boot test's steps are
        // read), and it is reached by reflection so this class stays dependency-free and can live in the
        // client jar. The file is the third: evidence that survives a run.
        System.out.println(line);
        System.out.flush();
        System.err.println(line);
        try {
            Class<?> webmc = Class.forName("org.lwjgl.system.WebMC");
            webmc.getMethod("log", String.class).invoke(null, line);
        } catch (Throwable ignored) {
            // no shim on the classpath: the other channels will have to do
        }
        // and through the client's own logger, which is the one channel measured to reach the page's log
        // panel and the browser console: Minecraft's own lines come out this way.
        try {
            Class<?> logManager = Class.forName("org.apache.logging.log4j.LogManager");
            Object logger = logManager.getMethod("getLogger", String.class).invoke(null, "webmc.Launch");
            logger.getClass().getMethod("info", String.class).invoke(logger, line);
        } catch (Throwable ignored) {
            // no log4j (a minimal launch): the other channels will have to do
        }
        try {
            Path log = Path.of("/files/launch.log");
            Files.createDirectories(log.getParent());
            Files.writeString(log, line + "\n", java.nio.file.StandardOpenOption.CREATE,
                              java.nio.file.StandardOpenOption.APPEND);
        } catch (Throwable ignored) {
            // the console is enough
        }
    }

    private static String read(String url) {
        try (InputStream stream = new URL(url).openStream()) {
            return new String(stream.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
        } catch (Throwable problem) {
            System.out.println("PREPARE FAILED: " + url + " : " + problem);
            return null;
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
