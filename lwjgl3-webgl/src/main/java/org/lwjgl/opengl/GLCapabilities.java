package org.lwjgl.opengl;

// HANDWRITTEN: this file is an implementation, not a generated stub.

/**
 * What this GL context can do, as the client's code reads it.
 *
 * <p>Every field here answers "may I use the fast path that needs this?". WebGL 2 has none of the extensions the
 * shim would have to fake - no direct state access, no uniform buffer objects, no vertex-attrib binding
 * indirection, no KHR_debug, no object labels - so every one of those is answered **false**, which makes the
 * client take the path it takes on a GPU without them. That is deliberate: saying yes here and then doing
 * nothing is how a client ends up rendering nothing and reporting no error.
 *
 * <p>The one that can be true is anisotropic filtering, because it is a real WebGL extension, and it is asked
 * for rather than assumed.
 */
public final class GLCapabilities {

    public final boolean GL_ARB_buffer_storage;
    public final boolean GL_ARB_debug_output;
    public final boolean GL_ARB_direct_state_access;
    public final boolean GL_ARB_vertex_attrib_binding;
    public final boolean GL_EXT_debug_label;
    public final boolean GL_EXT_texture_filter_anisotropic;
    public final boolean GL_KHR_debug;

    /** The context this describes, for the log. */
    public final String version;

    public GLCapabilities(boolean anisotropic, String version) {
        this.GL_ARB_buffer_storage = false;
        this.GL_ARB_debug_output = false;
        this.GL_ARB_direct_state_access = false;
        this.GL_ARB_vertex_attrib_binding = false;
        this.GL_EXT_debug_label = false;
        this.GL_EXT_texture_filter_anisotropic = anisotropic;
        this.GL_KHR_debug = false;
        this.version = version;
    }

    /** What the context says about itself; the client logs it. */
    public String toString() {
        return "GLCapabilities(" + this.version + ")";
    }
}
