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

    /** The provider, for anything that asks slf4j what backend it is running on. */
    public static org.slf4j.spi.SLF4JServiceProvider getProvider() {
        return PROVIDER;
    }

    public static org.slf4j.ILoggerFactory getILoggerFactory() {
        return PROVIDER.getLoggerFactory();
    }

    private static final org.slf4j.spi.SLF4JServiceProvider PROVIDER = new org.slf4j.spi.SLF4JServiceProvider() {
        private final org.slf4j.ILoggerFactory loggers = name -> getLogger(name);
        private final org.slf4j.IMarkerFactory markers = new SilentMarkers();
        private final org.slf4j.spi.MDCAdapter context = new org.slf4j.spi.MDCAdapter.Simple();

        @Override
        public org.slf4j.ILoggerFactory getLoggerFactory() {
            return this.loggers;
        }

        @Override
        public org.slf4j.IMarkerFactory getMarkerFactory() {
            return this.markers;
        }

        @Override
        public org.slf4j.spi.MDCAdapter getMDCAdapter() {
            return this.context;
        }

        @Override
        public String getRequestedApiVersion() {
            return "2.0.16";
        }

        @Override
        public void initialize() {
        }
    };

    /** Markers that only have a name: nothing in this client filters on them. */
    private static final class SilentMarkers implements org.slf4j.IMarkerFactory {
        private final java.util.Map<String, org.slf4j.Marker> known = new java.util.LinkedHashMap<>();

        @Override
        public org.slf4j.Marker getMarker(String name) {
            return this.known.computeIfAbsent(name == null ? "" : name, SilentMarker::new);
        }

        @Override
        public boolean exists(String name) {
            return this.known.containsKey(name);
        }

        @Override
        public boolean detachMarker(String name) {
            return this.known.remove(name) != null;
        }

        @Override
        public org.slf4j.Marker getDetachedMarker(String name) {
            return new SilentMarker(name == null ? "" : name);
        }
    }

    private record SilentMarker(String name) implements org.slf4j.Marker {
        @Override
        public String getName() {
            return this.name;
        }

        @Override
        public void add(org.slf4j.Marker reference) {
        }

        @Override
        public boolean remove(org.slf4j.Marker reference) {
            return false;
        }

        @Override
        public boolean hasChildren() {
            return false;
        }

        @Override
        public boolean hasReferences() {
            return false;
        }

        @Override
        public java.util.Iterator<org.slf4j.Marker> iterator() {
            return java.util.List.<org.slf4j.Marker>of().iterator();
        }

        @Override
        public boolean contains(org.slf4j.Marker other) {
            return false;
        }

        @Override
        public boolean contains(String name) {
            return this.name.equals(name);
        }
    }

    private record ConsoleLogger(String getName) implements Logger {
    }
}
