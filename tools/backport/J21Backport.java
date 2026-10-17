import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ConstantDynamic;
import org.objectweb.asm.Handle;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.commons.ClassRemapper;
import org.objectweb.asm.commons.Remapper;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipOutputStream;

/**
 * Make a Java 21 class run on a Java 17 JVM.
 *
 * <p>Why this exists: a browser JVM (CheerpJ) supports Java 8, 11 and 17 - Java 21 is announced but not
 * shipped - and Minecraft 1.21.11 is Java 21 bytecode. Running the real client in a tab therefore needs
 * the client's *use* of Java 21 to be removed, not the client to be recompiled. That use turns out to be
 * small and specific: measured against the official 1.21.11 client, 127 call sites across 27 members,
 * and most of those members already exist in Java 17 (a `Deque` has had `addFirst` for twenty years). What
 * is genuinely new is a handful of methods on `List`, `Math.clamp`, `Thread.threadId`, `List.reversed`,
 * and two interfaces (`SequencedMap`, `SequencedSet`) that Java 17's own collections do not implement.</p>
 *
 * <p>Three rewrites, all of them mechanical:</p>
 * <ul>
 *   <li>the class file version goes from 65 to 61, which is what makes a Java 17 JVM load it at all;</li>
 *   <li>the two new interfaces are remapped to the interfaces they extend (`SequencedMap` to `Map`,
 *       `SequencedSet` to `Set`), so a field or a cast naming them still resolves;</li>
 *   <li>a call to a method Java 17 does not have becomes a call to {@code webmc.j21.J21}, a small class
 *       that implements it in terms of what Java 17 does have (this is not emulation for its own sake:
 *       {@code List.getFirst()} really is {@code get(0)} with an emptiness check).</li>
 * </ul>
 *
 * <p>The output is written class by class, and the tool reports every rewrite it made, so "it was
 * backported" is a list rather than a claim.</p>
 */
public final class J21Backport {

    /** Methods that arrived in Java 21 on types that Java 17 already has: call sites become shim calls. */
    private static final Map<String, Map<String, String>> NEW_METHODS = new LinkedHashMap<>();
    /** Types that do not exist in Java 17: references become the type they extend. */
    /** Owners whose new methods are static: the shim call keeps the original descriptor, with no receiver. */
    private static final Set<String> STATIC_OWNERS = new LinkedHashSet<>(Set.of("java/lang/Math"));

    /**
     * The new members, by name, for *any* owner.
     *
     * A call is not always addressed to the interface: `ImmutableList.getFirst()` (Guava) and the same
     * method on fastutil's lists are addressed to the concrete class, and matching only `java/util/List`
     * left them calling a method Java 17 does not have - "NoSuchMethodError:
     * 'java.lang.Object com.google.common.collect.ImmutableList.getFirst()'" was the first one. The name is
     * what makes it new; the receiver is added as a cast, because a Guava list *is* a List.
     */
    private static final Map<String, String> NEW_BY_NAME = new LinkedHashMap<>();

    static {
        for (Map<String, String> rewrites : NEW_METHODS.values()) {
            NEW_BY_NAME.putAll(rewrites);
        }
    }

    private static final Map<String, String> NEW_TYPES = new LinkedHashMap<>(Map.of(
            "java/util/SequencedCollection", "java/util/Collection",
            "java/util/SequencedSet", "java/util/Set",
            "java/util/SequencedMap", "java/util/Map",
            // Java 21's MatchException, thrown by pattern matching in a switch, is in 151 of the client's
            // catch clauses and does not exist in Java 17 at all. It becomes our own class of the same shape.
            "java/lang/MatchException", "webmc/j21/MatchException"));

    static {
        Map<String, String> list = new LinkedHashMap<>();
        list.put("getFirst", "listGetFirst");
        list.put("getLast", "listGetLast");
        list.put("addFirst", "listAddFirst");
        list.put("addLast", "listAddLast");
        list.put("removeFirst", "listRemoveFirst");
        list.put("removeLast", "listRemoveLast");
        list.put("reversed", "listReversed");
        NEW_METHODS.put("java/util/List", list);
        // Deque's addFirst/addLast/removeFirst/removeLast/getFirst/getLast have existed since Java 6, so a
        // call through Deque is left alone - only the List ones are new.
        Map<String, String> math = new LinkedHashMap<>();
        math.put("clamp", "clamp");
        NEW_METHODS.put("java/lang/Math", math);
        Map<String, String> thread = new LinkedHashMap<>();
        thread.put("threadId", "threadId");
        NEW_METHODS.put("java/lang/Thread", thread);
        NEW_METHODS.put("java/lang/Thread$Builder", new LinkedHashMap<>(Map.of("name", "threadName")));
    }

    private J21Backport() {
    }

    /** What one class had rewritten in it, so the run can be read. */
    private static final class Tally {
        int classes;
        int versionRewrites;
        int typeRewrites;
        int methodRewrites;
        final Map<String, Integer> methods = new LinkedHashMap<>();

        void method(String name) {
            this.methodRewrites += 1;
            this.methods.merge(name, 1, Integer::sum);
        }
    }

    public static void main(String[] args) throws IOException {
        if (args.length != 2) {
            System.err.println("usage: J21Backport <input.jar> <output.jar>");
            System.exit(2);
        }
        Path input = Path.of(args[0]);
        Path output = Path.of(args[1]);
        Tally tally = new Tally();
        List<String> remaining = new ArrayList<>();

        // A class the JVM cannot resolve is as fatal as a method it cannot find, so both are audited.
        ClassLoader probe = J21Backport.class.getClassLoader();

        try (ZipFile archive = new ZipFile(input.toFile());
             ZipOutputStream out = new ZipOutputStream(Files.newOutputStream(output))) {
            Enumeration<? extends ZipEntry> entries = archive.entries();
            while (entries.hasMoreElements()) {
                ZipEntry entry = entries.nextElement();
                String entryName = entry.getName();
                // The official client jar is signed. A class file that has been rewritten cannot match its
                // signature any more, and a Java 17 JVM refuses *every* class in a jar whose digests do not
                // add up ("SHA-384 digest error"), so the signature is removed with the reason: it attested
                // to bytes that no longer exist. The manifest keeps everything that is not a digest.
                if (entryName.startsWith("META-INF/")
                        && (entryName.endsWith(".SF") || entryName.endsWith(".DSA") || entryName.endsWith(".RSA")
                            || entryName.endsWith(".EC"))) {
                    System.out.println("removed signature file " + entryName);
                    continue;
                }
                if (entryName.equals("META-INF/MANIFEST.MF")) {
                    writeManifest(archive, entry, out);
                    continue;
                }
                if (entry.isDirectory() || !entryName.endsWith(".class")) {
                    writeUnchanged(archive, entry, out);
                    continue;
                }
                byte[] original;
                try (InputStream stream = archive.getInputStream(entry)) {
                    original = stream.readAllBytes();
                }
                byte[] backported = backport(entry.getName(), original, tally, remaining);
                ZipEntry copy = new ZipEntry(entry.getName());
                copy.setTime(entry.getTime());
                out.putNextEntry(copy);
                out.write(backported);
                out.closeEntry();
            }

            // The rewritten calls land in the shim, so the shim travels with the client: a class file that
            // calls webmc/j21/J21 is useless in a jar that does not carry it.
            for (String shim : List.of("webmc/j21/J21.class", "webmc/j21/MatchException.class")) {
                try (InputStream stream = J21Backport.class.getClassLoader().getResourceAsStream(shim)) {
                    if (stream == null) {
                        System.out.println("! the shim " + shim + " is not on this tool's own classpath");
                        continue;
                    }
                    ZipEntry entry = new ZipEntry(shim);
                    out.putNextEntry(entry);
                    stream.transferTo(out);
                    out.closeEntry();
                    System.out.println("carried " + shim + " into the output");
                }
            }
        }

        System.out.println("classes written:      " + tally.classes);
        System.out.println("class versions 65->61: " + tally.versionRewrites);
        System.out.println("type references remapped: " + tally.typeRewrites);
        System.out.println("method calls rewritten:   " + tally.methodRewrites);
        for (Map.Entry<String, Integer> entry : tally.methods.entrySet()) {
            System.out.println("   " + entry.getValue() + "× " + entry.getKey());
        }
        if (!remaining.isEmpty()) {
            System.out.println("STILL JAVA 21 (" + remaining.size() + "): the backport is incomplete, and a build must not");
            System.out.println("pretend otherwise. The first few:");
            for (String line : remaining.subList(0, Math.min(20, remaining.size()))) {
                System.out.println("   " + line);
            }
            System.exit(1);
        }
        System.out.println("no Java 21-only API reference remains");
    }

    /** The manifest without its digest entries: the digests describe files this tool has changed. */
    private static void writeManifest(ZipFile archive, ZipEntry entry, ZipOutputStream out) throws IOException {
        StringBuilder text = new StringBuilder();
        boolean inMainSection = true;
        try (java.io.BufferedReader reader = new java.io.BufferedReader(
                new java.io.InputStreamReader(archive.getInputStream(entry), java.nio.charset.StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isEmpty()) {
                    inMainSection = false;
                    continue;
                }
                if (!inMainSection) {
                    continue; // per-entry sections are all digests
                }
                if (line.startsWith("Manifest-Version") || line.startsWith("Main-Class")
                        || line.startsWith("Multi-Release") || line.startsWith("Enable-Native-Access")) {
                    text.append(line).append("\r\n");
                }
            }
        }
        if (text.length() == 0) {
            text.append("Manifest-Version: 1.0\r\n");
        }
        text.append("\r\n");
        byte[] body = text.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8);
        ZipEntry copy = new ZipEntry("META-INF/MANIFEST.MF");
        out.putNextEntry(copy);
        out.write(body);
        out.closeEntry();
    }

    private static void writeUnchanged(ZipFile archive, ZipEntry entry, ZipOutputStream out) throws IOException {
        ZipEntry copy = new ZipEntry(entry.getName());
        copy.setTime(entry.getTime());
        out.putNextEntry(copy);
        if (!entry.isDirectory()) {
            try (InputStream stream = archive.getInputStream(entry)) {
                stream.transferTo(out);
            }
        }
        out.closeEntry();
    }

    private static byte[] backport(String name, byte[] original, Tally tally, List<String> remaining) {
        ClassReader reader = new ClassReader(original);
        // Frames are *copied*, not recomputed. Every rewrite here has the same stack and local shapes as
        // what it replaces (a call becomes a call, a type becomes its parent), so the original frames stay
        // true - and recomputing them with a `getCommonSuperClass` that answers "Object" for everything
        // loses the precision a verifier needs ("Bad type on operand stack: Object is not assignable to
        // Throwable" was how that showed up, in a method that catches and re-throws).
        ClassWriter writer = new ClassWriter(reader, 0);

        Remapper remapper = new Remapper() {
            @Override
            public String map(String internalName) {
                String mapped = NEW_TYPES.get(internalName);
                if (mapped != null) {
                    tally.typeRewrites += 1;
                    return mapped;
                }
                return internalName;
            }
        };

        ClassVisitor visitor = new ClassRemapper(writer, remapper) {
            @Override
            public void visit(int version, int access, String className, String signature, String superName,
                              String[] interfaces) {
                int major = version & 0xFFFF;
                int rewritten = version;
                if (major > Opcodes.V17) {
                    // keep the preview flag off as well: a preview class file is refused by a JVM that has
                    // not enabled preview features
                    rewritten = Opcodes.V17;
                    tally.versionRewrites += 1;
                }
                tally.classes += 1;
                super.visit(rewritten, access, className, signature, superName, interfaces);
            }

            @Override
            public MethodVisitor visitMethod(int access, String methodName, String descriptor, String signature,
                                             String[] exceptions) {
                MethodVisitor next = super.visitMethod(access, methodName, descriptor, signature, exceptions);
                return new MethodVisitor(Opcodes.ASM9, next) {
                    /**
                     * A method *reference* (a lambda like `list::getFirst`, or a method handle constant) is
                     * not a call instruction: it is a Handle in the constant pool, and leaving those alone
                     * is how a backport ends up "complete" with a hundred Java 21 references still in it.
                     */
                    private Handle rewriteHandle(Handle handle) {
                        if (handle == null) {
                            return null;
                        }
                        String replacement = NEW_BY_NAME.get(handle.getName());
                        if (replacement == null) {
                            return handle;
                        }
                        tally.method(replacement);
                        String receiver = STATIC_OWNERS.contains(handle.getOwner()) ? handle.getOwner()
                                : shimReceiverType(handle.getName());
                        return new Handle(Opcodes.H_INVOKESTATIC, "webmc/j21/J21", replacement,
                                shimDescriptor(receiver, handle.getName(), handle.getDesc()), false);
                    }

                    @Override
                    public void visitLdcInsn(Object value) {
                        if (value instanceof Handle handle) {
                            super.visitLdcInsn(rewriteHandle(handle));
                            return;
                        }
                        if (value instanceof ConstantDynamic dynamic) {
                            Object[] args = new Object[dynamic.getBootstrapMethodArgumentCount()];
                            boolean changed = false;
                            for (int i = 0; i < args.length; i++) {
                                Object argument = dynamic.getBootstrapMethodArgument(i);
                                if (argument instanceof Handle handle) {
                                    args[i] = rewriteHandle(handle);
                                    changed = changed || args[i] != argument;
                                } else {
                                    args[i] = argument;
                                }
                            }
                            if (changed) {
                                super.visitLdcInsn(new ConstantDynamic(dynamic.getName(), dynamic.getDescriptor(),
                                        dynamic.getBootstrapMethod(), args));
                                return;
                            }
                        }
                        super.visitLdcInsn(value);
                    }

                    @Override
                    public void visitInvokeDynamicInsn(String name, String descriptor, Handle bootstrapMethodHandle,
                                                       Object... bootstrapMethodArguments) {
                        Object[] args = new Object[bootstrapMethodArguments.length];
                        for (int i = 0; i < args.length; i++) {
                            Object argument = bootstrapMethodArguments[i];
                            args[i] = argument instanceof Handle handle ? rewriteHandle(handle) : argument;
                        }
                        super.visitInvokeDynamicInsn(name, descriptor, bootstrapMethodHandle, args);
                    }

                    @Override
                    public void visitMethodInsn(int opcode, String owner, String calledName, String calledDescriptor,
                                                boolean isInterface) {
                        String replacement = NEW_BY_NAME.get(calledName);
                        if (replacement != null && !STATIC_OWNERS.contains(owner)) {
                            tally.method(replacement);
                            // The shim takes a List (or Thread) as its first argument: cast the receiver, so a
                            // concrete type like Guava's ImmutableList is accepted where the interface is
                            // expected. The cast is free at run time for anything that really is one.
                            super.visitTypeInsn(Opcodes.CHECKCAST, shimReceiverType(calledName));
                            super.visitMethodInsn(Opcodes.INVOKESTATIC, "webmc/j21/J21", replacement,
                                    shimDescriptor(shimReceiverType(calledName), calledName, calledDescriptor), false);
                            return;
                        }
                        if (replacement != null) {
                            tally.method(replacement);
                            super.visitMethodInsn(Opcodes.INVOKESTATIC, "webmc/j21/J21", replacement,
                                    shimDescriptor(owner, calledName, calledDescriptor), false);
                            return;
                        }
                        super.visitMethodInsn(opcode, owner, calledName, calledDescriptor, isInterface);
                    }
                };
            }
        };

        reader.accept(visitor, 0);
        byte[] written = writer.toByteArray();
        audit(name, written, remaining);
        return written;
    }

    /**
     * The shim's descriptor: for an instance method the receiver becomes the first argument, and for a
     * static one the descriptor is unchanged. Getting this wrong is a `NoSuchMethodError` at run time that
     * names a method nobody wrote - "int java.lang.Math.clamp(long, int, int)" was the first one.
     */
    /** What the shim's first parameter is for a given new method: the list methods take a List, the rest. */
    private static String shimReceiverType(String name) {
        return switch (name) {
            case "getFirst", "getLast", "addFirst", "addLast", "removeFirst", "removeLast", "reversed" -> "java/util/List";
            case "threadId" -> "java/lang/Thread";
            default -> "java/lang/Object";
        };
    }

    private static String shimDescriptor(String owner, String name, String descriptor) {
        if (STATIC_OWNERS.contains(owner)) {
            return descriptor;
        }
        String prefix = "L" + owner + ";";
        return "(" + prefix + descriptor.substring(1);
    }

    /** Read every class back and refuse to call the job done while a Java 21 reference is left. */
    private static void audit(String name, byte[] bytes, List<String> remaining) {
        ClassReader reader = new ClassReader(bytes);
        if ((reader.readUnsignedShort(6)) > Opcodes.V17) {
            remaining.add(name + ": the class version is still " + reader.readUnsignedShort(6));
        }
        reader.accept(new ClassVisitor(Opcodes.ASM9) {
            private void flag(String owner, String member, String descriptor) {
                if (NEW_BY_NAME.containsKey(member) && !STATIC_OWNERS.contains(owner)) {
                    remaining.add(name + ": " + owner + "#" + member + descriptor);
                }
                if (NEW_BY_NAME.containsKey(member) && STATIC_OWNERS.contains(owner)) {
                    remaining.add(name + ": " + owner + "#" + member + descriptor);
                }
            }

            @Override
            public MethodVisitor visitMethod(int access, String methodName, String descriptor, String signature,
                                             String[] exceptions) {
                return new MethodVisitor(Opcodes.ASM9) {
                    @Override
                    public void visitMethodInsn(int opcode, String owner, String calledName, String calledDescriptor,
                                                boolean isInterface) {
                        flag(owner, calledName, calledDescriptor);
                    }

                    @Override
                    public void visitLdcInsn(Object value) {
                        if (value instanceof Handle handle) {
                            flag(handle.getOwner(), handle.getName(), handle.getDesc());
                        }
                    }

                    @Override
                    public void visitInvokeDynamicInsn(String name, String descriptor, Handle bootstrapMethodHandle,
                                                       Object... bootstrapMethodArguments) {
                        for (Object argument : bootstrapMethodArguments) {
                            if (argument instanceof Handle handle) {
                                flag(handle.getOwner(), handle.getName(), handle.getDesc());
                            }
                        }
                    }
                };
            }
        }, 0);
    }
}
