package webmc;

import org.lwjgl.glfw.GLFW;
import org.lwjgl.opengl.GL11;
import org.lwjgl.opengl.GL20;
import org.lwjgl.opengl.GL30;
import org.lwjgl.system.MemoryUtil;

/**
 * Does the loader work? This is the question, asked in Java.
 *
 * It uses only the shim - no real LWJGL anywhere on the classpath - and does what a game does on its
 * first frame: open a window, make the context current, set a clear colour, compile and link a
 * shader program, upload a triangle, draw it, swap buffers, and then read a pixel back to see that
 * the colour we asked for is the colour that arrived. Every step is something a 1.13 client does
 * before it draws a title screen, so a pass here means the approach is sound and the remaining work is
 * filling in members rather than discovering that it cannot be done.
 *
 * The exit code is what the browser page reads: 0 only if the pixel matched.
 */
public final class BootTest {

    private static final int GL_TRIANGLES = 0x0004;
    private static final int GL_FLOAT = 0x1406;
    private static final int GL_ARRAY_BUFFER = 0x8892;
    private static final int GL_STATIC_DRAW = 0x88E4;
    private static final int GL_VERTEX_SHADER = 0x8B31;
    private static final int GL_FRAGMENT_SHADER = 0x8B30;
    private static final int GL_COMPILE_STATUS = 0x8B81;
    private static final int GL_LINK_STATUS = 0x8B82;
    private static final int GL_COLOR_BUFFER_BIT = 0x00004000;
    private static final int GL_RGBA = 0x1908;
    private static final int GL_UNSIGNED_BYTE = 0x1401;
    private static final int GLFW_TRUE = 1;

    private static void step(String what) {
        System.out.println("  " + what);
    }

    public static void main(String[] args) {
        long window = 0;
        long buffer = 0;
        try {
            step("glfwInit");
            if (GLFW.glfwInit() != GLFW_TRUE) {
                throw new IllegalStateException("glfwInit did not report success");
            }
            step("glfwCreateWindow");
            window = GLFW.glfwCreateWindow(320, 240, "WebMC boot test", 0L, 0L);
            if (window == 0) {
                throw new IllegalStateException("no window");
            }
            GLFW.glfwMakeContextCurrent(window);
            step("context current, GL_VERSION = " + GL11.glGetString(GL11.GL_VERSION));

            int vertexShader = GL20.glCreateShader(GL_VERTEX_SHADER);
            GL20.glShaderSource(vertexShader, "attribute vec4 position; void main() { gl_Position = position; }");
            GL20.glCompileShader(vertexShader);
            if (GL20.glGetShaderi(vertexShader, GL_COMPILE_STATUS) == 0) {
                throw new IllegalStateException("vertex shader: " + GL20.glGetShaderInfoLog(vertexShader));
            }
            int fragmentShader = GL20.glCreateShader(GL_FRAGMENT_SHADER);
            GL20.glShaderSource(fragmentShader,
                "precision mediump float; void main() { gl_FragColor = vec4(0.9, 0.2, 0.1, 1.0); }");
            GL20.glCompileShader(fragmentShader);
            int program = GL20.glCreateProgram();
            GL20.glAttachShader(program, vertexShader);
            GL20.glAttachShader(program, fragmentShader);
            GL20.glBindAttribLocation(program, 0, "position");
            GL20.glLinkProgram(program);
            if (GL20.glGetProgrami(program, GL_LINK_STATUS) == 0) {
                throw new IllegalStateException("program: " + GL20.glGetProgramInfoLog(program));
            }
            GL20.glUseProgram(program);
            step("shaders compiled and linked");

            float[] triangle = { 0f, 0.8f, 0f, 0f, 0f, 0f, 0.8f, -0.8f, 0f, 0f, -0.8f, -0.8f, 0f, 0f, 0f };
            buffer = MemoryUtil.nmemAlloc(triangle.length * 4L);
            for (int index = 0; index < triangle.length; index++) {
                MemoryUtil.memPutFloat(buffer + index * 4L, triangle[index]);
            }
            long vertexArray = MemoryUtil.nmemAlloc(4);
            GL30.nglGenVertexArrays(1, vertexArray);
            GL30.glBindVertexArray(MemoryUtil.memGetInt(vertexArray));
            long bufferNames = MemoryUtil.nmemAlloc(4);
            org.lwjgl.opengl.GL15.nglGenBuffers(1, bufferNames);
            GL15.glBindBuffer(GL_ARRAY_BUFFER, MemoryUtil.memGetInt(bufferNames));
            GL15.nglBufferData(GL_ARRAY_BUFFER, triangle.length * 4L, buffer, GL_STATIC_DRAW);
            GL20.nglVertexAttribPointer(0, 3, GL_FLOAT, false, 12, 0L);
            GL20.glEnableVertexAttribArray(0);
            step("geometry uploaded");

            GL11.glClearColor(0.06f, 0.06f, 0.08f, 1f);
            GL11.glClear(GL_COLOR_BUFFER_BIT);
            GL11.nglDrawArrays(GL_TRIANGLES, 0, 3);
            step("triangle drawn");

            GLFW.glfwSwapBuffers(window);
            GLFW.glfwPollEvents();

            long pixels = MemoryUtil.nmemAlloc(16);
            GL11.glReadPixels(160, 80, 2, 2, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
            int red = MemoryUtil.memGetByte(pixels) & 0xFF;
            int green = MemoryUtil.memGetByte(pixels + 1) & 0xFF;
            int blue = MemoryUtil.memGetByte(pixels + 2) & 0xFF;
            step("pixel at the centre: r=" + red + " g=" + green + " b=" + blue);
            MemoryUtil.nmemFree(pixels);
            MemoryUtil.nmemFree(bufferNames);
            MemoryUtil.nmemFree(vertexArray);

            // the triangle is red-ish, the clear colour is nearly black: either way, the pixel that
            // came back was drawn by something, and a 0,0,0,0 answer means nothing was
            if (red == 0 && green == 0 && blue == 0) {
                throw new IllegalStateException("readback was empty: no frame was rendered");
            }
            System.out.println("BOOT OK");
        } catch (Throwable failure) {
            System.out.println("BOOT FAILED: " + failure);
            failure.printStackTrace(System.out);
            System.exit(1);
        } finally {
            if (buffer != 0) {
                MemoryUtil.nmemFree(buffer);
            }
            if (window != 0) {
                GLFW.glfwDestroyWindow(window);
            }
            GLFW.glfwTerminate();
        }
        System.exit(0);
    }

    private BootTest() {
    }
}
