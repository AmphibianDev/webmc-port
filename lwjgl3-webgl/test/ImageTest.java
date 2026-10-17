package webmc;

import java.nio.ByteBuffer;
import java.nio.IntBuffer;

import org.lwjgl.stb.STBImage;
import org.lwjgl.system.MemoryUtil;

/**
 * Does STB image work? This asks the same question about textures that the boot test asks about GL: a PNG
 * built byte by byte goes through the shim's decoder, and the pixels that come out are compared with the
 * ones that went in.
 */
public final class ImageTest {

    private static final String PNG = String.join("",
        "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAXElEQVR4nGNkYGD4rxoNx0zo",
        "bCZuBtP/UIyVzSzIEFAnoSfNBMT/JdYBMRJ7BpDNAlHN8B+KMdiMOgxX/uGyH9kNON3CrM5g",
        "1Qi2E2IvE4w9A8pmgajEtF8VygYAqcBCUpLq8lMAAAAASUVORK5CYII=",
        "");

    /** The first 32 channel values of that image: eight pixels, RGBA. */
    private static final int[] EXPECTED = {0, 0, 0, 255, 37, 91, 0, 254, 74, 182, 0, 253, 111, 17, 0, 255, 148, 108, 0, 254, 185, 199, 0, 253, 222, 34, 0, 255, 3, 125, 0, 254};

    public static void main(String[] args) {
        byte[] png = java.util.Base64.getDecoder().decode(PNG);
        System.out.println("  png bytes: " + png.length);
        ByteBuffer input = MemoryUtil.memAlloc(png.length);
        input.put(png);
        input.flip();                                  // the decode reads from position to limit
        IntBuffer width = MemoryUtil.memAllocInt(1);
        IntBuffer height = MemoryUtil.memAllocInt(1);
        IntBuffer channels = MemoryUtil.memAllocInt(1);
        ByteBuffer decoded = STBImage.stbi_load_from_memory(input, width, height, channels, 4);
        if (decoded == null) {
            throw new IllegalStateException("stbi_load_from_memory failed: " + STBImage.stbi_failure_reason());
        }
        System.out.println("  decoded " + width.get(0) + "x" + height.get(0)
            + " channels=" + channels.get(0));
        int wrong = 0;
        for (int index = 0; index < EXPECTED.length; index++) {
            int got = decoded.get(index) & 0xFF;
            if (got != EXPECTED[index]) {
                wrong++;
                if (wrong <= 4) {
                    System.out.println("    byte " + index + ": expected " + EXPECTED[index] + " got " + got);
                }
            }
        }
        System.out.println("  compared " + EXPECTED.length + " channel values, " + wrong + " wrong");
        if (width.get(0) != 8 || height.get(0) != 8) {
            throw new IllegalStateException("the decoded size is wrong");
        }
        if (wrong > 0) {
            throw new IllegalStateException("the decoded pixels are not the encoded ones");
        }
        System.out.println("IMAGE OK");
    }
}
