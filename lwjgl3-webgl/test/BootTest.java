package webmc;

import org.lwjgl.glfw.GLFW;
import org.lwjgl.opengl.GL11;
import org.lwjgl.opengl.GL15;
import org.lwjgl.opengl.GL20;
import org.lwjgl.opengl.GL30;
import org.lwjgl.system.MemoryUtil;

/**
 * Does the loader work? This is the question, asked in Java.
 *
 * It uses only the shim - no real LWJGL anywhere on the classpath - and does what a game does on its
 * first frame: open a window, make the context current, compile and link a shader program, upload a
 * triangle, draw it, swap buffers, and read a pixel back to see whether the colour that arrived is a
 * colour that was drawn. Every step is something a 1.13 client does before it draws a title screen, so
 * a pass here means the approach is sound and the remaining work is filling in members.
 *
 * The exit code is what the browser page reads: 0 only if a pixel came back drawn.
 */
public final class BootTest {

    private static void step(String what) {
        System.out.println("  " + what);
    }

    public static void main(String[] args) {
        long window = 0;
        long geometry = 0;
        try {
            step("glfwInit");
            if (!GLFW.glfwInit()) {
                throw new IllegalStateException("glfwInit did not report success");
            }
            step("glfwCreateWindow");
            window = GLFW.glfwCreateWindow(320, 240, "WebMC boot test", 0L, 0L);
            if (window == 0) {
                throw new IllegalStateException("no window");
            }
            GLFW.glfwMakeContextCurrent(window);
            step("context current, version " + GL11.glGetString(GL11.GL_VERSION));

            int vertexShader = GL20.glCreateShader(GL20.GL_VERTEX_SHADER);
            GL20.glShaderSource(vertexShader, "attribute vec4 position; void main() { gl_Position = position; }");
            GL20.glCompileShader(vertexShader);
            if (GL20.glGetShaderi(vertexShader, GL20.GL_COMPILE_STATUS) == 0) {
                throw new IllegalStateException("vertex shader: " + GL20.glGetShaderInfoLog(vertexShader, 4096));
            }
            int fragmentShader = GL20.glCreateShader(GL20.GL_FRAGMENT_SHADER);
            GL20.glShaderSource(fragmentShader,
                "precision mediump float; void main() { gl_FragColor = vec4(0.9, 0.2, 0.1, 1.0); }");
            GL20.glCompileShader(fragmentShader);
            if (GL20.glGetShaderi(fragmentShader, GL20.GL_COMPILE_STATUS) == 0) {
                throw new IllegalStateException("fragment shader: " + GL20.glGetShaderInfoLog(fragmentShader, 4096));
            }
            int program = GL20.glCreateProgram();
            GL20.glAttachShader(program, vertexShader);
            GL20.glAttachShader(program, fragmentShader);
            GL20.glBindAttribLocation(program, 0, "position");
            GL20.glLinkProgram(program);
            if (GL20.glGetProgrami(program, GL20.GL_LINK_STATUS) == 0) {
                throw new IllegalStateException("program: " + GL20.glGetProgramInfoLog(program, 4096));
            }
            GL20.glUseProgram(program);
            step("shaders compiled and linked");

            float[] triangle = { 0f, 0.8f, 0f, 0f, 0f, 0f, 0.8f, -0.8f, 0f, 0f, -0.8f, -0.8f, 0f, 0f, 0f };
            geometry = MemoryUtil.nmemAlloc(triangle.length * 4L);
            for (int index = 0; index < triangle.length; index++) {
                MemoryUtil.memPutFloat(geometry + index * 4L, triangle[index]);
            }
            long vertexArrays = MemoryUtil.nmemAlloc(4);
            GL30.nglGenVertexArrays(1, vertexArrays);
            GL30.glBindVertexArray(MemoryUtil.memGetInt(vertexArrays));
            long bufferNames = MemoryUtil.nmemAlloc(4);
            GL15.nglGenBuffers(1, bufferNames);
            GL15.glBindBuffer(GL15.GL_ARRAY_BUFFER, MemoryUtil.memGetInt(bufferNames));
            GL15.nglBufferData(GL15.GL_ARRAY_BUFFER, triangle.length * 4L, geometry, GL15.GL_STATIC_DRAW);
            GL20.nglVertexAttribPointer(0, 3, GL11.GL_FLOAT, false, 12, 0L);
            GL20.glEnableVertexAttribArray(0);
            step("geometry uploaded");

            GL11.glClearColor(0.06f, 0.06f, 0.08f, 1f);
            GL11.glClear(GL11.GL_COLOR_BUFFER_BIT);
            GL11.nglDrawArrays(GL11.GL_TRIANGLES, 0, 3);
            step("triangle drawn");

            GLFW.glfwSwapBuffers(window);
            GLFW.glfwPollEvents();

            long pixels = MemoryUtil.nmemAlloc(16);
            GL11.glReadPixels(160, 80, 2, 2, GL11.GL_RGBA, GL11.GL_UNSIGNED_BYTE, pixels);
            int red = MemoryUtil.memGetByte(pixels) & 0xFF;
            int green = MemoryUtil.memGetByte(pixels + 1) & 0xFF;
            int blue = MemoryUtil.memGetByte(pixels + 2) & 0xFF;
            step("pixel at the centre: r=" + red + " g=" + green + " b=" + blue);
            MemoryUtil.nmemFree(pixels);
            MemoryUtil.nmemFree(bufferNames);
            MemoryUtil.nmemFree(vertexArrays);
            if (red == 0 && green == 0 && blue == 0) {
                throw new IllegalStateException("the readback was empty: nothing was rendered");
            }
            System.out.println("BOOT OK");
        } catch (Throwable failure) {
            System.out.println("BOOT FAILED: " + failure);
            failure.printStackTrace(System.out);
            System.exit(1);
        } finally {
            if (geometry != 0) {
                MemoryUtil.nmemFree(geometry);
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
