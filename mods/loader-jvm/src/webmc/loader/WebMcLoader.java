package webmc.loader;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.URL;
import java.net.URLClassLoader;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.logging.LogManager;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * WebMC's own loader: the one class between the page and the game.
 *
 * <p>The page writes a plan (a {@code .properties} file) and the jars it names into the browser JVM's
 * filesystem, then starts this class. Everything after that is decided here, from the plan:
 *
 * <pre>
 *   mode=vanilla   start the game's own main class. Mods that carry a webmc.mod.json are loaded first
 *                  from a URLClassLoader over mods.dir, so they run inside the game's JVM and can use
 *                  the game's own classes.
 *   mode=fabric    hand the game to Fabric Loader: set fabric.gameJarPath / fabric.addMods /
 *                  fabric.side, then start net.fabricmc.loader.impl.launch.knot.KnotClient. Fabric
 *                  transforms the game's classes at load time; that is what a Fabric mod needs, and
 *                  no amount of work here substitutes for it.
 *   mode=legacyfabric  the same, with Legacy Fabric's loader - which is what carries 1.8.9 - 1.12.2.
 * </pre>
 *
 * <p>Why this class exists at all, given the page could call {@code cheerpjRunMain} on any class:
 *
 * <ul>
 *   <li><b>Vanilla has no mod door.</b> A jar on the classpath is invisible unless something starts
 *       it. This loader is that something, and it is the only way a mod runs in an unmodified vanilla
 *       client.</li>
 *   <li><b>The plan is data, not the URL.</b> A launch is described by a file, so a mod set can be
 *       changed without rebuilding the page or re-generating a client.</li>
 *   <li><b>Failures are reported.</b> A JVM that dies silently is indistinguishable from one that is
 *       still loading. Every exit path here writes a line the page can show, including the cause.</li>
 * </ul>
 *
 * <p>Compiled for Java 8 bytecode on purpose: the same jar runs in CheerpJ's Java 8 mode (the
 * LWJGL 2 clients) and its Java 17 mode (up to 1.20.4). Target that is older than the runtime is
 * free; a target that is newer is a class file the JVM refuses to load.
 */
public final class WebMcLoader {

    private static final String DEFAULT_PLAN = "/str/webmc/plan.properties";
    private static final List<String> REPORT = new CopyOnWriteArrayList<String>();

    private WebMcLoader() {
    }

    public static void main(String[] args) {
        int code = run(args.length > 0 ? args[0] : DEFAULT_PLAN,
                        args.length > 1 ? subarray(args, 1) : new String[0]);
        System.exit(code);
    }

    /** Runs the plan; returns the exit code. Kept out of main so a test can call it directly. */
    public static int run(String planPath, String[] gameArgs) {
        Properties plan;
        try {
            plan = readPlan(planPath);
        } catch (Exception error) {
            report("FATAL cannot read the launch plan at " + planPath + ": " + error);
            return 2;
        }

        String mode = plan.getProperty("mode", "vanilla");
        String version = plan.getProperty("version", "?");
        String gameClass = plan.getProperty("game.class", "net.minecraft.client.Minecraft");
        List<String> modJars = new ArrayList<String>();
        List<String> modIds = new ArrayList<String>();
        int modCount = number(plan.getProperty("mod.count", "0"));
        for (int i = 0; i < modCount; i++) {
            String file = plan.getProperty("mod." + i + ".file", "");
            if (file.length() > 0) {
                modJars.add(file);
                modIds.add(plan.getProperty("mod." + i + ".id", new File(file).getName()));
            }
        }

        banner(version, mode, plan, modJars);

        try {
            if (mode.startsWith("fabric")) {
                return launchFabric(plan, mode, version, modJars, gameArgs);
            }
            return launchVanilla(plan, version, gameClass, modJars, modIds, gameArgs);
        } catch (Throwable error) {
            report("FATAL the launch failed: " + describe(error));
            report("      mode=" + mode + " game=" + gameClass
                   + " mods=" + (modJars.isEmpty() ? "none" : String.valueOf(modJars.size())));
            for (StackTraceElement frame : error.getStackTrace()) {
                if (frame.getClassName().startsWith("webmc.")) {
                    report("      at " + frame);
                }
            }
            return 1;
        }
    }

    // ------------------------------------------------------------------ vanilla

    /**
     * Vanilla: our own mod door, then the game.
     *
     * The mod jars go into a {@link URLClassLoader} whose parent is this class's loader, which is the
     * loader that has the game on it. A mod therefore sees the game's classes; the game does not see
     * the mod's, which is the honest half of the deal - a mod can *use* the game, and only Fabric can
     * *change* it.
     */
    static int launchVanilla(Properties plan, String version, String gameClass, List<String> modJars,
                             List<String> modIds, String[] gameArgs) throws Exception {
        URLClassLoader mods = null;
        if (!modJars.isEmpty()) {
            URL[] urls = new URL[modJars.size()];
            for (int i = 0; i < modJars.size(); i++) {
                urls[i] = new URL("file:" + modJars.get(i));
            }
            mods = new URLClassLoader(urls, WebMcLoader.class.getClassLoader());
        }

        int loaded = 0;
        if (mods != null) {
            WebMcApi api = new Api(version, plan.getProperty("mode", "vanilla"));
            for (int i = 0; i < modJars.size(); i++) {
                String jar = modJars.get(i);
                String id = i < modIds.size() ? modIds.get(i) : new File(jar).getName();
                if (startMod(mods, jar, id, api)) {
                    loaded++;
                }
            }
            report("mods: " + loaded + " of " + modJars.size() + " started");
        } else {
            report("mods: none");
        }

        report("starting " + gameClass);
        Class<?> entry = Class.forName(gameClass, true, mods != null ? mods : WebMcLoader.class.getClassLoader());
        Method main = entry.getMethod("main", String[].class);
        main.invoke(null, (Object) gameArgs);
        report("the game returned normally");
        return 0;
    }

    /** Reads one jar's {@code webmc.mod.json}, starts its entrypoint. Returns whether it did. */
    static boolean startMod(ClassLoader loader, String jarPath, String fallbackId, WebMcApi api) {
        String descriptor = readEntry(jarPath, "webmc.mod.json");
        if (descriptor == null) {
            // Not one of ours. It stays on the classpath (it may be a library another mod needs) and
            // says so, because "my mod did nothing" is otherwise the hardest bug to see.
            report("mod " + fallbackId + ": no webmc.mod.json - on the classpath, not started"
                   + " (a Fabric mod is started by Fabric, see mode=fabric)");
            return false;
        }
        String entrypoint = value(descriptor, "entrypoint");
        String id = value(descriptor, "id");
        String modVersion = value(descriptor, "version");
        if (entrypoint == null) {
            report("mod " + fallbackId + ": webmc.mod.json names no entrypoint");
            return false;
        }
        try {
            Class<?> type = Class.forName(entrypoint, true, loader);
            if (!WebMcMod.class.isAssignableFrom(type)) {
                report("mod " + fallbackId + ": " + entrypoint + " does not implement WebMcMod");
                return false;
            }
            WebMcMod mod = (WebMcMod) type.getDeclaredConstructor().newInstance();
            mod.onLoad(api);
            report("mod " + (id == null ? fallbackId : id) + " " + (modVersion == null ? "" : modVersion)
                   + ": started (" + entrypoint + ")");
            return true;
        } catch (Throwable error) {
            report("mod " + fallbackId + ": failed: " + describe(error));
            return false;
        }
    }

    // ------------------------------------------------------------------ fabric

    /**
     * Fabric: properties first, then Fabric's own entry class.
     *
     * Fabric reads its configuration from system properties, and it expects a launcher to have set
     * them. Two matter here:
     *
     * <pre>
     *   fabric.gameJarPath  the game is not on the classpath the way a launcher would put it
     *   fabric.addMods      the mods are jars in the browser's filesystem, not entries in ./mods
     * </pre>
     *
     * With those set, KnotClient does the rest: it remaps the game's obfuscated names to
     * intermediary, runs the mixins, and starts the game through them. This is the same contract the
     * official launcher uses, which is why a real Fabric mod works here without knowing it is
     * running in a tab.
     */
    static int launchFabric(Properties plan, String mode, String version, List<String> modJars,
                            String[] gameArgs) throws Exception {
        String loaderMain = plan.getProperty("loader.main",
                "net.fabricmc.loader.impl.launch.knot.KnotClient");
        String gameJar = plan.getProperty("game.jar", "");
        String libraries = plan.getProperty("loader.jars", "");

        set("fabric.gameJarPath", gameJar);
        set("fabric.side", "client");
        set("fabric.development", "false");
        set("fabric.gameVersion", version);
        set("fabric.modsFolder", "/str/mods");
        if (!modJars.isEmpty()) {
            StringBuilder joined = new StringBuilder();
            for (String jar : modJars) {
                if (joined.length() > 0) {
                    joined.append(File.pathSeparatorChar);
                }
                joined.append(jar);
            }
            set("fabric.addMods", joined.toString());
        }
        report("fabric: loader jars=" + (libraries.isEmpty() ? "from the classpath" : libraries));
        report("fabric: gameJarPath=" + gameJar + " addMods=" + modJars.size() + " jar(s)");
        report("starting " + loaderMain);
        Class<?> entry = Class.forName(loaderMain, true, WebMcLoader.class.getClassLoader());
        Method main = entry.getMethod("main", String[].class);
        main.invoke(null, (Object) gameArgs);
        report("Fabric returned normally");
        return 0;
    }

    private static void set(String key, String value) {
        if (value != null && value.length() > 0) {
            System.setProperty(key, value);
        }
    }

    // ------------------------------------------------------------------ the plan

    static Properties readPlan(String path) throws Exception {
        Properties plan = new Properties();
        InputStream stream = null;
        try {
            if (new File(path).isFile()) {
                stream = new FileInputStream(path);
            } else {
                stream = WebMcLoader.class.getResourceAsStream(path);
            }
            if (stream == null) {
                throw new java.io.FileNotFoundException(path);
            }
            plan.load(stream);
        } finally {
            if (stream != null) {
                stream.close();
            }
        }
        return plan;
    }

    private static void banner(String version, String mode, Properties plan, List<String> modJars) {
        report("");
        report("WebMC loader - Minecraft " + version + " (" + mode + "), "
               + modJars.size() + " mod jar(s)");
        report("loader: webmc.loader.WebMcLoader on Java "
               + System.getProperty("java.version", "?") + ", game jar "
               + plan.getProperty("game.jar", "?"));
    }

    // ------------------------------------------------------------------ reporting

    /**
     * One line to whoever is watching: the page (through the native the page registers, when there is
     * one) and standard output (always, which is what makes this class testable outside a browser).
     */
    static void report(String line) {
        REPORT.add(line);
        System.out.println("[webmc] " + line);
        try {
            nativeReport(line);
        } catch (Throwable ignored) {
            // no page attached (a plain JVM, or the native was not registered): stdout is enough
        }
    }

    /** Registered by the page through cheerpjInit({natives: {...}}). Absent everywhere else. */
    private static native void nativeReport(String line);

    /** The lines reported so far - for a test, and for a page that polls instead of listening. */
    public static String[] report() {
        return REPORT.toArray(new String[0]);
    }

    // ------------------------------------------------------------------ small helpers

    private static String[] subarray(String[] args, int from) {
        String[] rest = new String[args.length - from];
        System.arraycopy(args, from, rest, 0, rest.length);
        return rest;
    }

    private static int number(String text) {
        try {
            return Integer.parseInt(text.trim());
        } catch (NumberFormatException error) {
            return 0;
        }
    }

    /** Reads one entry out of a jar as text, or null when the jar has no such entry. */
    static String readEntry(String jarPath, String name) {
        ZipInputStream zip = null;
        try {
            zip = new ZipInputStream(new FileInputStream(jarPath));
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                if (name.equals(entry.getName())) {
                    StringBuilder text = new StringBuilder();
                    byte[] buffer = new byte[4096];
                    int read;
                    while ((read = zip.read(buffer)) > 0) {
                        text.append(new String(buffer, 0, read, "UTF-8"));
                    }
                    return text.toString();
                }
            }
        } catch (Exception error) {
            report("  " + new File(jarPath).getName() + ": cannot be read: " + error);
        } finally {
            try {
                if (zip != null) {
                    zip.close();
                }
            } catch (Exception ignored) {
                // closing is not worth reporting
            }
        }
        return null;
    }

    /**
     * The one value this loader needs out of a mod descriptor, without a JSON parser.
     *
     * A real JSON parser would be the right answer for a real manifest; {@code webmc.mod.json} is
     * deliberately four flat string fields, so a 60-line reader beats a dependency that has to be
     * fetched before the game can start. Anything nested is ignored on purpose.
     */
    static String value(String json, String key) {
        int at = json.indexOf("\"" + key + "\"");
        if (at < 0) {
            return null;
        }
        int colon = json.indexOf(':', at);
        int quote = json.indexOf('"', colon + 1);
        if (colon < 0 || quote < 0) {
            return null;
        }
        int end = json.indexOf('"', quote + 1);
        return end < 0 ? null : json.substring(quote + 1, end);
    }

    private static String describe(Throwable error) {
        Throwable cause = error;
        if (error instanceof InvocationTargetException && error.getCause() != null) {
            cause = error.getCause();
        }
        StringBuilder text = new StringBuilder(cause.getClass().getName());
        if (cause.getMessage() != null) {
            text.append(": ").append(cause.getMessage());
        }
        return text.toString();
    }

    // ------------------------------------------------------------------ the api

    /** The {@link WebMcApi} a vanilla mod is given. */
    static final class Api implements WebMcApi {
        private final String version;
        private final String mode;
        private final Map<String, String> bus = new ConcurrentHashMap<String, String>();
        private final List<Timer> timers = new CopyOnWriteArrayList<Timer>();
        private volatile long ticks = 0;
        private volatile boolean running = true;
        private final Thread clock;

        Api(String version, String mode) {
            this.version = version;
            this.mode = mode;
            this.clock = new Thread(new Runnable() {
                public void run() {
                    tickLoop();
                }
            }, "webmc-mod-clock");
            // A daemon thread cannot keep the JVM alive after the game returns. The game is what
            // keeps a process running, and a mod's clock must not be able to outlive it.
            this.clock.setDaemon(true);
            this.clock.start();
        }

        public String gameVersion() {
            return version;
        }

        public String mode() {
            return mode;
        }

        public void log(String message) {
            report(message);
        }

        public void set(String key, String value) {
            if (value == null) {
                bus.remove(key);
            } else {
                bus.put(key, value);
            }
        }

        public String get(String key) {
            return bus.get(key);
        }

        public void every(int millis, Runnable task) {
            timers.add(new Timer(Math.max(1, millis), task));
        }

        public long ticks() {
            return ticks;
        }

        public void stop() {
            running = false;
        }

        private void tickLoop() {
            while (running) {
                long now = System.currentTimeMillis();
                for (Timer timer : timers) {
                    if (now - timer.last >= timer.every) {
                        timer.last = now;
                        try {
                            timer.task.run();
                        } catch (Throwable error) {
                            // one bad timer must not stop the clock the other mods are on
                            report("mod clock: " + describe(error));
                        }
                    }
                }
                ticks++;
                try {
                    Thread.sleep(50L);
                } catch (InterruptedException stop) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }

        /** One registered heartbeat. Mutated only by the clock thread. */
        private static final class Timer {
            final int every;
            final Runnable task;
            long last;

            Timer(int every, Runnable task) {
                this.every = every;
                this.task = task;
            }
        }
    }

    static {
        // Minecraft's own logging is loud in a tab and the page shows the important lines anyway.
        try {
            LogManager.getLogManager().reset();
        } catch (Throwable ignored) {
            // a JVM without logging is still a JVM
        }
    }

}
