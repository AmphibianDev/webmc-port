package org.slf4j;

/** Loggers by name, all of them the console logger. */
public final class LoggerFactory {
    private LoggerFactory() {
    }

    public static Logger getLogger(String name) {
        return new ConsoleLogger(name == null ? "unknown" : name);
    }

    public static Logger getLogger(Class<?> type) {
        return getLogger(type == null ? "unknown" : type.getName());
    }

    private record ConsoleLogger(String getName) implements Logger {
    }
}
