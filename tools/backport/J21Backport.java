import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ConstantDynamic;
import org.objectweb.asm.Handle;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.FieldVisitor;
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
import java.util.jar.JarEntry;
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
    /**
     * The new members that are *static*, by owner and name.
     *
     * Staticness is per member, not per owner: `Thread.ofPlatform()` is static and `Thread.threadId()` is
     * not, and treating one like the other produces a call to a shim method that does not exist - which is
     * exactly what "NoSuchMethodError: webmc.j21.J21.builderName(java.lang.Object)" was.
     */
    private static final Set<String> STATIC_MEMBERS = new LinkedHashSet<>(Set.of(
            "java/lang/Math#clamp",
            "java/lang/Thread#sleep"));

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

    /**
     * The names that may be matched for *any* owner, and the owners they may be matched on.
     *
     * A call is often addressed to a concrete class (`ImmutableList.getFirst()`), which is why matching by
     * name is needed at all - but a name alone is not enough: `RenderPipeline$UniformDescription.name()` is
     * a record accessor, and rewriting that into a thread-builder call is how a tool breaks a jar. So the
     * list methods are matched across the collection types, and everything else only on its own owner.
     */
    private static final Set<String> LIST_METHODS = new LinkedHashSet<>(Set.of(
            "getFirst", "getLast", "addFirst", "addLast", "removeFirst", "removeLast", "reversed"));

    /**
     * Types that really are Lists, worked out from the jars on the client's classpath.
     *
     * The JDK's own hierarchy is not enough: `ImmutableList.getFirst()` is addressed to Guava's class, and
     * the client calls it (that is how this list came to exist). Guessing from a package name failed in
     * both directions - it rewrote `Iterables.getFirst(a, b)` and `Comparator.reversed()`, which are not
     * lists at all - so the hierarchy is *read*: a class is a List when its superclass or one of its
     * interfaces says so, transitively, across the jars this tool was given.
     */
    private static final Set<String> LIST_TYPES = new LinkedHashSet<>(Set.of("java/util/List"));
    private static final Map<String, String> HIERARCHY = new LinkedHashMap<>();

    private static void readHierarchy(List<Path> jars) throws IOException {
        for (Path jar : jars) {
            if (!Files.isRegularFile(jar)) {
                continue;
            }
            try (java.util.jar.JarFile archive = new java.util.jar.JarFile(jar.toFile())) {
                java.util.Enumeration<JarEntry> entries = archive.entries();
                while (entries.hasMoreElements()) {
                    JarEntry entry = entries.nextElement();
                    if (!entry.getName().endsWith(".class")) {
                        continue;
                    }
                    try (InputStream stream = archive.getInputStream(entry)) {
                        byte[] bytes = stream.readAllBytes();
                        ClassReader reader = new ClassReader(bytes);
                        HIERARCHY.put(reader.getClassName(), reader.getSuperName());
                        for (String iface : reader.getInterfaces()) {
                            HIERARCHY.merge(reader.getClassName(), iface, (a, b) -> a + "," + b);
                        }
                    } catch (RuntimeException | IOException ignored) {
                        // a class this cannot read contributes no hierarchy, which only makes the matching
                        // more conservative
                    }
                }
            }
        }
        // then the closure: anything whose chain reaches java/util/List is a List
        for (String type : HIERARCHY.keySet()) {
            if (reachesList(type, new LinkedHashSet<>())) {
                LIST_TYPES.add(type);
            }
        }
    }

    private static boolean reachesList(String type, Set<String> seen) {
        if (type == null || !seen.add(type)) {
            return false;
        }
        if (type.equals("java/util/List") || type.equals("java/util/AbstractList")) {
            return true;
        }
        String parents = HIERARCHY.get(type);
        if (parents == null) {
            return false;
        }
        for (String parent : parents.split(",")) {
            if (reachesList(parent, seen)) {
                return true;
            }
        }
        return false;
    }

    /**
     * Whether this call is one of the new members, given its owner *and* its descriptor.
     *
     * The descriptor matters as much as the name: Guava has `Iterables.getFirst(Iterable, T)`, which is
     * older than Java 21 and takes two arguments, while Java 21's `List.getFirst()` takes none. Matching on
     * the name alone rewrote the Guava call into the shim's one-argument form, and the JVM said
     * "Operand stack underflow" - which is a kind way of saying the tool made nonsense bytecode.
     */
    private static String replacementFor(String owner, String member, String descriptor) {
        // Thread.sleep(long) has been there since Java 1.0 and Thread.sleep(Duration) arrived in Java 19:
        // the name alone cannot tell them apart, so this one member is matched on its descriptor.
        if (owner.equals("java/lang/Thread") && member.equals("sleep")
                && !"(Ljava/time/Duration;)V".equals(descriptor)) {
            return null;
        }
        Map<String, String> exact = NEW_METHODS.get(owner);
        if (exact != null && exact.containsKey(member)) {
            return exact.get(member);
        }
        // A call is not always addressed to the interface: the client calls `ImmutableList.getFirst()` and
        // fastutil's own `ObjectArrayList.getFirst()` too, so the *owner* has to be allowed to differ - but
        // only when it really is a List (read from the classpath's hierarchy, not guessed from a package
        // name) and only when the descriptor has the shape Java 21's method has. Both of those checks exist
        // because the looser versions of this broke the client: a record accessor called `name()`, Guava's
        // two-argument `Iterables.getFirst(a, b)` ("Operand stack underflow"), and `Comparator.reversed()`
        // cast to a List.
        if (LIST_METHODS.contains(member) && LIST_TYPES.contains(owner) && hasJava21Arity(member, descriptor)) {
            return NEW_BY_NAME.get(member);
        }
        return null;
    }

    /** Java 21's list methods take no arguments, except the two that add one. */
    private static boolean hasJava21Arity(String member, String descriptor) {
        if (descriptor == null || !descriptor.startsWith("(")) {
            return false;
        }
        int arguments = org.objectweb.asm.Type.getArgumentCount(descriptor);
        return switch (member) {
            case "addFirst", "addLast" -> arguments == 1;
            default -> arguments == 0;
        };
    }

    private static final Map<String, String> NEW_TYPES = new LinkedHashMap<>(Map.of(
            "java/util/SequencedCollection", "java/util/Collection",
            "java/util/SequencedSet", "java/util/Set",
            "java/util/SequencedMap", "java/util/Map",
            // Java 21's MatchException, thrown by pattern matching in a switch, is in 151 of the client's
            // catch clauses and does not exist in Java 17 at all. It becomes our own class of the same shape.
            "java/lang/MatchException", "webmc/j21/MatchException",
            "java/time/Duration", "java/time/Duration"));

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
        // Java 19 added sleep(Duration); Java 17 has only the (long, int) form
        thread.put("sleep", "sleepDuration");
        NEW_METHODS.put("java/lang/Thread", thread);
        // filled here, at the end of the same static block, because Java runs static initialisers in
        // textual order and an empty table would silently rewrite nothing at all
        for (Map<String, String> rewrites : NEW_METHODS.values()) {
            NEW_BY_NAME.putAll(rewrites);
        }
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
        if (args.length < 2) {
            System.err.println("usage: J21Backport <input.jar> <output.jar> [<library.jar> …]");
            System.exit(2);
        }
        Path input = Path.of(args[0]);
        Path output = Path.of(args[1]);
        List<Path> libraries = new ArrayList<>();
        for (int i = 2; i < args.length; i++) {
            libraries.add(Path.of(args[i]));
        }
        readHierarchy(libraries);
        Tally tally = new Tally();
        List<String> remaining = new ArrayList<>();
        List<String> failed = new ArrayList<>();

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
                byte[] backported;
                try {
                    backported = backport(entry.getName(), original, tally, remaining);
                } catch (RuntimeException failure) {
                    System.out.println("! " + entry.getName() + " could not be backported (" + failure + "): copied"
                            + " unchanged, and named here rather than failing the whole jar");
                    failed.add(entry.getName());
                    backported = original;
                }
                ZipEntry copy = new ZipEntry(entry.getName());
                copy.setTime(entry.getTime());
                out.putNextEntry(copy);
                out.write(backported);
                out.closeEntry();
            }

            // The rewritten calls land in the shim, so the shim travels with the client: a class file that
            // calls webmc/j21/J21 is useless in a jar that does not carry it. Written once, and skipped when the
            // input already has it - backporting a mod that is a *bundle* runs this over jars that were already
            // through it, and a duplicate entry makes ZipOutputStream refuse the whole output ("duplicate entry:
            // webmc/j21/J21.class"), which is how the first attempt at a Fabric mod produced an empty file.
            for (String shim : List.of("webmc/j21/J21.class", "webmc/j21/MatchException.class")) {
                if (written.contains(shim)) {
                    continue;
                }
                try (InputStream stream = J21Backport.class.getClassLoader().getResourceAsStream(shim)) {
                    if (stream == null) {
                        System.out.println("! the shim " + shim + " is not on this tool's own classpath");
                        continue;
                    }
                    ZipEntry entry = new ZipEntry(shim);
                    out.putNextEntry(entry);
                    stream.transferTo(out);
                    out.closeEntry();
                    written.add(shim);
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
        if (!failed.isEmpty()) {
            System.out.println("classes copied unchanged after a failed backport: " + failed.size());
            for (String line : failed.subList(0, Math.min(10, failed.size()))) {
                System.out.println("   " + line);
            }
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
                        String replacement = replacementFor(handle.getOwner(), handle.getName(), handle.getDesc());
                        if (replacement == null || handle.getDesc() == null || !handle.getDesc().startsWith("(")) {
                            return handle;
                        }
                        tally.method(replacement);
                        boolean isStatic = STATIC_MEMBERS.contains(handle.getOwner() + "#" + handle.getName());
                        String receiver = isStatic ? handle.getOwner() : shimReceiverType(handle.getName());
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
                        if (owner.contains("fastutil") && calledName.equals("getFirst")) {
                            System.out.println("  DEBUG owner=" + owner + " inListMethods=" + LIST_METHODS.contains(calledName)
                                    + " inListTypes=" + LIST_TYPES.contains(owner)
                                    + " arity=" + hasJava21Arity(calledName, calledDescriptor)
                                    + " byName=" + NEW_BY_NAME.get(calledName)
                                    + " byNameSize=" + NEW_BY_NAME.size()
                                    + " desc=" + calledDescriptor);
                        }
                        String replacement = replacementFor(owner, calledName, calledDescriptor);
                        boolean isStatic = STATIC_MEMBERS.contains(owner + "#" + calledName);
                        if (replacement != null && calledDescriptor != null && calledDescriptor.startsWith("(")
                                && !isStatic) {
                            tally.method(replacement);
                            // The shim takes a List (or Thread) as its first argument: cast the receiver, so a
                            // concrete type like Guava's ImmutableList is accepted where the interface is
                            // expected. The cast is free at run time for anything that really is one.
                            super.visitTypeInsn(Opcodes.CHECKCAST, shimReceiverType(calledName));
                            super.visitMethodInsn(Opcodes.INVOKESTATIC, "webmc/j21/J21", replacement,
                                    shimDescriptor(shimReceiverType(calledName), calledName, calledDescriptor), false);
                            return;
                        }
                        if (replacement != null && calledDescriptor != null && calledDescriptor.startsWith("(")) {
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
    private static String shimReceiverType(String member) {
        // the *original* member name, as the client wrote it
        return switch (member) {
            case "getFirst", "getLast", "addFirst", "addLast", "removeFirst", "removeLast", "reversed" -> "java/util/List";
            case "threadId", "sleep" -> "java/lang/Thread";
            default -> "java/lang/Object";
        };
    }

    private static String shimDescriptor(String owner, String name, String descriptor) {
        if (descriptor == null || !descriptor.startsWith("(")) {
            // a field handle, not a method: nothing here applies, and building a method descriptor out of
            // a field descriptor is how this tool once crashed inside ASM with an index error
            throw new IllegalArgumentException("not a method descriptor: " + descriptor);
        }
        if (STATIC_MEMBERS.contains(owner + "#" + name)) {
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
            /**
             * A declaration's own descriptor. A field or method declared as `SequencedMap` is as fatal as a
             * call to it, and a scan of the constant pool cannot tell a live declaration from a stale entry,
             * because the pool of a rewritten class keeps the names it no longer uses. This reads what ASM
             * was actually handed, so it can.
             */
            private void flagDescriptor(String descriptor, String what) {
                if (descriptor == null || descriptor.isEmpty()) {
                    return;
                }
                for (String type : Set.of("java/util/SequencedMap", "java/util/SequencedSet",
                        "java/util/SequencedCollection", "java/lang/MatchException")) {
                    if (descriptor.contains("L" + type + ";")) {
                        remaining.add(name + ": " + what + " is declared as " + type);
                    }
                }
            }

            private void flag(String owner, String member, String descriptor) {
                if (owner.startsWith("webmc/j21/")) {
                    return; // the shim implements these: flagging it would report the fix as the problem
                }
                if (replacementFor(owner, member, descriptor) != null) {
                    remaining.add(name + ": " + owner + "#" + member + descriptor);
                }
            }

            @Override
            public FieldVisitor visitField(int access, String fieldName, String descriptor, String signature,
                                           Object value) {
                flagDescriptor(descriptor, "field " + fieldName);
                return super.visitField(access, fieldName, descriptor, signature, value);
            }
            @Override
            public MethodVisitor visitMethod(int access, String methodName, String descriptor, String signature,
                                             String[] exceptions) {
                flagDescriptor(descriptor, "method " + methodName);
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
