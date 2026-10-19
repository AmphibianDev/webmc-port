package webmc.loader;

/**
 * A mod our own loader can start in a vanilla client, described by {@code webmc.mod.json}:
 *
 * <pre>
 * {
 *   "id": "hello-webmc",
 *   "name": "Hello WebMC",
 *   "version": "1.0.0",
 *   "entrypoint": "com.example.HelloMod"
 * }
 * </pre>
 *
 * The class named by {@code entrypoint} implements this interface and is called once, after the JVM
 * has started and before the game's own main class runs.
 *
 * A jar that has a {@code fabric.mod.json} instead does not implement this: Fabric starts it.
 */
public interface WebMcMod {

    void onLoad(WebMcApi api) throws Exception;
}
