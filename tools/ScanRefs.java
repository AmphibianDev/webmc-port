// Print every reference a set of jars makes into a package prefix, so stubs can be generated for them.
//
//   java -cp <asm>:. ScanRefs baritone/ /tmp/mods/*.jar
//
// One line per reference, tab separated, for tools/build_optional_stubs.py to collect:
//
//   method      <owner>  <name>   <descriptor>
//   field       <owner>  <name>   <descriptor>
//   implements  <owner>  -        interface      (a class in the jar inherits from this type)
//   interface   <owner>  -        -              (a method was called on it with invokeinterface)
//   descriptor  <type>   -        -
//
// The kind matters: a type called with invokeinterface must be stubbed as an interface, and so must one
// that a class in the same set implements.

import org.objectweb.asm.*;

import java.nio.file.*;
import java.util.*;
import java.util.zip.*;

public class ScanRefs {
    private static final Set<String> PREFIXES = new HashSet<>();
    private static final Set<String> REPORTED = new LinkedHashSet<>();

    public static void main(String[] args) throws Exception {
        for (String prefix : args[0].split(",")) {
            if (!prefix.isEmpty()) {
                PREFIXES.add(prefix);
            }
        }
        for (int index = 1; index < args.length; index++) {
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
        REPORTED.forEach(System.out::println);
    }

    private static boolean wanted(String name) {
        if (name == null) {
            return false;
        }
        for (String prefix : PREFIXES) {
            // a class name matches itself and its inner classes; a name ending in '/' is a package. A plain
            // string prefix is wrong here: "java/lang/reflect/Parameter" matched "ParameterizedType", which
            // put a stub in java.* and made javac refuse the whole set.
            if (name.equals(prefix)
                    || (prefix.endsWith("/") && name.startsWith(prefix))
                    || name.startsWith(prefix + "$")) {
                return true;
            }
        }
        return false;
    }

    private static void note(String line) {
        if (REPORTED.size() < 20000) {
            REPORTED.add(line);
        }
    }

    private static void scan(Path path) throws Exception {
        if (path.toString().endsWith(".class")) {
            visit(Files.readAllBytes(path));
            return;
        }
        try (ZipInputStream in = new ZipInputStream(Files.newInputStream(path))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                byte[] data = in.readAllBytes();
                if (entry.getName().endsWith(".class")) {
                    visit(data);
                } else if (entry.getName().endsWith(".jar")) {
                    Path nested = Files.createTempFile("nested", ".jar");
                    Files.write(nested, data);
                    scan(nested);
                    Files.deleteIfExists(nested);
                }
            }
        }
    }

    private static void descriptorTypes(String descriptor) {
        int index = 0;
        while ((index = descriptor.indexOf('L', index)) >= 0) {
            int end = descriptor.indexOf(';', index);
            if (end < 0) {
                return;
            }
            String name = descriptor.substring(index + 1, end);
            if (wanted(name)) {
                note("descriptor\t" + name + "\t-\t-");
            }
            index = end + 1;
        }
    }

    private static void visit(byte[] data) {
        try {
            new ClassReader(data).accept(new ClassVisitor(Opcodes.ASM9) {
                @Override
                public void visit(int version, int access, String name, String signature, String superName,
                                  String[] interfaces) {
                    if (wanted(superName)) {
                        note("implements\t" + superName + "\t-\tinterface");
                    }
                    if (interfaces != null) {
                        for (String parent : interfaces) {
                            if (wanted(parent)) {
                                note("implements\t" + parent + "\t-\tinterface");
                            }
                        }
                    }
                }

                @Override
                public MethodVisitor visitMethod(int access, String methodName, String descriptor, String signature,
                                                 String[] exceptions) {
                    descriptorTypes(descriptor);
                    return new MethodVisitor(Opcodes.ASM9) {
                        @Override
                        public void visitMethodInsn(int opcode, String owner, String target, String targetDesc,
                                                    boolean isInterface) {
                            if (wanted(owner)) {
                                if (isInterface) {
                                    note("interface\t" + owner + "\t-\t-");
                                }
                                note("method\t" + owner + "\t" + target + "\t" + targetDesc);
                                descriptorTypes(targetDesc);
                            }
                        }

                        @Override
                        public void visitFieldInsn(int opcode, String owner, String target, String targetDesc) {
                            if (wanted(owner)) {
                                note("field\t" + owner + "\t" + target + "\t" + targetDesc);
                                descriptorTypes(targetDesc);
                            }
                        }

                        @Override
                        public void visitTypeInsn(int opcode, String type) {
                            if (wanted(type)) {
                                note("type\t" + type + "\t-\tclass");
                            }
                        }
                    };
                }
            }, 0);
        } catch (RuntimeException ignored) {
            // a class this ASM cannot read contributes nothing
        }
    }
}
