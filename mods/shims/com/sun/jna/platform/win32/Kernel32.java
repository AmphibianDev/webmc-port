package com.sun.jna.platform.win32;

/** JNA's Windows kernel entry points: present so a mod compiles, and refused if actually called. */
public interface Kernel32 {
    Kernel32 INSTANCE = new Refusing();

    int GetCurrentProcessId();

    /** The one thing this can do in a browser: say that it cannot. */
    final class Refusing implements Kernel32 {
        @Override
        public int GetCurrentProcessId() {
            throw new UnsupportedOperationException("this client runs in a browser: there is no Windows process to ask");
        }
    }
}
