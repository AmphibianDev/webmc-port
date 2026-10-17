package org.slf4j.spi;

import java.util.function.Supplier;

/**
 * The fluent logging API of slf4j 2.x. This client writes log lines straight to the console, so a
 * builder collects nothing and logs nothing - but it exists, and every call on it is safe, which is
 * what a merged mod needs from it.
 */
public interface LoggingEventBuilder {
    LoggingEventBuilder setCause(Throwable cause);

    LoggingEventBuilder addMarker(org.slf4j.Marker marker);

    LoggingEventBuilder addArgument(Object argument);

    LoggingEventBuilder addArgument(Supplier<?> argument);

    LoggingEventBuilder addKeyValue(String key, Object value);

    LoggingEventBuilder addKeyValue(String key, Supplier<Object> value);

    void log();

    void log(String message);

    void log(String message, Object argument);

    void log(String message, Object first, Object second);

    void log(String message, Object... arguments);

    void log(Supplier<String> message);

    /** The builder this shim hands out: it does nothing, and it never throws. */
    final class Noop implements LoggingEventBuilder {
        @Override
        public LoggingEventBuilder setCause(Throwable cause) {
            return this;
        }

        @Override
        public LoggingEventBuilder addMarker(org.slf4j.Marker marker) {
            return this;
        }

        @Override
        public LoggingEventBuilder addArgument(Object argument) {
            return this;
        }

        @Override
        public LoggingEventBuilder addArgument(Supplier<?> argument) {
            return this;
        }

        @Override
        public LoggingEventBuilder addKeyValue(String key, Object value) {
            return this;
        }

        @Override
        public LoggingEventBuilder addKeyValue(String key, Supplier<Object> value) {
            return this;
        }

        @Override
        public void log() {
        }

        @Override
        public void log(String message) {
        }

        @Override
        public void log(String message, Object argument) {
        }

        @Override
        public void log(String message, Object first, Object second) {
        }

        @Override
        public void log(String message, Object... arguments) {
        }

        @Override
        public void log(Supplier<String> message) {
        }
    }
}
