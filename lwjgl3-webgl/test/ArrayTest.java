package webmc;

import java.nio.ByteBuffer;

import org.lwjgl.glfw.GLFW;
import org.lwjgl.system.WebMC;
import org.lwjgl.system.MemoryUtil;

/** Which shapes of call across the CheerpJ boundary come back, and which hang. */
public final class ArrayTest {

    private static void step(String what, Runnable body) {
        WebMC.log("begin: " + what);
        body.run();
        WebMC.log("end:   " + what);
    }

    public static void main(String[] args) {
        step("nmemGetInt(long) -> int", () -> WebMC.log("  = " + WebMC.nmemGetInt(0)));
        step("arenaUsed() -> long", () -> WebMC.log("  = " + WebMC.arenaUsed()));
        step("glfwInit() -> boolean", () -> WebMC.log("  = " + GLFW.glfwInit()));
        step("nmemAlloc(long) -> long", () -> WebMC.log("  = " + WebMC.nmemAlloc(16)));
        step("nmemPutByte(long, byte)", () -> WebMC.nmemPutByte(0, (byte) 7));
        step("nmemSet(long, byte, long)", () -> WebMC.nmemSet(0, (byte) 0, 16));
        step("nmemWriteBytes(long, byte[], int)", () -> WebMC.nmemWriteBytes(0, new byte[] { 1, 2, 3, 4 }, 0));
        step("nmemReadBytes(long, byte[], int, int)", () -> {
            byte[] back = new byte[4];
            WebMC.nmemReadBytes(0, back, 0, 4);
            WebMC.log("  = " + back[0] + "," + back[1] + "," + back[2] + "," + back[3]);
        });
        step("MemoryUtil.nmemAlloc(long) -> long", () -> WebMC.log("  = " + MemoryUtil.nmemAlloc(8)));
        System.out.println("ARRAY OK");
    }
}
