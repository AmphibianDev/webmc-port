package org.lwjgl.opengl;

// HANDWRITTEN: this file is an implementation, not a generated stub.

import org.lwjgl.system.Callback;

/**
 * The callback GL hands driver messages to.
 *
 * <p>WebGL 2 has no KHR_debug, so the messages never come from a driver - but the shim raises its own when a GL
 * call fails or when something asked for cannot be translated, and this is where they go. That turns a black
 * screen into a line in the client's log, which is the only reason a client installs one of these.
 */
public class GLDebugMessageCallback extends Callback {

    public GLDebugMessageCallback(GLDebugMessageCallbackI handler) {
        super(handler);
    }

    public static GLDebugMessageCallback create(GLDebugMessageCallbackI handler) {
        return new GLDebugMessageCallback(handler);
    }

    public GLDebugMessageCallbackI handler() {
        return callback() instanceof GLDebugMessageCallbackI ? (GLDebugMessageCallbackI) callback() : null;
    }

    /** The text of a message: GL hands it over as a length and a pointer. */
    public static String getMessage(int length, long message) {
        if (message == 0) {
            return "";
        }
        StringBuilder text = new StringBuilder(Math.max(0, length));
        for (int index = 0; index < length; index++) {
            text.append((char) org.lwjgl.system.MemoryUtil.memGetByte(message + index));
        }
        return text.toString();
    }
}
