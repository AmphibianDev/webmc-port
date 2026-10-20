// Who refers to this class, or to this member? The compile says what is missing; this says where from.
//
//   java -cp <asm>:. WhoRefs java/nio/channels/SocketChannel <jar-or-dir>...
//   java -cp <asm>:. WhoRefs java/io/RandomAccessFile getChannel <jar-or-dir>...
//
// Written because a compiler names one missing class per build and a whole-program compile only reports what
// it reached: knowing the referring class is what tells you whether it is a mod's optional feature, a bundled
// library, or the client itself - and those have different fixes.

import org.objectweb.asm.*;

import java.nio.file.*;
import java.util.*;
import java.util.zip.*;

public class WhoRefs {
    private static final Map<String, Integer> REFERRERS = new TreeMap<>();
    private static String target;
    private static String member;
    private static int total = 0;
    private static boolean memberOnly = false;

    public static void main(String[] args) throws Exception {
        // a bare name (no slash) is a *method name*: report every call to it, whatever owns it, because a
        // compiler's "Method X.y()Z was not found" leaves it open which caller asked
        memberOnly = !args[0].contains("/");
        target = memberOnly ? "\u0000never" : args[0];
        if (memberOnly) {
            member = args[0];
        }
        int first = 1;
        if (!memberOnly && args.length > 1 && !args[1].endsWith(".jar") && !Files.isDirectory(Path.of(args[1]))) {
            member = args[1];
            first = 2;
        }
        for (int index = first; index < args.length; index++) {
            Path path = Path.of(args[index]);
            if (Files.isDirectory(path)) {
                try (var walk = Files.walk(path)) {
                    for (Path file : (Iterable<Path>) walk.filter(Files::isRegularFile)::iterator) {
                        if (file.toString().endsWith(".class") || file.toString().endsWith(".jar")) {
                            scan(file);
                        }
                    }
                }
            } else if (Files.isRegularFile(path)) {
                scan(path);
            }
        }
        System.out.println(total + " reference(s) to " + target + (member == null ? "" : "." + member));
        REFERRERS.forEach((name, count) -> System.out.println("  " + count + "x  " + name));
    }

    private static void scan(Path path) throws Exception {
        if (path.toString().endsWith(".class")) {
            visit(path.toString(), Files.readAllBytes(path));
            return;
        }
        try (ZipInputStream in = new ZipInputStream(Files.newInputStream(path))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                byte[] data = in.readAllBytes();
                if (entry.getName().endsWith(".class")) {
                    visit(entry.getName().replace(".class", ""), data);
                } else if (entry.getName().endsWith(".jar")) {
                    Path nested = Files.createTempFile("nested", ".jar");
                    Files.write(nested, data);
                    scan(nested);
                    Files.deleteIfExists(nested);
                }
            }
        }
    }

    private static void note(String from, String what) {
        total++;
        if (REFERRERS.size() < 400) {
            REFERRERS.merge(from + "  (" + what + ")", 1, Integer::sum);
        }
    }

    private static boolean inType(String descriptor) {
        return descriptor != null && descriptor.contains("L" + target + ";");
    }

    private static void visit(String className, byte[] data) {
        try {
            new ClassReader(data).accept(new ClassVisitor(Opcodes.ASM9) {
                @Override
                public void visit(int version, int access, String name, String signature, String superName,
                                  String[] interfaces) {
                    if (target.equals(superName)) {
                        note(className, "extends");
                    }
                    if (interfaces != null) {
                        for (String parent : interfaces) {
                            if (target.equals(parent)) {
                                note(className, "implements");
                            }
                        }
                    }
                    if (inType(signature)) {
                        note(className, "signature");
                    }
                }

                @Override
                public FieldVisitor visitField(int access, String fieldName, String descriptor, String signature,
                                               Object value) {
                    if (target.equals(fieldDescriptorOwner(descriptor))) {
                        note(className, "field type");
                    }
                    return null;
                }

                @Override
                public MethodVisitor visitMethod(int access, String methodName, String descriptor, String signature,
                                                 String[] exceptions) {
                    if (inType(descriptor)) {
                        note(className, "method " + methodName + descriptor);
                    }
                    return new MethodVisitor(Opcodes.ASM9) {
                        @Override
                        public void visitTypeInsn(int opcode, String type) {
                            if (target.equals(type)) {
                                note(className, "type instruction");
                            }
                        }

                        @Override
                        public void visitMethodInsn(int opcode, String owner, String name, String targetDesc,
                                                    boolean isInterface) {
                            if (memberOnly && member.equals(name)) {
                                note(className, "calls " + owner + "." + name + targetDesc);
                            } else if (target.equals(owner) && (member == null || member.equals(name))) {
                                note(className, "calls " + name + targetDesc);
                            } else if (inType(targetDesc) && !memberOnly) {
                                note(className, "descriptor of " + name + targetDesc);
                            }
                        }

                        @Override
                        public void visitFieldInsn(int opcode, String owner, String name, String fieldDescriptor) {
                            if (memberOnly && member.equals(name)) {
                                note(className, "touches " + owner + "." + name + " " + fieldDescriptor);
                            } else if (target.equals(owner) && (member == null || member.equals(name))) {
                                note(className, "touches " + name + " " + fieldDescriptor);
                            }
                        }

                        @Override
                        public void visitLocalVariable(String name, String descriptor, String signature, Label start,
                                                       Label end, int index) {
                            if (inType(descriptor)) {
                                note(className, "local variable");
                            }
                        }
                    };
                }
            }, 0);
        } catch (RuntimeException ignored) {
            // a class this ASM cannot read contributes nothing
        }
    }

    private static String fieldDescriptorOwner(String descriptor) {
        if (descriptor.startsWith("L") && descriptor.endsWith(";")) {
            return descriptor.substring(1, descriptor.length() - 1);
        }
        return null;
    }
}
