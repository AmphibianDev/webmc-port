package org.slf4j;

/**
 * The logger interface Fabric API compiles against, wired to the client's console.
 *
 * Everything under "warn" is dropped unless the player turned it on: a compiled client has one place
 * to write (the browser console) and a mod that logs per frame would drown it.
 */
public interface Logger {
    String getName();

    default boolean isTraceEnabled() {
        return false;
    }

    default boolean isDebugEnabled() {
        return false;
    }

    default boolean isInfoEnabled() {
        return true;
    }

    default boolean isWarnEnabled() {
        return true;
    }

    default boolean isErrorEnabled() {
        return true;
    }

    default void trace(String message) {
    }

    default void trace(String format, Object argument) {
    }

    default void trace(String format, Object first, Object second) {
    }

    default void trace(String format, Object... arguments) {
    }

    default void trace(String message, Throwable error) {
    }

    default void debug(String message) {
    }

    default void debug(String format, Object argument) {
    }

    default void debug(String format, Object first, Object second) {
    }

    default void debug(String format, Object... arguments) {
    }

    default void debug(String message, Throwable error) {
    }

    default void info(String message) {
        write("INFO", message, null);
    }

    default void info(String format, Object argument) {
        write("INFO", format, argument);
    }

    default void info(String format, Object first, Object second) {
        write("INFO", format, new Object[] { first, second });
    }

    default void info(String format, Object... arguments) {
        write("INFO", format, arguments);
    }

    default void info(String message, Throwable error) {
        write("INFO", message, error);
    }

    default void warn(String message) {
        write("WARN", message, null);
    }

    default void warn(String format, Object argument) {
        write("WARN", format, argument);
    }

    default void warn(String format, Object first, Object second) {
        write("WARN", format, new Object[] { first, second });
    }

    default void warn(String format, Object... arguments) {
        write("WARN", format, arguments);
    }

    default void warn(String message, Throwable error) {
        write("WARN", message, error);
    }

    default void error(String message) {
        write("ERROR", message, null);
    }

    default void error(String format, Object argument) {
        write("ERROR", format, argument);
    }

    default void error(String format, Object first, Object second) {
        write("ERROR", format, new Object[] { first, second });
    }

    default void error(String format, Object... arguments) {
        write("ERROR", format, arguments);
    }

    default void error(String message, Throwable error) {
        write("ERROR", message, error);
    }

    /** One place where a log line becomes something a person can see. */
    private void write(String level, String format, Object detail) {
        StringBuilder line = new StringBuilder("[").append(getName()).append("] ").append(level).append(' ')
                .append(format == null ? "" : format);
        if (detail instanceof Object[] arguments) {
            for (Object argument : arguments) {
                line.append(' ').append(argument);
            }
        } else if (detail != null) {
            line.append(' ').append(detail);
        }
        System.out.println(line);
    }
}
