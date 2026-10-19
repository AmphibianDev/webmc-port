package webmc;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

/**
 * Put the assets where the client asks for them.
 *
 * CheerpJ's /str mount - the only thing a page can write - is flat: a path with a directory in it is
 * refused with "Directories are not supported", silently. Minecraft's assets are a tree of
 * assets/indexes/&lt;id&gt;.json and assets/objects/&lt;xx&gt;/&lt;hash&gt;. Java, however, can create real
 * directories under /files (measured: write, read and list all work), so the page mounts every asset flat
 * under /str beside a flat listing, and this copies them into the tree the client wants.
 *
 * It is its own main class, run by the page before the client, so that a failure here is visible as itself
 * rather than as Minecraft not finding a texture.
 *
 *     webmc.Prepare &lt;assets-url|-> &lt;target-root&gt; &lt;listing&gt;
 */
public final class Prepare {

    private Prepare() {
    }

    public static void main(String[] arguments) {
        String base = arguments.length > 0 ? arguments[0] : "-";
        String root = arguments.length > 1 ? arguments[1] : "/files/assets";
        Path listing = Path.of(arguments.length > 2 ? arguments[2] : "/str/assets-map.txt");
        say("PREPARE: listing=" + listing + " exists=" + Files.exists(listing)
            + " root=" + root + " base=" + base);
        try {
            if (Files.exists(listing)) {
                fromListing(listing, Path.of(root));
                return;
            }
            say("PREPARE FAILED: no listing at " + listing
                + " - the page mounts it at /str/assets-map.txt, and a missing one means the mount did not land");
        } catch (Throwable problem) {
            say("PREPARE FAILED: " + problem);
            problem.printStackTrace(System.out);
        }
    }

    private static void fromListing(Path listing, Path assets) throws Exception {
        int written = 0;
        int failed = 0;
        long started = System.currentTimeMillis();
        for (String line : Files.readAllLines(listing)) {
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
                if (failed <= 5) {
                    say("  asset " + parts[1] + ": " + problem);
                }
            }
        }
        say("PREPARE: " + written + " asset(s) into " + assets + ", " + failed + " failed, "
            + (System.currentTimeMillis() - started) + " ms");
    }

    private static void say(String line) {
        // the shim's log is the channel measured to reach the page's console, and stdout/stderr as backup
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
