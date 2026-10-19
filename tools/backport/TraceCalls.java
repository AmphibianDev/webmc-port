// Instrument the client's own methods so its progress can be watched from outside.
//
// Why this is needed: under this JVM the client cannot be debugged the usual way. Thread.getAllStackTraces
// answers with an empty map, ThreadMXBean reports zero threads, Thread.getStackTrace on another thread
// throws, its own log goes quiet for long stretches, and a stall looks exactly like slowness. The one thing
// that can be arranged is for the client to say what it is doing - so this rewrites named methods to call
// webmc.Diag.mark on entry, and the marks arrive through the client's own logger.
//
//     java TraceCalls <in.jar> <out.jar> <strings-file>
//
// The strings file names method *substrings* to instrument (class or method names), and is a file rather
// than an argument so the same patcher can be pointed at a different set without recompiling.
import org.objectweb.asm.*;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.zip.*;

public class TraceCalls {
    public static void main(String[] args) throws Exception {
        Path input = Path.of(args[0]);
        Path output = Path.of(args[1]);
        List<String> wanted = Files.readAllLines(Path.of(args[2])).stream()
            .map(String::trim).filter(line -> !line.isEmpty() && !line.startsWith("#")).toList();
        System.out.println("instrumenting methods matching: " + wanted);

        int classes = 0;
        int methods = 0;
        try (ZipInputStream in = new ZipInputStream(Files.newInputStream(input));
             ZipOutputStream out = new ZipOutputStream(Files.newOutputStream(output))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                byte[] data = in.readAllBytes();
                if (entry.getName().endsWith(".class")) {
                    int[] count = new int[2];
                    byte[] rewritten = rewrite(data, wanted, count);
                    classes += count[0];
                    methods += count[1];
                    data = rewritten;
                }
                out.putNextEntry(new ZipEntry(entry.getName()));
                out.write(data);
                out.closeEntry();
            }
        }
        System.out.println("instrumented " + methods + " method(s) in " + classes + " class(es)");
    }

    private static byte[] rewrite(byte[] data, List<String> wanted, int[] count) {
        ClassReader reader;
        try {
            reader = new ClassReader(data);
        } catch (RuntimeException problem) {
            return data;
        }
        String className = reader.getClassName();
        // Only classes whose name matches: the markers are class or method name fragments, so this keeps
        // the rewrite to the paths that matter instead of tens of thousands of methods.
        boolean classWanted = matches(className, wanted);
        ClassWriter writer = new ClassWriter(reader, 0);
        boolean[] touched = {false};
        reader.accept(new ClassVisitor(Opcodes.ASM9, writer) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature,
                                             String[] exceptions) {
                MethodVisitor next = super.visitMethod(access, name, descriptor, signature, exceptions);
                if ((access & (Opcodes.ACC_ABSTRACT | Opcodes.ACC_NATIVE)) != 0) {
                    return next;
                }
                if (!classWanted && !matches(className + "." + name, wanted)) {
                    return next;
                }
                String label = className.replace("/", ".") + "." + name;
                return new MethodVisitor(Opcodes.ASM9, next) {
                    @Override
                    public void visitCode() {
                        super.visitCode();
                        // Straight to stdout, with no helper and no reflection: this is the one channel
                        // measured to reach the page's log from inside the client, and every layer between
                        // here and it has been a place for the diagnostic to fail silently.
                        super.visitFieldInsn(Opcodes.GETSTATIC, "java/lang/System", "out",
                            "Ljava/io/PrintStream;");
                        super.visitTypeInsn(Opcodes.NEW, "java/lang/StringBuilder");
                        super.visitInsn(Opcodes.DUP);
                        super.visitMethodInsn(Opcodes.INVOKESPECIAL, "java/lang/StringBuilder", "<init>",
                            "()V", false);
                        super.visitLdcInsn("MARK " + label);
                        super.visitMethodInsn(Opcodes.INVOKEVIRTUAL, "java/lang/StringBuilder", "append",
                            "(Ljava/lang/String;)Ljava/lang/StringBuilder;", false);
                        super.visitMethodInsn(Opcodes.INVOKEVIRTUAL, "java/lang/StringBuilder", "toString",
                            "()Ljava/lang/String;", false);
                        super.visitMethodInsn(Opcodes.INVOKEVIRTUAL, "java/io/PrintStream", "println",
                            "(Ljava/lang/String;)V", false);
                        touched[0] = true;
                    }
                };
            }
        }, 0);
        if (!touched[0]) {
            return data;
        }
        count[0]++;
        count[1] += 1;
        return writer.toByteArray();
    }

    /**
     * Exact class names, or "*" for every class.
     *
     * Instrumenting everything is expensive and it is the last resort: when a client stops in code that
     * none of the obvious classes cover, the answer is only in the one method it entered last, and the only
     * way to know that is to have all of them say so.
     */
    private static boolean matches(String name, List<String> wanted) {
        return wanted.contains("*") || wanted.contains(name);
    }
}
