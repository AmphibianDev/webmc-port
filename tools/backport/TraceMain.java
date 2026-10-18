// Rewrite the client's own crash-report call in net.minecraft.client.main.Main so the original exception
// reaches us instead of dying inside the report builder.
//
// Why: this client, under this JVM, throws during startup and then NPEs *while building the crash report*
// (measured - the report file is never written, and the only visible frame is an obfuscated report class).
// The phase and the cause are both lost. Main.main is not obfuscated, so its exception handler can be
// redirected precisely: webmc.Diag.die prints the throwable and the description, then throws, which reaches
// the launcher that started it.
import org.objectweb.asm.*;

import java.io.*;
import java.nio.file.*;
import java.util.zip.*;

public class TraceMain {
    public static void main(String[] args) throws Exception {
        Path input = Path.of(args[0]);
        Path output = Path.of(args[1]);
        boolean patched = false;
        try (ZipInputStream in = new ZipInputStream(Files.newInputStream(input));
             ZipOutputStream out = new ZipOutputStream(Files.newOutputStream(output))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                byte[] data = in.readAllBytes();
                if (entry.getName().equals("net/minecraft/client/main/Main.class")) {
                    data = rewrite(data);
                    patched = true;
                }
                out.putNextEntry(new ZipEntry(entry.getName()));
                out.write(data);
                out.closeEntry();
            }
        }
        System.out.println(patched
            ? "the crash-report call in Main is redirected to webmc.Diag.die"
            : "! Main.class was not found: nothing was patched");
    }

    private static byte[] rewrite(byte[] data) {
        ClassReader reader = new ClassReader(data);
        ClassWriter writer = new ClassWriter(reader, 0);
        reader.accept(new ClassVisitor(Opcodes.ASM9, writer) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature,
                                             String[] exceptions) {
                MethodVisitor next = super.visitMethod(access, name, descriptor, signature, exceptions);
                return new MethodVisitor(Opcodes.ASM9, next) {
                    @Override
                    public void visitMethodInsn(int opcode, String owner, String called, String calledDescriptor,
                                                boolean isInterface) {
                        // CrashReport.forThrowable(Throwable, String) - whatever its obfuscated owner is
                        if (opcode == Opcodes.INVOKESTATIC && calledDescriptor.endsWith(";")
                                && calledDescriptor.startsWith("(Ljava/lang/Throwable;Ljava/lang/String;)")
                                && isCrashReport(owner)) {
                            super.visitMethodInsn(Opcodes.INVOKESTATIC, "webmc/Diag", "die",
                                "(Ljava/lang/Throwable;Ljava/lang/String;)Ljava/lang/RuntimeException;", false);
                            super.visitInsn(Opcodes.ATHROW);
                            return;
                        }
                        super.visitMethodInsn(opcode, owner, called, calledDescriptor, isInterface);
                    }
                };
            }
        }, 0);
        return writer.toByteArray();
    }

    /** The report class is obfuscated, so it is recognised by the shape of its class rather than its name. */
    private static boolean isCrashReport(String owner) {
        return owner.length() <= 3 && !owner.startsWith("java/") && !owner.startsWith("oshi/");
    }
}
