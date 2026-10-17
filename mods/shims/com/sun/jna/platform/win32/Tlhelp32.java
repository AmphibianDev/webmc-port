package com.sun.jna.platform.win32;

/** The Windows toolhelp structures, as a type name a mod can compile against. */
public interface Tlhelp32 {
    /** One module of a process, as Windows describes it. */
    class MODULEENTRY32W {
        public int dwSize;
        public int th32ModuleID;
        public int th32ProcessID;
        public String szModule;
        public String szExePath;
    }
}
