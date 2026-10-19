// Repair a mod's method descriptors against the client it will be compiled into.
//
//   java -cp <asm>:. RepairDescriptors <official client jar> <mod jar> <output jar>
//
// A mod is compiled against Fabric's intermediary, and intermediary sometimes disagrees with the official
// client about a type that the JDK grew between versions: Meteor calls FuelValues.fuelItems() expecting
// java.util.Set, while the 1.21.11 client returns java.util.SequencedSet. The compiler then reports
//
//   Method net.minecraft.world.level.block.entity.FuelValues.fuelItems()Ljava/util/Set; was not found
//
// which is true and unhelpful. This asks the client what the method actually looks like and rewrites the
// reference to match, trying the Java 21 spellings of the sequenced types. Anything it cannot resolve is
// left exactly as it was and reported, never guessed at.

import org.objectweb.asm.*;

import java.nio.file.*;
import java.util.*;
import java.util.zip.*;

public class RepairDescriptors {
    /** What a Java 21 type was called before 21, and the other way round. */
    private static final Map<String, String> TO_JAVA21 = Map.of(
            "java/util/Set", "java/util/SequencedSet",
            "java/util/Collection", "java/util/SequencedCollection",
            "java/util/Map", "java/util/SequencedMap");
    private static final Map<String, String> TO_JAVA17 = Map.of(
            "java/util/SequencedSet", "java/util/Set",
            "java/util/SequencedCollection", "java/util/Collection",
            "java/util/SequencedMap", "java/util/Map");

    private static final Map<String, Map<String, Set<String>>> METHODS = new HashMap<>();
    private static final Map<String, String> SUPERCLASS = new HashMap<>();
    private static final Map<String, List<String>> INTERFACES = new HashMap<>();
    private static final Map<String, Integer> REPAIRED = new TreeMap<>();
    private static final Set<String> UNRESOLVED = new TreeSet<>();
    private static final Set<String> MOD_CLASSES = new HashSet<>();
    private static int repaired = 0;

    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            System.out.println("usage: RepairDescriptors <official client jar> <mod jar> <output jar>");
            return;
        }
        Path client = Path.of(args[0]), input = Path.of(args[1]), output = Path.of(args[2]);
        index(client);
        // the mod's own classes: a call into one of them is not a client question
        try (ZipInputStream in = new ZipInputStream(Files.newInputStream(input))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                if (entry.getName().endsWith(".class")) {
                    MOD_CLASSES.add(entry.getName().replace(".class", ""));
                }
            }
        }
        try (ZipInputStream in = new ZipInputStream(Files.newInputStream(input));
             ZipOutputStream out = new ZipOutputStream(Files.newOutputStream(output))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                byte[] data = in.readAllBytes();
                if (entry.getName().endsWith(".class")) {
                    data = repair(data);
                } else if (entry.getName().endsWith(".jar")) {
                    Path nested = Files.createTempFile("nested", ".jar");
                    Path fixed = Files.createTempFile("nested-fixed", ".jar");
                    Files.write(nested, data);
                    main(new String[]{args[0], nested.toString(), fixed.toString()});
                    data = Files.readAllBytes(fixed);
                    Files.deleteIfExists(nested);
                    Files.deleteIfExists(fixed);
                }
                out.putNextEntry(new ZipEntry(entry.getName()));
                out.write(data);
                out.closeEntry();
            }
        }
        System.out.println("repaired " + repaired + " descriptor(s) against " + client.getFileName());
        REPAIRED.forEach((what, count) -> System.out.println("  " + count + "x  " + what));
        if (!UNRESOLVED.isEmpty()) {
            System.out.println("  " + UNRESOLVED.size() + " reference(s) still do not match the client:");
            UNRESOLVED.stream().limit(8).forEach(line -> System.out.println("    " + line));
        }
    }

    private static void index(Path jar) throws Exception {
        try (ZipInputStream in = new ZipInputStream(Files.newInputStream(jar))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                if (!entry.getName().endsWith(".class")) {
                    continue;
                }
                byte[] data = in.readAllBytes();
                String name = entry.getName().replace(".class", "");
                try {
                    ClassReader reader = new ClassReader(data);
                    SUPERCLASS.put(name, reader.getSuperName());
                    INTERFACES.put(name, Arrays.asList(reader.getInterfaces()));
                    reader.accept(new ClassVisitor(Opcodes.ASM9) {
                        @Override
                        public MethodVisitor visitMethod(int access, String methodName, String descriptor,
                                                         String signature, String[] exceptions) {
                            METHODS.computeIfAbsent(name, key -> new HashMap<>())
                                    .computeIfAbsent(methodName, key -> new HashSet<>()).add(descriptor);
                            return null;
                        }
                    }, 0);
                } catch (RuntimeException ignored) {
                    // an unreadable class contributes nothing
                }
            }
        }
    }

    private static boolean declares(String owner, String name, String descriptor) {
        Set<String> seen = new HashSet<>();
        Deque<String> queue = new ArrayDeque<>();
        queue.add(owner);
        while (!queue.isEmpty()) {
            String current = queue.poll();
            if (current == null || !seen.add(current)) {
                continue;
            }
            Map<String, Set<String>> members = METHODS.get(current);
            if (members != null && members.getOrDefault(name, Set.of()).contains(descriptor)) {
                return true;
            }
            if (members == null && !SUPERCLASS.containsKey(current) && !MOD_CLASSES.contains(current)) {
                // neither in the client nor one of the mod's own classes: not ours to judge. The mod's own
                // classes are compiled together with the mod, so a call into one always resolves.
                return true;
            }
            queue.add(SUPERCLASS.get(current));
            for (String parent : INTERFACES.getOrDefault(current, List.of())) {
                queue.add(parent);
            }
        }
        return false;
    }

    private static String swap(String descriptor, Map<String, String> names) {
        StringBuilder text = new StringBuilder(descriptor);
        for (Map.Entry<String, String> entry : names.entrySet()) {
            String from = "L" + entry.getKey() + ";", to = "L" + entry.getValue() + ";";
            int at;
            while ((at = text.indexOf(from)) >= 0) {
                text.replace(at, at + from.length(), to);
            }
        }
        return text.toString();
    }

    /** Can the reference be satisfied as written? The declaring class may be an ancestor of the owner. */
    private static boolean declaresAnywhere(String owner, String name, String descriptor) {
        return declares(owner, name, descriptor);
    }

    /** Try the descriptor as it is, then the Java 21 spelling of the types it mentions. */
    private static String settle(String owner, String name, String descriptor) {
        if (declaresAnywhere(owner, name, descriptor)) {
            return descriptor;
        }
        String asJava21 = swap(descriptor, TO_JAVA21);
        if (!asJava21.equals(descriptor) && declares(owner, name, asJava21)) {
            return asJava21;
        }
        String asJava17 = swap(descriptor, TO_JAVA17);
        if (!asJava17.equals(descriptor) && declares(owner, name, asJava17)) {
            return asJava17;
        }
        return null;
    }

    private static byte[] repair(byte[] data) {
        ClassReader reader;
        try {
            reader = new ClassReader(data);
        } catch (RuntimeException problem) {
            return data;
        }
        ClassWriter writer = new ClassWriter(reader, 0);
        try {
            reader.accept(new ClassVisitor(Opcodes.ASM9, writer) {
                @Override
                public MethodVisitor visitMethod(int access, String methodName, String descriptor, String signature,
                                                 String[] exceptions) {
                    return new MethodVisitor(Opcodes.ASM9, super.visitMethod(access, methodName, descriptor, signature,
                            exceptions)) {
                        @Override
                        public void visitMethodInsn(int opcode, String owner, String target, String targetDescriptor,
                                                    boolean isInterface) {
                            if (owner.startsWith("net/minecraft/")) {
                                String fixed = settle(owner, target, targetDescriptor);
                                if (fixed == null) {
                                    UNRESOLVED.add(owner + "." + target + targetDescriptor);
                                } else if (!fixed.equals(targetDescriptor)) {
                                    REPAIRED.merge(owner + "." + target + " " + targetDescriptor + " -> " + fixed,
                                            1, Integer::sum);
                                    repaired++;
                                    super.visitMethodInsn(opcode, owner, target, fixed, isInterface);
                                    return;
                                }
                            }
                            super.visitMethodInsn(opcode, owner, target, targetDescriptor, isInterface);
                        }
                    };
                }
            }, 0);
            return writer.toByteArray();
        } catch (RuntimeException problem) {
            System.out.println("  ! a class could not be repaired, left as it was: " + problem);
            return data;
        }
    }
}
