// What does a set of jars reference that the compiler's class library does not have?
//
//   java MissingRefs --classlib <teavm-classlib.jar> --scan <client.jar> <mod.jar>...
//
// A whole-program browser compiler resolves every reference it reaches; the ones it cannot resolve stop the
// build, one per run. This asks the question directly and offline: for every class, method and field the
// inputs reference, is it provided by the class library (which names its classes org/teavm/classlib/.../TX)
// or by the inputs themselves? What is left is exactly the list a build would choke on, in one go.
//
// Output is one line per gap:
//
//   class   <internal name>
//   method  <owner> <name> <descriptor>
//   field   <owner> <name> <descriptor>
//
// with a trailing summary of how many distinct references were checked.

import org.objectweb.asm.*;

import java.nio.file.*;
import java.util.*;
import java.util.zip.*;

public class MissingRefs {
    private static final String CLASSLIB_PREFIX = "org/teavm/classlib/";
    private static final Set<String> AVAILABLE = new HashSet<>();          // class names the compiler can see
    private static final Map<String, Set<String>> METHODS = new HashMap<>(); // only for classlib classes
    private static final Map<String, Set<String>> FIELDS = new HashMap<>();
    private static final Set<String> SUPERS = new HashSet<>();              // anything with a supertype we know
    private static final Map<String, String> SUPERCLASS = new HashMap<>();
    private static final Map<String, List<String>> INTERFACES = new HashMap<>();
    private static final Set<String> APP = new HashSet<>();

    private static final Set<String> MISSING_CLASS = new TreeSet<>();
    private static final Set<String> MISSING_METHOD = new TreeSet<>();
    private static final Set<String> MISSING_FIELD = new TreeSet<>();
    private static int checked = 0;

    public static void main(String[] args) throws Exception {
        List<Path> classlib = new ArrayList<>(), scan = new ArrayList<>();
        List<Path> list = null;
        for (String argument : args) {
            switch (argument) {
                case "--classlib" -> list = classlib;
                case "--scan" -> list = scan;
                default -> {
                    if (list != null) {
                        for (Path path : expand(Path.of(argument))) {
                            list.add(path);
                        }
                    }
                }
            }
        }
        for (Path jar : classlib) {
            indexClasslib(jar);
        }
        for (Path jar : scan) {
            indexApp(jar);
        }
        for (Path jar : scan) {
            visit(jar, false);
        }
        for (Path jar : classlib) {
            visit(jar, true);
        }
        System.out.println("class\t" + String.join("\nclass\t", MISSING_CLASS));
        System.out.println("method\t" + String.join("\nmethod\t", MISSING_METHOD));
        System.out.println("field\t" + String.join("\nfield\t", MISSING_FIELD));
        System.out.println("# checked " + checked + " reference(s): " + MISSING_CLASS.size() + " class(es), "
                + MISSING_METHOD.size() + " method(s), " + MISSING_FIELD.size() + " field(s) unavailable");
    }

    /** Accepts a jar, a class file, or a directory to walk. */
    private static List<Path> expand(Path path) {
        List<Path> found = new ArrayList<>();
        if (Files.isDirectory(path)) {
            try (var walk = Files.walk(path)) {
                for (Path file : (Iterable<Path>) walk.filter(Files::isRegularFile)::iterator) {
                    if (file.toString().endsWith(".jar") || file.toString().endsWith(".class")) {
                        found.add(file);
                    }
                }
            } catch (Exception ignored) {
                // an unreadable directory contributes nothing
            }
        } else {
            found.add(path);
        }
        return found;
    }

    /** The class library names its implementation of java.lang.Thread org/teavm/classlib/java/lang/TThread. */
    private static String classlibName(String internal) {
        if (!internal.startsWith(CLASSLIB_PREFIX)) {
            return null;
        }
        String rest = internal.substring(CLASSLIB_PREFIX.length());
        int slash = rest.lastIndexOf('/');
        String packageName = slash < 0 ? "" : rest.substring(0, slash + 1);
        String simple = slash < 0 ? rest : rest.substring(slash + 1);
        if (simple.startsWith("T")) {
            return packageName + simple.substring(1);
        }
        return null;
    }

    private static void indexClasslib(Path jar) throws Exception {
        each(jar, (name, data) -> {
            String mapped = classlibName(name);
            if (mapped != null) {
                AVAILABLE.add(mapped);
                remember(mapped, data, true);
            } else {
                AVAILABLE.add(name);
            }
        });
    }

    private static void indexApp(Path jar) throws Exception {
        each(jar, (name, data) -> {
            APP.add(name);
            AVAILABLE.add(name);
            remember(name, data, false);
        });
    }

    private static void remember(String name, byte[] data, boolean classlib) {
        try {
            ClassReader reader = new ClassReader(data);
            if (!classlib) {
                SUPERCLASS.put(name, reader.getSuperName());
                INTERFACES.put(name, Arrays.asList(reader.getInterfaces()));
            }
            if (reader.getSuperName() != null) {
                SUPERS.add(name);
            }
            reader.accept(new ClassVisitor(Opcodes.ASM9) {
                @Override
                public MethodVisitor visitMethod(int access, String methodName, String descriptor, String signature,
                                                 String[] exceptions) {
                    METHODS.computeIfAbsent(name, key -> new HashSet<>()).add(methodName + descriptor);
                    return null;
                }

                @Override
                public FieldVisitor visitField(int access, String fieldName, String descriptor, String signature,
                                               Object value) {
                    FIELDS.computeIfAbsent(name, key -> new HashSet<>()).add(fieldName + " " + descriptor);
                    return null;
                }
            }, ClassReader.SKIP_CODE);
        } catch (RuntimeException ignored) {
            // unreadable: treated as having no members, which can only make this report more conservative
        }
    }

    private interface ClassSink {
        void accept(String name, byte[] data) throws Exception;
    }

    private static void each(Path path, ClassSink sink) throws Exception {
        if (path.toString().endsWith(".class")) {
            sink.accept(path.getFileName().toString().replace(".class", ""), Files.readAllBytes(path));
            return;
        }
        try (ZipInputStream in = new ZipInputStream(Files.newInputStream(path))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                byte[] data = in.readAllBytes();
                if (entry.getName().endsWith(".class")) {
                    sink.accept(entry.getName().replace(".class", ""), data);
                } else if (entry.getName().endsWith(".jar")) {
                    Path nested = Files.createTempFile("nested", ".jar");
                    Files.write(nested, data);
                    each(nested, sink);
                    Files.deleteIfExists(nested);
                }
            }
        }
    }

    private static void visit(Path path, boolean classlib) throws Exception {
        each(path, (name, data) -> {
            String owner = classlib ? classlibName(name) : name;
            if (owner != null) {
                scanClass(owner, data);
            }
        });
    }

    private static void scanClass(String owner, byte[] data) {
        try {
            new ClassReader(data).accept(new ClassVisitor(Opcodes.ASM9) {
                @Override
                public void visit(int version, int access, String name, String signature, String superName,
                                  String[] interfaces) {
                    checkClass(superName);
                    if (interfaces != null) {
                        for (String parent : interfaces) {
                            checkClass(parent);
                        }
                    }
                }

                @Override
                public FieldVisitor visitField(int access, String fieldName, String descriptor, String signature,
                                               Object value) {
                    checkDescriptor(descriptor);
                    return null;
                }

                @Override
                public MethodVisitor visitMethod(int access, String methodName, String descriptor, String signature,
                                                 String[] exceptions) {
                    checkDescriptor(descriptor);
                    if (exceptions != null) {
                        for (String thrown : exceptions) {
                            checkClass(thrown);
                        }
                    }
                    return new MethodVisitor(Opcodes.ASM9) {
                        @Override
                        public void visitTypeInsn(int opcode, String type) {
                            checkClass(type);
                        }

                        @Override
                        public void visitMethodInsn(int opcode, String target, String name, String targetDesc,
                                                    boolean isInterface) {
                            checkClass(target);
                            checkDescriptor(targetDesc);
                            if (target.startsWith("java/") || target.startsWith("javax/")) {
                                checked++;
                                if (!declaresMethod(target, name, targetDesc)) {
                                    MISSING_METHOD.add(target + " " + name + " " + targetDesc);
                                }
                            }
                        }

                        @Override
                        public void visitFieldInsn(int opcode, String target, String name, String fieldDesc) {
                            checkClass(target);
                            checkDescriptor(fieldDesc);
                            if (target.startsWith("java/") || target.startsWith("javax/")) {
                                checked++;
                                if (!declaresField(target, name, fieldDesc)) {
                                    MISSING_FIELD.add(target + " " + name + " " + fieldDesc);
                                }
                            }
                        }

                        @Override
                        public void visitInvokeDynamicInsn(String name, String descriptor, Handle bootstrap,
                                                           Object... arguments) {
                            checkDescriptor(descriptor);
                            for (Object argument : arguments) {
                                if (argument instanceof Handle handle) {
                                    checkClass(handle.getOwner());
                                    checkDescriptor(handle.getDesc());
                                } else if (argument instanceof Type type) {
                                    checkDescriptor(type.getDescriptor());
                                }
                            }
                        }
                    };
                }
            }, 0);
        } catch (RuntimeException problem) {
            // never silent: a swallowed exception here made this tool report a clean bill of health for a
            // class it had stopped reading after the constructor
            if (System.getenv("WEBMC_DEBUG") != null) {
                System.err.println("[probe] " + owner + " could not be scanned: " + problem);
                StackTraceElement[] trace = problem.getStackTrace();
                for (int index = 0; index < Math.min(4, trace.length); index++) {
                    System.err.println("[probe]     at " + trace[index]);
                }
            }
        }
    }

    private static boolean declaresMethod(String owner, String name, String descriptor) {
        Set<String> seen = new HashSet<>();
        Deque<String> queue = new ArrayDeque<>();
        queue.add(owner);
        while (!queue.isEmpty()) {
            String current = queue.poll();
            if (current == null || !seen.add(current)) {
                continue;
            }
            Set<String> members = METHODS.get(current);
            if (members != null && members.contains(name + descriptor)) {
                return true;
            }
            if (members == null && !AVAILABLE.contains(current)) {
                // an unavailable class we are already reporting: do not also report its members
                return true;
            }
            String parentClass = SUPERCLASS.get(current);
            if (parentClass != null) {
                queue.add(parentClass);
            }
            for (String parent : INTERFACES.getOrDefault(current, List.of())) {
                if (parent != null) {
                    queue.add(parent);
                }
            }
        }
        return false;
    }

    private static boolean declaresField(String owner, String name, String descriptor) {
        Set<String> seen = new HashSet<>();
        Deque<String> queue = new ArrayDeque<>();
        queue.add(owner);
        while (!queue.isEmpty()) {
            String current = queue.poll();
            if (current == null || !seen.add(current)) {
                continue;
            }
            Set<String> members = FIELDS.get(current);
            if (members != null && members.contains(name + " " + descriptor)) {
                return true;
            }
            if (members == null && !AVAILABLE.contains(current)) {
                return true;
            }
            String parentClass = SUPERCLASS.get(current);
            if (parentClass != null) {
                queue.add(parentClass);
            }
            for (String parent : INTERFACES.getOrDefault(current, List.of())) {
                if (parent != null) {
                    queue.add(parent);
                }
            }
        }
        return false;
    }

    private static void checkClass(String name) {
        if (name == null || name.startsWith("[")) {
            return;
        }
        if (name.startsWith("java/") || name.startsWith("javax/") || name.startsWith("jdk/")) {
            checked++;
            if (!AVAILABLE.contains(name)) {
                MISSING_CLASS.add(name);
            }
        }
    }

    private static void checkDescriptor(String descriptor) {
        int index = 0;
        while ((index = descriptor.indexOf('L', index)) >= 0) {
            int end = descriptor.indexOf(';', index);
            if (end < 0) {
                return;
            }
            checkClass(descriptor.substring(index + 1, end));
            index = end + 1;
        }
    }
}
