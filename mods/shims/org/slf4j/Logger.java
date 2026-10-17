package org.slf4j;

/**
 * The logger interface Fabric API compiles against, wired to the client's console.
 *
 * Everything under "warn" is dropped unless the player turned it on: a compiled client has one place
 * to write (the browser console) and a mod that logs per frame would drown it.
 */
public interface Logger {
    String getName();

    /** The fluent API of slf4j 2.x: present, and a no-op, so a merged mod can call it. */
    default org.slf4j.spi.LoggingEventBuilder makeLoggingEventBuilder(org.slf4j.event.Level level) {
        return new org.slf4j.spi.LoggingEventBuilder.Noop();
    }

    default org.slf4j.spi.LoggingEventBuilder atLevel(org.slf4j.event.Level level) {
        return makeLoggingEventBuilder(level);
    }

    default boolean isEnabledForLevel(org.slf4j.event.Level level) {
        return true;
    }

    default org.slf4j.spi.LoggingEventBuilder atTrace() {
        return makeLoggingEventBuilder(org.slf4j.event.Level.TRACE);
    }

    default org.slf4j.spi.LoggingEventBuilder atDebug() {
        return makeLoggingEventBuilder(org.slf4j.event.Level.DEBUG);
    }

    default org.slf4j.spi.LoggingEventBuilder atInfo() {
        return makeLoggingEventBuilder(org.slf4j.event.Level.INFO);
    }

    default org.slf4j.spi.LoggingEventBuilder atWarn() {
        return makeLoggingEventBuilder(org.slf4j.event.Level.WARN);
    }

    default org.slf4j.spi.LoggingEventBuilder atError() {
        return makeLoggingEventBuilder(org.slf4j.event.Level.ERROR);
    }

    default boolean isTraceEnabled(Marker marker) {
        return isTraceEnabled();
    }

    default boolean isDebugEnabled(Marker marker) {
        return isDebugEnabled();
    }

    default boolean isInfoEnabled(Marker marker) {
        return isInfoEnabled();
    }

    default boolean isWarnEnabled(Marker marker) {
        return isWarnEnabled();
    }

    default boolean isErrorEnabled(Marker marker) {
        return isErrorEnabled();
    }

    default void trace(Marker marker, String message) {
        trace(message);
    }

    default void trace(Marker marker, String format, Object argument) {
        trace(format, argument);
    }

    default void trace(Marker marker, String format, Object first, Object second) {
        trace(format, first, second);
    }

    default void trace(Marker marker, String format, Object... arguments) {
        trace(format, arguments);
    }

    default void trace(Marker marker, String message, Throwable error) {
        trace(message, error);
    }

    default void debug(Marker marker, String message) {
        debug(message);
    }

    default void debug(Marker marker, String format, Object argument) {
        debug(format, argument);
    }

    default void debug(Marker marker, String format, Object first, Object second) {
        debug(format, first, second);
    }

    default void debug(Marker marker, String format, Object... arguments) {
        debug(format, arguments);
    }

    default void debug(Marker marker, String message, Throwable error) {
        debug(message, error);
    }

    default void info(Marker marker, String message) {
        info(message);
    }

    default void info(Marker marker, String format, Object argument) {
        info(format, argument);
    }

    default void info(Marker marker, String format, Object first, Object second) {
        info(format, first, second);
    }

    default void info(Marker marker, String format, Object... arguments) {
        info(format, arguments);
    }

    default void info(Marker marker, String message, Throwable error) {
        info(message, error);
    }

    default void warn(Marker marker, String message) {
        warn(message);
    }

    default void warn(Marker marker, String format, Object argument) {
        warn(format, argument);
    }

    default void warn(Marker marker, String format, Object first, Object second) {
        warn(format, first, second);
    }

    default void warn(Marker marker, String format, Object... arguments) {
        warn(format, arguments);
    }

    default void warn(Marker marker, String message, Throwable error) {
        warn(message, error);
    }

    default void error(Marker marker, String message) {
        error(message);
    }

    default void error(Marker marker, String format, Object argument) {
        error(format, argument);
    }

    default void error(Marker marker, String format, Object first, Object second) {
        error(format, first, second);
    }

    default void error(Marker marker, String format, Object... arguments) {
        error(format, arguments);
    }

    default void error(Marker marker, String message, Throwable error) {
        error(message, error);
    }

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
