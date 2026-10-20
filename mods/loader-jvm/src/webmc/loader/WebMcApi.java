package webmc.loader;

/**
 * What our loader hands to a mod, before the game starts.
 *
 * This is a small surface on purpose. Under a real JVM on the real-Java rows there are two kinds of
 * mod, and the honest ones are different things:
 *
 *   * a <b>Fabric mod</b> is bytecode plus mixins, and Fabric Loader transforms the game's classes as
 *     they are loaded. That is the powerful one, and it belongs to Fabric - our loader only makes sure
 *     Fabric finds the jar (see {@code WebMcLoader}).
 *   * a <b>WebMC mod</b> is a jar with a {@code webmc.mod.json} and a class that implements
 *     {@link WebMcMod}. It cannot rewrite the game (nothing here transforms bytecode), but it can run
 *     code inside the game's JVM, use the game's own classes, keep a heartbeat, and talk to the page.
 *
 * Both can be in the same folder; the loader sorts them out by what is inside the jar.
 *
 * Threading: {@link #every} runs on a daemon thread the loader started, not on the game's render
 * thread. Anything that touches the game's state has to be marshalled by the mod itself. This is
 * stated rather than hidden because a callback that silently hops threads is how a mod becomes
 * "sometimes it works".
 */
public interface WebMcApi {

    /** The game version this JVM is running, e.g. {@code 1.12.2}. */
    String gameVersion();

    /** {@code vanilla}, {@code fabric} or {@code legacyfabric} - how the game was started. */
    String mode();

    /** Writes a line to the loader's report, which the page shows. */
    void log(String message);

    /**
     * A tiny string bus, so two mods can cooperate without depending on each other's classes.
     * Nothing about it is Minecraft-specific, which is the point: it is the one thing every mod can
     * rely on, whatever version it was written for.
     */
    void set(String key, String value);

    String get(String key);

    /** Calls {@code task} about every {@code millis} milliseconds, on the loader's clock thread. */
    void every(int millis, Runnable task);

    /** How many times the clock has fired since the game started. */
    long ticks();

    /** Stops the clock. The JVM exits when the game returns and no non-daemon thread is left. */
    void stop();
}
