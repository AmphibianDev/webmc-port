package org.lwjgl.stb;

// HANDWRITTEN: an implementation, so the client can build one.

/**
 * stbi_write_png's callback holder.
 *
 * The client subclasses this to receive PNG bytes when it saves a screenshot. Writing PNG is not
 * implemented in this shim (see STBImageWrite), so nothing is ever handed to it - but the class has to
 * exist and be constructible, because the client builds one before it calls.
 */
public class STBIWriteCallback {

    protected STBIWriteCallback() {
    }
}
