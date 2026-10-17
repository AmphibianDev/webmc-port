package org.slf4j.spi;

import org.slf4j.ILoggerFactory;
import org.slf4j.IMarkerFactory;

/** What a logging backend offers slf4j. Ours is the console. */
public interface SLF4JServiceProvider {
    ILoggerFactory getLoggerFactory();

    IMarkerFactory getMarkerFactory();

    MDCAdapter getMDCAdapter();

    String getRequestedApiVersion();

    void initialize();
}
