package webmc.runtime;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.ServiceLoader;
import java.util.concurrent.AbstractExecutorService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

/**
 * The Java methods a mod calls that this client's class library does not have yet.
 *
 * TeaVM implements the Java standard library itself, and it is not the whole of Java 21: a mod compiled
 * against 21 can call something as ordinary as `String.splitWithDelimiters`, and the compile stops there.
 * Rather than change the mod, the bake rewrites those calls to the methods here - which do the same thing,
 * written the way this client can run.
 *
 * Each one is named after the JDK method it stands in for, so a stack trace reads honestly.
 */
public final class JdkCompat {
    private JdkCompat() {
    }

    /**
     * {@code ServiceLoader.stream()} and {@code ServiceLoader.findFirst()} (Java 9).
     *
     * A client compiled ahead of time has no service providers to discover: its classes came from a build,
     * not from a jar scan. So the stream yields the services the loader already has (usually none), and the
     * `Provider.get()` that follows is rewritten to the identity - which is what the caller meant.
     */
    public static Stream<Object> serviceStream(ServiceLoader<?> loader) {
        List<Object> services = new ArrayList<>();
        for (Object service : loader) {
            services.add(service);
        }
        return services.stream();
    }

    /** {@code ServiceLoader.findFirst()}: the first service, if this client has any. */
    public static Optional<Object> serviceFindFirst(ServiceLoader<?> loader) {
        for (Object service : loader) {
            return Optional.of(service);
        }
        return Optional.empty();
    }

    /** The stand-in for {@code ServiceLoader.Provider.get()}, after the stream rewrite above. */
    public static Object identity(Object value) {
        return value;
    }

    /**
     * {@code Executors.newThreadPerTaskExecutor(ThreadFactory)} (Java 21).
     *
     * A page has one thread, and this client's threads are cooperative; a task-per-thread executor is
     * therefore a cooperative thread per task, which is the closest true statement and keeps the caller's
     * background work off the tick it started from.
     */
    public static ExecutorService newThreadPerTaskExecutor(final ThreadFactory factory) {
        return new AbstractExecutorService() {
            @Override
            public void execute(Runnable command) {
                factory.newThread(command).start();
            }

            @Override
            public void shutdown() {
            }

            @Override
            public List<Runnable> shutdownNow() {
                return List.of();
            }

            @Override
            public boolean isShutdown() {
                return false;
            }

            @Override
            public boolean isTerminated() {
                return false;
            }

            @Override
            public boolean awaitTermination(long timeout, TimeUnit unit) {
                return true;
            }
        };
    }

    /** {@code String.splitWithDelimiters(String regex, int limit)}: the values and the separators. */
    public static String[] splitWithDelimiters(String input, String regex, int limit) {
        if (input == null) {
            throw new NullPointerException("input is null");
        }
        if (regex == null) {
            throw new NullPointerException("regex is null");
        }
        if (input.isEmpty()) {
            return new String[] { "" };
        }
        Pattern pattern = Pattern.compile(regex);
        Matcher matcher = pattern.matcher(input);
        List<String> parts = new ArrayList<>();
        int index = 0;
        int count = 0;
        while (matcher.find()) {
            if (limit > 0 && count >= limit - 1) {
                break;
            }
            parts.add(input.substring(index, matcher.start()));
            parts.add(matcher.group());
            index = matcher.end();
            count += 1;
        }
        parts.add(input.substring(index));
        return parts.toArray(new String[0]);
    }
}
