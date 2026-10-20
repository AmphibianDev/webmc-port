package org.slf4j;

/** The logger factory a service provider hands out. */
public interface ILoggerFactory {
    Logger getLogger(String name);
}
