// Rewrite a Fabric mod jar from intermediary names to Mojang's official names.
//
// Why this exists: a mod is compiled against intermediary names (`net/minecraft/class_2561`) because that
// is what Fabric Loader hands it at play time. A client compiled by this pipeline has been remapped to
// Mojang's official names (`net/minecraft/network/chat/Component`), so a mod baked into it is full of
// references the compiler cannot resolve - which is what the first build with Meteor died of, with
// "Class net.minecraft.class_2561 was not found" and a gap report.
//
//     java ModRemapper <in.jar> <out.jar> <mappings-prefix>
//
// The prefix names three tsv files (classes, .fields.tsv, .methods.tsv) produced by
// tools/compose_mappings.py. Nested jars are remapped too - Meteor carries fifteen of them - and so are the
// strings in mixin configs and access wideners, because a target named in a string is a reference like any
// other.
//
// Stack map frames are copied rather than recomputed: the rewrite keeps every stack shape, and recomputing
// them (with a hierarchy ASM cannot resolve) is a mistake this project has already made once.
import org.objectweb.asm.*;
import org.objectweb.asm.commons.ClassRemapper;
import org.objectweb.asm.commons.Remapper;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.jar.*;
import java.util.zip.*;

public class ModRemapper {
    private static final Map<String, String> CLASSES = new HashMap<>();
    private static final Map<String, String> FIELDS = new HashMap<>();
    private static final Map<String, String> METHODS = new HashMap<>();
    private static final Map<String, String> FIELDS_BY_DESCRIPTOR = new HashMap<>();
    private static final Map<String, String> METHODS_BY_DESCRIPTOR = new HashMap<>();
    // the same members, keyed without their owner, for calls made through a subclass: a mod compiled
    // against intermediary calls RegistryFriendlyByteBuf.method_10812 while the mapping declares
    // method_10812 on FriendlyByteBuf, and TeaVM wants the name to be official regardless of which class
    // in the hierarchy the call names. The descriptor keeps this exact where an intermediary name is not
    // unique (two of 48,667 methods), and a name is only used alone when it stands for one member.
    private static final Map<String, String> GLOBAL_BY_DESCRIPTOR = new HashMap<>();
    private static final Map<String, String> GLOBAL_FIELDS_BY_DESCRIPTOR = new HashMap<>();
    private static final Map<String, String> GLOBAL_FIELDS_BY_NAME = new HashMap<>();
    private static final Map<String, String> GLOBAL_BY_NAME = new HashMap<>();
    private static final java.util.Set<String> GLOBAL_AMBIGUOUS = new java.util.HashSet<>();
    private static int throughHierarchy = 0;

    public static void main(String[] args) throws Exception {
        Path input = Path.of(args[0]);
        Path output = Path.of(args[1]);
        // accept the prefix with or without a .tsv extension: the Python side writes "<prefix>.tsv" and
        // "<prefix>.fields.tsv", and appending to the full name looked for int-to-official.tsv.fields.tsv
        String prefix = args[2].endsWith(".tsv")
            ? args[2].substring(0, args[2].length() - 4)
            : args[2];
        load(Path.of(prefix));
        System.out.println("mappings: " + CLASSES.size() + " class(es), " + FIELDS.size() + " field(s), "
            + METHODS.size() + " method(s); by descriptor " + FIELDS_BY_DESCRIPTOR.size() + "/"
            + METHODS_BY_DESCRIPTOR.size() + "; ownerless by name " + GLOBAL_FIELDS_BY_NAME.size() + "/"
            + GLOBAL_BY_NAME.size() + ", by descriptor " + GLOBAL_FIELDS_BY_DESCRIPTOR.size() + "/"
            + GLOBAL_BY_DESCRIPTOR.size() + ", ambiguous " + GLOBAL_AMBIGUOUS.size());
        Files.createDirectories(output.getParent() == null ? Path.of(".") : output.getParent());
        int[] counts = new int[3];   // classes remapped, strings rewritten, nested jars
        writeJar(input, output, counts);
        System.out.println("remapped " + counts[0] + " class(es), " + counts[1] + " string reference(s), "
            + counts[2] + " nested jar(s), " + throughHierarchy + " member(s) resolved through the class "
            + "hierarchy -> " + output);
    }

    private static void load(Path prefix) throws IOException {
        for (String line : Files.readAllLines(Path.of(prefix + ".tsv"))) {
            String[] parts = line.split("\t");
            if (parts.length == 2) {
                CLASSES.put(parts[0], parts[1]);
            }
        }
        for (String line : Files.readAllLines(Path.of(prefix + ".fields.tsv"))) {
            String[] parts = line.split("\t");
            if (parts.length == 3) {
                FIELDS.put(parts[0] + "#" + parts[1], parts[2]);
            } else if (parts.length == 4) {
                // with a descriptor: preferred, because obfuscated names repeat inside a class
                FIELDS_BY_DESCRIPTOR.put(parts[0] + "#" + parts[1] + "#" + parts[2], parts[3]);
                FIELDS.putIfAbsent(parts[0] + "#" + parts[1], parts[3]);
                String previousField = GLOBAL_FIELDS_BY_DESCRIPTOR.putIfAbsent(parts[1] + "#" + parts[2], parts[3]);
                if (previousField != null && !previousField.equals(parts[3])) {
                    GLOBAL_AMBIGUOUS.add(parts[1] + "#" + parts[2]);
                }
                previousField = GLOBAL_FIELDS_BY_NAME.putIfAbsent(parts[1], parts[3]);
                if (previousField != null && !previousField.equals(parts[3])) {
                    GLOBAL_FIELDS_BY_NAME.remove(parts[1]);
                    GLOBAL_AMBIGUOUS.add(parts[1]);
                }
            }
        }
        for (String line : Files.readAllLines(Path.of(prefix + ".methods.tsv"))) {
            String[] parts = line.split("\t");
            if (parts.length == 3) {
                METHODS.put(parts[0] + "#" + parts[1], parts[2]);
            } else if (parts.length == 4) {
                METHODS_BY_DESCRIPTOR.put(parts[0] + "#" + parts[1] + "#" + parts[2], parts[3]);
                METHODS.putIfAbsent(parts[0] + "#" + parts[1], parts[3]);
                String previous = GLOBAL_BY_DESCRIPTOR.putIfAbsent(parts[1] + "#" + parts[2], parts[3]);
                if (previous != null && !previous.equals(parts[3])) {
                    GLOBAL_AMBIGUOUS.add(parts[1] + "#" + parts[2]);
                }
                previous = GLOBAL_BY_NAME.putIfAbsent(parts[1], parts[3]);
                if (previous != null && !previous.equals(parts[3])) {
                    GLOBAL_BY_NAME.remove(parts[1]);
                    GLOBAL_AMBIGUOUS.add(parts[1]);
                }
            }
        }
    }

    private static void writeJar(Path input, Path output, int[] counts) throws IOException {
        try (ZipInputStream in = new ZipInputStream(Files.newInputStream(input));
             ZipOutputStream out = new ZipOutputStream(Files.newOutputStream(output))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                byte[] data = in.readAllBytes();
                String name = entry.getName();
                if (name.endsWith(".class")) {
                    byte[] rewritten = remapClass(data);
                    if (rewritten != null) {
                        data = rewritten;
                        counts[0]++;
                    }
                } else if (name.endsWith(".jar")) {
                    // a mod that bundles other mods (Fabric API modules, libraries) brings their
                    // references with it
                    ByteArrayOutputStream nested = new ByteArrayOutputStream();
                    Path temporary = Files.createTempFile("nested", ".jar");
                    Files.write(temporary, data);
                    writeJar(temporary, temporary.resolveSibling(temporary.getFileName() + "-out.jar"), counts);
                    data = Files.readAllBytes(temporary.resolveSibling(temporary.getFileName() + "-out.jar"));
                    Files.deleteIfExists(temporary);
                    Files.deleteIfExists(temporary.resolveSibling(temporary.getFileName() + "-out.jar"));
                    counts[2]++;
                } else if (name.endsWith(".json") || name.endsWith(".accesswidener") || name.endsWith(".txt")) {
                    String text = new String(data, java.nio.charset.StandardCharsets.UTF_8);
                    String rewritten = remapText(text, counts);
                    if (!rewritten.equals(text)) {
                        data = rewritten.getBytes(java.nio.charset.StandardCharsets.UTF_8);
                    }
                }
                // the entry name is not rewritten by the class visitor, so a remapped jar used to keep its
                // obfuscated file names ("fzu.class") while the classes inside were official - which makes
                // the jar useless for anything that looks a class up by name, including this project's
                // member checker
                if (name.endsWith(".class")) {
                    String internal = CLASSES.get(name.substring(0, name.length() - 6));
                    if (internal != null) {
                        name = internal.replace('.', '/') + ".class";
                    }
                }
                out.putNextEntry(new ZipEntry(name));
                out.write(data);
                out.closeEntry();
            }
        }
    }

    /** Class references (and their member references) rewritten with the frames left alone. */
    private static byte[] remapClass(byte[] data) {
        ClassReader reader;
        try {
            reader = new ClassReader(data);
        } catch (RuntimeException problem) {
            return null;
        }
        ClassWriter writer = new ClassWriter(reader, 0);
        Remapper remapper = new Remapper() {
            @Override
            public String map(String internalName) {
                String official = CLASSES.get(internalName);
                if (official == null) {
                    return internalName;
                }
                // a class reference is an internal name: dots here made every remapped call in the mod a
                // call to a name the compiler could not match against any member ("Method
                // net.minecraft.util.Tuple.setA() was not found", with the dotted owner in the message)
                return official.replace('.', '/');
            }

            @Override
            public String mapFieldName(String owner, String name, String descriptor) {
                if (System.getenv("WEBMC_DEBUG_MEMBER") != null && name.equals(System.getenv("WEBMC_DEBUG_MEMBER"))) {
                    System.out.println("  [debug] mapFieldName owner=" + owner + " name=" + name
                        + " descriptor=" + descriptor);
                }
                String exact = FIELDS_BY_DESCRIPTOR.get(owner + "#" + name + "#" + descriptor);
                if (exact != null) {
                    return exact;
                }
                String byOwner = FIELDS.get(owner + "#" + name);
                if (byOwner != null) {
                    return byOwner;
                }
                // a field read through a subclass names the subclass, while the mapping declares it on the
                // ancestor: LocalPlayer.field_5969 is Entity's x, and without this every such read kept its
                // intermediary name
                String byDescriptor = GLOBAL_FIELDS_BY_DESCRIPTOR.get(name + "#" + descriptor);
                if (byDescriptor != null && !GLOBAL_AMBIGUOUS.contains(name + "#" + descriptor)) {
                    throughHierarchy++;
                    return byDescriptor;
                }
                String byName = GLOBAL_FIELDS_BY_NAME.get(name);
                if (byName != null && !GLOBAL_AMBIGUOUS.contains(name)) {
                    throughHierarchy++;
                    return byName;
                }
                return name;
            }

            @Override
            public String mapMethodName(String owner, String name, String descriptor) {
                // the descriptor decides between overloads that share an obfuscated name: Tuple's field,
                // getA and setA are all "a" in the official file
                String exact = METHODS_BY_DESCRIPTOR.get(owner + "#" + name + "#" + descriptor);
                if (exact != null) {
                    return exact;
                }
                String byOwner = METHODS.get(owner + "#" + name);
                if (byOwner != null) {
                    return byOwner;
                }
                String byDescriptor = GLOBAL_BY_DESCRIPTOR.get(name + "#" + descriptor);
                if (byDescriptor != null && !GLOBAL_AMBIGUOUS.contains(name + "#" + descriptor)) {
                    throughHierarchy++;
                    return byDescriptor;
                }
                String byName = GLOBAL_BY_NAME.get(name);
                if (byName != null && !GLOBAL_AMBIGUOUS.contains(name)) {
                    throughHierarchy++;
                    return byName;
                }
                return name;
            }
        };
        try {
            ClassRemapper delegate = new ClassRemapper(writer, remapper) {
                @Override
                public MethodVisitor visitMethod(int access, String name, String descriptor, String signature,
                                                 String[] exceptions) {
                    MethodVisitor inner = super.visitMethod(access, name, descriptor, signature, exceptions);
                    return inner == null ? null : new MethodVisitor(Opcodes.ASM9, inner) {
                        @Override
                        public void visitMethodInsn(int opcode, String owner, String target, String targetDesc,
                                                    boolean isInterface) {
                            // java.base has methods the compiler's class library does not, and this one is
                            // called from code somebody else compiled: the call is pointed at a small shim
                            // that ships with the client instead of being left to fail translation
                            if (owner.equals("java/lang/Class") && target.equals("getPackageName")
                                    && targetDesc.equals("()Ljava/lang/String;")) {
                                super.visitMethodInsn(Opcodes.INVOKESTATIC, "webmc/shim/Jdk", "packageName",
                                        "(Ljava/lang/Class;)Ljava/lang/String;", false);
                                return;
                            }
                            super.visitMethodInsn(opcode, owner, target, targetDesc, isInterface);
                        }
                    };
                }
            };
            reader.accept(delegate, 0);
            return writer.toByteArray();
        } catch (RuntimeException problem) {
            System.out.println("  ! a class could not be remapped, left as it was: " + problem);
            return null;
        }
    }

    /** Names inside strings: mixin targets, access widener entries, refmap entries. */
    private static String remapText(String text, int[] counts) {
        StringBuilder out = new StringBuilder(text.length());
        java.util.regex.Matcher matcher = java.util.regex.Pattern
            .compile("net/minecraft/(class_\\d+)|net\\.minecraft\\.(class_\\d+)|(method_\\d+)|(field_\\d+)")
            .matcher(text);
        int last = 0;
        while (matcher.find()) {
            out.append(text, last, matcher.start());
            String found = matcher.group();
            String mapped = null;
            if (matcher.group(1) != null || matcher.group(2) != null) {
                String internal = "net/minecraft/" + (matcher.group(1) != null ? matcher.group(1) : matcher.group(2));
                String official = CLASSES.get(internal);
                mapped = official == null ? null : (matcher.group(2) != null ? official.replace('/', '.') : official);
            } else {
                // a member name on its own cannot be mapped without knowing its owner: mixin configs name
                // their targets as classes, and the members live in the annotations ASM already handled
                mapped = null;
            }
            if (mapped != null) {
                out.append(mapped);
                counts[1]++;
            } else {
                out.append(found);
            }
            last = matcher.end();
        }
        out.append(text, last, text.length());
        return out.toString();
    }
}
