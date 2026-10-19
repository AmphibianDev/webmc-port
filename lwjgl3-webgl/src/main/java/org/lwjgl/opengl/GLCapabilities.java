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
    public final boolean GL_KHR_no_error;
    public final boolean GL_ARB_shader_image_load_store;
    /** Whether the context claims at least this version, as LWJGL's own fields do. */
    public final boolean OpenGL44;
    public final boolean OpenGL46;
    /** Named for the extension it guards; false here means "do not take the barrier path". */
    public final boolean glMemoryBarrier;

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
        this.GL_KHR_no_error = false;
        this.GL_ARB_shader_image_load_store = false;
        this.OpenGL44 = false;
        this.OpenGL46 = false;
        this.glMemoryBarrier = false;
        this.version = version;
    }

    /** What the context says about itself; the client logs it. */
    public String toString() {
        return "GLCapabilities(" + this.version + ")";
    }
}
