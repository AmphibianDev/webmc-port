package org.lwjgl.util.tinyfd;

// HANDWRITTEN: this file is an implementation, not a generated stub. The generator skips it.

/**
 * The file dialogs. A browser has none, and a released game should not open one anyway, so this
 * answers "no" - which is the same as a player cancelling, and the callers all handle that.
 */
public final class TinyFileDialogs {

    private TinyFileDialogs() {
    }

    public static boolean tinyfd_messageBox(CharSequence title, CharSequence message,
                                            CharSequence dialogType, CharSequence iconType,
                                            boolean defaultYes) {
        return false;
    }

    public static boolean tinyfd_messageBox(CharSequence title, CharSequence message,
                                            CharSequence dialogType, CharSequence iconType,
                                            int defaultYes) {
        return false;
    }
}
