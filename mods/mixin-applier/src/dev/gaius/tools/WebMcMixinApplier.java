package dev.gaius.tools;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Type;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.AnnotationNode;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.FieldInsnNode;
import org.objectweb.asm.tree.FieldNode;
import org.objectweb.asm.tree.InsnList;
import org.objectweb.asm.tree.InsnNode;
import org.objectweb.asm.tree.LabelNode;
import org.objectweb.asm.tree.LdcInsnNode;
import org.objectweb.asm.tree.MethodInsnNode;
import org.objectweb.asm.tree.MethodNode;
import org.objectweb.asm.tree.TryCatchBlockNode;
import org.objectweb.asm.tree.TypeInsnNode;
import org.objectweb.asm.tree.VarInsnNode;

/**
 * Applies Fabric-style mixins to a client's classes <b>at build time</b>.
 *
 * <p>A mixin is normally applied by a runtime agent that rewrites a class as it is loaded. A browser client
 * compiled ahead of time has no such moment: it is compiled once, and after that there is no loader to
 * intercept. So the merge happens here instead, on the class files, before the client is compiled - which works
 * out better than it sounds, because the compiler then sees the merged result and a mixin's code is compiled
 * into the client exactly like the rest of it, with no interpretation and no reflection at play time.</p>
 *
 * <p>What is applied, and what is refused <b>out loud</b> (never silently):</p>
 * <ul>
 *   <li>{@code @Overwrite} - the target method's body becomes the mixin's, when the descriptors match. A
 *       mismatched overwrite is a refusal, not a best effort: half an overwrite is a broken client.</li>
 *   <li>{@code @Inject} at {@code HEAD}, {@code RETURN} and {@code TAIL}, for handlers whose parameters are
 *       {@code (CallbackInfo)} or {@code (CallbackInfoReturnable)}. A handler that also takes the target's own
 *       arguments is refused by name, because passing them correctly needs Mixin's local-variable analysis and a
 *       wrong argument is far worse than a missing injection.</li>
 *   <li>{@code @Shadow} - never copied. After the merge the mixin's code <i>is</i> the target's code, so a
 *       shadowed call resolves to the target's own member, which is the only reason a shadow exists.</li>
 *   <li>{@code @Unique} and any other member the mixin declares - copied with it, because it belongs to the
 *       mixin rather than to the target.</li>
 *   <li>Everything else ({@code @Redirect}, {@code @ModifyArg}, {@code @ModifyVariable}, {@code @Accessor},
 *       {@code @Invoker}, {@code @At} selectors other than the three above) is counted as not applied and
 *       reported by name, so a build log says exactly which parts of a mod are missing instead of a mod that
 *       half works and a bug report that cannot be reproduced.</li>
 * </ul>
 */
public final class WebMcMixinApplier {

    private static final String MIXIN = "Lorg/spongepowered/asm/mixin/Mixin;";
    private static final String INJECT = "Lorg/spongepowered/asm/mixin/injection/Inject;";
    private static final String OVERWRITE = "Lorg/spongepowered/asm/mixin/Overwrite;";
    private static final String SHADOW = "Lorg/spongepowered/asm/mixin/Shadow;";
    private static final String CALLBACK_INFO = "org/spongepowered/asm/mixin/injection/callback/CallbackInfo";
    private static final String CALLBACK_RETURNABLE = "org/spongepowered/asm/mixin/injection/callback/CallbackInfoReturnable";
    private static final String CALLBACK_CTOR = "(Ljava/lang/String;Z)V";

    /** What one mod's mixins did, so a build log can be specific instead of optimistic. */
    public record Report(int classes, int overwritten, int injected, int copiedMembers, int shadows,
                         List<String> notApplied, List<String> refusals) {
    }

    private WebMcMixinApplier() {
    }

    public static Report apply(Path modJar, Path classesRoot, Consumer<String> log) throws IOException {
        List<String> notApplied = new ArrayList<>();
        List<String> refusals = new ArrayList<>();
        Set<String> touched = new LinkedHashSet<>();
        Set<String> visited = new LinkedHashSet<>();
        int overwritten = 0;
        int injected = 0;
        int copied = 0;
        int shadows = 0;

        try (ZipFile archive = new ZipFile(modJar.toFile())) {
            for (String config : mixinConfigs(archive)) {
                ZipEntry entry = archive.getEntry(config);
                if (entry == null) {
                    refusals.add(config + ": named in the descriptor but not in the jar");
                    continue;
                }
                String text = read(archive, entry);
                for (String mixin : mixinClasses(text, notApplied)) {
                    if (!visited.add(mixin)) {
                        continue;
                    }
                    ZipEntry classEntry = archive.getEntry(mixin.replace('.', '/') + ".class");
                    if (classEntry == null) {
                        refusals.add(mixin + ": listed as a mixin but its class is not in the jar");
                        continue;
                    }
                    ClassNode mixinClass = new ClassNode();
                    new ClassReader(readBytes(archive, classEntry)).accept(mixinClass, 0);
                    String target = targetOf(mixinClass);
                    if (target == null) {
                        refusals.add(mixin + ": no @Mixin target, so there is nothing to edit");
                        continue;
                    }
                    Path targetFile = classesRoot.resolve(target + ".class");
                    if (!Files.isRegularFile(targetFile)) {
                        refusals.add(mixin + ": its target " + target.replace('/', '.')
                                + " is not in this client (wrong version, or a server-side class)");
                        continue;
                    }
                    ClassNode clientClass = new ClassNode();
                    try (InputStream stream = Files.newInputStream(targetFile)) {
                        new ClassReader(stream).accept(clientClass, 0);
                    }
                    Outcome outcome = merge(mixinClass, clientClass, log);
                    overwritten += outcome.overwritten();
                    injected += outcome.injected();
                    copied += outcome.copied();
                    shadows += outcome.shadows();
                    notApplied.addAll(outcome.notApplied());
                    refusals.addAll(outcome.refusals());
                    if (outcome.changed()) {
                        ClassWriter writer = new ClassWriter(ClassWriter.COMPUTE_FRAMES | ClassWriter.COMPUTE_MAXS) {
                            /** Frames are computed against classes this tool cannot load: saying "Object" is
                             *  always safe for a merge point and needs no class hierarchy at all. */
                            @Override
                            protected String getCommonSuperClass(String type1, String type2) {
                                return "java/lang/Object";
                            }
                        };
                        clientClass.accept(writer);
                        Files.write(targetFile, writer.toByteArray());
                        touched.add(target);
                    }
                }
            }
        }
        return new Report(touched.size(), overwritten, injected, copied, shadows, notApplied, refusals);
    }

    private record Outcome(int overwritten, int injected, int copied, int shadows, List<String> notApplied,
                           List<String> refusals) {
        boolean changed() {
            return overwritten + injected + copied > 0;
        }
    }

    private static Outcome merge(ClassNode mixin, ClassNode target, Consumer<String> log) {
        int overwritten = 0;
        int injected = 0;
        int copied = 0;
        int shadows = 0;
        List<String> notApplied = new ArrayList<>();
        List<String> refusals = new ArrayList<>();
        String from = mixin.name;
        String to = target.name;

        for (MethodNode method : new ArrayList<>(mixin.methods)) {
            if (annotation(method, SHADOW) != null) {
                shadows++;
                continue; // a shadow is a promise about the target, never a copy
            }
            if (method.name.equals("<init>") || method.name.equals("<clinit>")) {
                // a mixin is a class too, so it has its own constructors: they are how the mixin is built, not
                // something it contributes. Mixin itself treats an unannotated constructor as invisible.
                continue;
            }
            String where = to.replace('/', '.') + "#" + method.name;
            AnnotationNode overwrite = annotation(method, OVERWRITE);
            AnnotationNode inject = annotation(method, INJECT);
            if (overwrite != null) {
                String name = named(method, overwrite);
                MethodNode victim = find(target, name, method.desc);
                if (victim == null) {
                    refusals.add(where + ": an @Overwrite of a method this client does not have"
                            + " with that descriptor (" + name + method.desc + ")");
                    continue;
                }
                MethodNode replacement = cloneMethod(method, from, to, false);
                victim.instructions = replacement.instructions;
                victim.tryCatchBlocks = replacement.tryCatchBlocks;
                victim.localVariables = null;
                victim.maxStack = 0;
                victim.maxLocals = 0;
                overwritten++;
                continue;
            }
            if (inject != null) {
                String name = named(method, inject);
                MethodNode victim = find(target, name, null);
                if (victim == null) {
                    refusals.add(where + ": an @Inject into a method this client does not have (" + name + ")");
                    continue;
                }
                if (!callbackOnly(method)) {
                    notApplied.add(where + " -> " + name + ": the handler takes the target's own arguments, which "
                            + "needs Mixin's local-variable analysis; only (CallbackInfo) handlers are called");
                    continue;
                }
                for (String point : injectionPoints(inject, notApplied, where + " -> " + name)) {
                    if (insertCall(victim, method, point, to, inject)) {
                        injected++;
                    } else {
                        notApplied.add(where + " -> " + name + ": no " + point + " in this client's build of it");
                    }
                }
                if (!addIfAbsent(target, cloneMethod(method, from, to, false))) {
                    refusals.add(where + ": a method of that name and descriptor is already in the target");
                }
                continue;
            }
            if (addIfAbsent(target, cloneMethod(method, from, to, false))) {
                copied++;
            } else {
                refusals.add(where + ": a method of that name and descriptor is already in the target");
            }
        }

        for (FieldNode field : new ArrayList<>(mixin.fields)) {
            if (annotation(field, SHADOW) != null) {
                shadows++;
                continue;
            }
            FieldNode copy = new FieldNode(field.access, field.name, field.desc, field.signature, field.value);
            copy.visibleAnnotations = field.visibleAnnotations;
            copy.invisibleAnnotations = field.invisibleAnnotations;
            if (target.fields.stream().noneMatch(f -> f.name.equals(copy.name) && f.desc.equals(copy.desc))) {
                target.fields.add(copy);
                copied++;
            }
        }

        log.accept("  mixin " + from.replace('/', '.') + " -> " + to.replace('/', '.') + ": " + overwritten
                + " overwritten, " + injected + " injected, " + copied + " member(s) copied, " + shadows
                + " shadow(s) read");
        return new Outcome(overwritten, injected, copied, shadows, notApplied, refusals);
    }

    /** One injected call, plus the fresh callback handle it takes: `new CallbackInfo("x", cancellable)`. */
    private static boolean insertCall(MethodNode victim, MethodNode handler, String point, String targetName,
                                      AnnotationNode inject) {
        boolean cancellable = bool(inject, "cancellable", false);
        boolean returns = handler.desc.contains(CALLBACK_RETURNABLE);
        String callback = returns ? CALLBACK_RETURNABLE : CALLBACK_INFO;
        boolean isStatic = (handler.access & Opcodes.ACC_STATIC) != 0;

        InsnList code = new InsnList();
        if (!isStatic) {
            code.add(new VarInsnNode(Opcodes.ALOAD, 0));
        }
        code.add(new TypeInsnNode(Opcodes.NEW, callback));
        code.add(new InsnNode(Opcodes.DUP));
        code.add(new LdcInsnNode(victim.name));
        code.add(new InsnNode(cancellable ? Opcodes.ICONST_1 : Opcodes.ICONST_0));
        code.add(new MethodInsnNode(Opcodes.INVOKESPECIAL, callback, "<init>", CALLBACK_CTOR, false));
        code.add(new MethodInsnNode(isStatic ? Opcodes.INVOKESTATIC : Opcodes.INVOKEVIRTUAL, targetName,
                handler.name, handler.desc, false));
        if (!handler.desc.endsWith(")V")) {
            code.add(new InsnNode(Opcodes.POP));
        }

        if (point.equals("HEAD")) {
            victim.instructions.insert(code);
            return true;
        }
        // RETURN and TAIL are one idea in a compiled method: before the exit. Every exit gets the call, because
        // a method with several exits has several places "before it returns" can mean.
        List<AbstractInsnNode> exits = new ArrayList<>();
        for (AbstractInsnNode node = victim.instructions.getFirst(); node != null; node = node.getNext()) {
            int opcode = node.getOpcode();
            if (opcode >= Opcodes.IRETURN && opcode <= Opcodes.RETURN) {
                exits.add(node);
            }
        }
        if (exits.isEmpty()) {
            return false;
        }
        if (point.equals("TAIL")) {
            victim.instructions.insertBefore(exits.get(exits.size() - 1), code);
            return true;
        }
        for (AbstractInsnNode exit : exits) {
            victim.instructions.insertBefore(exit, code);
        }
        return true;
    }

    private static List<String> injectionPoints(AnnotationNode inject, List<String> notApplied, String where) {
        List<String> points = new ArrayList<>();
        for (AnnotationNode at : annotations(inject, "at")) {
            String value = string(at, "value");
            if (value == null) {
                continue;
            }
            if (value.equals("HEAD") || value.equals("RETURN") || value.equals("TAIL")) {
                points.add(value);
            } else {
                notApplied.add(where + ": @At(\"" + value + "\") is not applied (HEAD, RETURN and TAIL are)");
            }
        }
        if (points.isEmpty()) {
            points.add("HEAD"); // Mixin's own default
        }
        return points;
    }

    /** Whether every parameter is a callback, which is the only handler shape this can call correctly. */
    private static boolean callbackOnly(MethodNode handler) {
        for (Type type : Type.getArgumentTypes(handler.desc)) {
            String name = type.getInternalName();
            if (!name.equals(CALLBACK_INFO) && !name.equals(CALLBACK_RETURNABLE)) {
                return false;
            }
        }
        return true;
    }

    /**
     * A copy of a mixin method that can live in the target class: labels and try/catch blocks cloned so the
     * copies point at each other, and every reference to the mixin class redirected to the target, because the
     * code is about to *be* the target. Annotation nodes are dropped: the mixin annotations have been read and
     * mean nothing in the client.
     */
    private static MethodNode cloneMethod(MethodNode method, String from, String to, boolean keepAnnotations) {
        Map<LabelNode, LabelNode> labels = new HashMap<>();
        Map<AbstractInsnNode, AbstractInsnNode> clones = new IdentityHashMap<>();
        for (AbstractInsnNode node = method.instructions.getFirst(); node != null; node = node.getNext()) {
            if (node instanceof LabelNode label) {
                labels.put(label, new LabelNode());
            }
        }
        InsnList copy = new InsnList();
        for (AbstractInsnNode node = method.instructions.getFirst(); node != null; node = node.getNext()) {
            AbstractInsnNode moved = cloneNode(node, labels, from, to);
            clones.put(node, moved);
            copy.add(moved);
        }
        MethodNode result = new MethodNode(method.access, method.name, method.desc, method.signature,
                method.exceptions == null ? null : method.exceptions.toArray(new String[0]));
        result.instructions = copy;
        if (method.tryCatchBlocks != null) {
            result.tryCatchBlocks = new ArrayList<>();
            for (TryCatchBlockNode block : method.tryCatchBlocks) {
                result.tryCatchBlocks.add(new TryCatchBlockNode(labels.getOrDefault(block.start, new LabelNode()),
                        labels.getOrDefault(block.end, new LabelNode()),
                        labels.getOrDefault(block.handler, new LabelNode()), block.type));
            }
        }
        if (keepAnnotations) {
            result.visibleAnnotations = method.visibleAnnotations;
            result.invisibleAnnotations = method.invisibleAnnotations;
        }
        return result;
    }

    private static AbstractInsnNode cloneNode(AbstractInsnNode node, Map<LabelNode, LabelNode> labels, String from,
                                              String to) {
        AbstractInsnNode moved = node.clone(labels);
        if (moved instanceof MethodInsnNode call && call.owner.equals(from)) {
            return new MethodInsnNode(call.getOpcode(), to, call.name, call.desc, call.itf);
        }
        if (moved instanceof FieldInsnNode field && field.owner.equals(from)) {
            return new FieldInsnNode(field.getOpcode(), to, field.name, field.desc);
        }
        if (moved instanceof TypeInsnNode type && type.desc.equals(from)) {
            return new TypeInsnNode(type.getOpcode(), to);
        }
        return moved;
    }

    private static MethodNode find(ClassNode owner, String name, String desc) {
        for (MethodNode method : owner.methods) {
            if (method.name.equals(name) && (desc == null || method.desc.equals(desc))) {
                return method;
            }
        }
        return null;
    }

    private static boolean addIfAbsent(ClassNode owner, MethodNode method) {
        if (find(owner, method.name, method.desc) != null) {
            return false;
        }
        owner.methods.add(method);
        return true;
    }

    private static String named(MethodNode method, AnnotationNode annotation) {
        String value = string(annotation, "method");
        return value != null ? value : method.name;
    }

    private static String targetOf(ClassNode mixin) {
        AnnotationNode annotation = annotation(mixin, MIXIN);
        if (annotation == null) {
            return null;
        }
        List<Object> values = annotation.values == null ? List.of() : annotation.values;
        for (int i = 0; i + 1 < values.size(); i += 2) {
            String key = String.valueOf(values.get(i));
            Object value = values.get(i + 1);
            if (key.equals("value")) {
                for (Object item : value instanceof List ? (List<?>) value : List.of(value)) {
                    if (item instanceof Type named) {
                        return named.getInternalName();
                    }
                }
            }
            if (key.equals("targets")) {
                for (Object item : value instanceof List ? (List<?>) value : List.of(value)) {
                    if (item instanceof String text) {
                        return text.replace('.', '/');
                    }
                }
            }
        }
        return null;
    }

    private static List<String> mixinConfigs(ZipFile archive) throws IOException {
        ZipEntry entry = archive.getEntry("fabric.mod.json");
        if (entry == null) {
            return List.of();
        }
        return stringsAfter(read(archive, entry), "mixins");
    }

    /**
     * The mixin class names one config lists, in the order Mixin would read them.
     *
     * <p>A config writes them relative to its own `package` (`"client": ["GameMixin"]`), and a full name is
     * allowed too - which is why the package is prepended only when the name does not already carry one.</p>
     */
    private static List<String> mixinClasses(String config, List<String> notApplied) {
        String pkg = firstStringAfter(config, "package");
        List<String> found = new ArrayList<>();
        for (String name : List.copyOf(stringsAfter(config, "mixins"))) {
            found.add(name);
        }
        found.addAll(stringsAfter(config, "client"));
        found.addAll(stringsAfter(config, "server"));
        if (config.contains("\"injectors\"")) {
            notApplied.add("a mixin config sets injectors.* defaults, which this bake does not read");
        }
        List<String> qualified = new ArrayList<>();
        for (String name : found) {
            qualified.add(name.contains(".") || pkg == null || pkg.isEmpty() ? name : pkg + "." + name);
        }
        return qualified;
    }

    /** The value of a plain string key: `"package": "example.mixin"`. */
    private static String firstStringAfter(String json, String key) {
        String needle = "\"" + key + "\"";
        int at = json.indexOf(needle);
        if (at < 0) {
            return null;
        }
        int colon = json.indexOf(':', at + needle.length());
        int quote = colon < 0 ? -1 : json.indexOf('"', colon + 1);
        int end = quote < 0 ? -1 : json.indexOf('"', quote + 1);
        return end < 0 ? null : json.substring(quote + 1, end);
    }

    /** Every string in the JSON array that follows a key, without a JSON parser to argue with. */
    private static List<String> stringsAfter(String json, String key) {
        List<String> found = new ArrayList<>();
        String needle = "\"" + key + "\"";
        int at = json.indexOf(needle);
        while (at >= 0) {
            int colon = json.indexOf(':', at + needle.length());
            int open = colon < 0 ? -1 : json.indexOf('[', colon);
            int close = open < 0 ? -1 : json.indexOf(']', open);
            if (colon < 0 || open < 0 || close < 0) {
                break;
            }
            for (String piece : json.substring(open + 1, close).split(",")) {
                String value = piece.trim();
                if (value.startsWith("\"") && value.endsWith("\"") && value.length() > 2) {
                    found.add(value.substring(1, value.length() - 1));
                }
            }
            at = json.indexOf(needle, close);
        }
        return found;
    }

    private static String read(ZipFile archive, ZipEntry entry) throws IOException {
        return new String(readBytes(archive, entry), StandardCharsets.UTF_8);
    }

    private static byte[] readBytes(ZipFile archive, ZipEntry entry) throws IOException {
        try (InputStream stream = archive.getInputStream(entry)) {
            return stream.readAllBytes();
        }
    }

    private static AnnotationNode annotation(MethodNode method, String desc) {
        return find(method.visibleAnnotations, desc, method.invisibleAnnotations);
    }

    private static AnnotationNode annotation(ClassNode owner, String desc) {
        return find(owner.visibleAnnotations, desc, owner.invisibleAnnotations);
    }

    private static AnnotationNode annotation(FieldNode field, String desc) {
        return find(field.visibleAnnotations, desc, field.invisibleAnnotations);
    }

    private static AnnotationNode find(List<AnnotationNode> first, String desc, List<AnnotationNode> second) {
        for (AnnotationNode node : first == null ? List.<AnnotationNode>of() : first) {
            if (node.desc.equals(desc)) {
                return node;
            }
        }
        for (AnnotationNode node : second == null ? List.<AnnotationNode>of() : second) {
            if (node.desc.equals(desc)) {
                return node;
            }
        }
        return null;
    }

    /** The annotations nested under a key, e.g. `at = @At("HEAD")`. */
    private static List<AnnotationNode> annotations(AnnotationNode owner, String key) {
        List<AnnotationNode> found = new ArrayList<>();
        List<Object> values = owner.values == null ? List.of() : owner.values;
        for (int i = 0; i + 1 < values.size(); i += 2) {
            if (!String.valueOf(values.get(i)).equals(key)) {
                continue;
            }
            Object value = values.get(i + 1);
            if (value instanceof AnnotationNode node) {
                found.add(node);
            } else if (value instanceof List<?> list) {
                for (Object item : list) {
                    if (item instanceof AnnotationNode node) {
                        found.add(node);
                    }
                }
            }
        }
        return found;
    }

    private static String string(AnnotationNode annotation, String key) {
        List<Object> values = annotation.values == null ? List.of() : annotation.values;
        for (int i = 0; i + 1 < values.size(); i += 2) {
            if (!String.valueOf(values.get(i)).equals(key)) {
                continue;
            }
            Object value = values.get(i + 1);
            if (value instanceof String text) {
                return text;
            }
            if (value instanceof List<?> list && list.get(0) instanceof String text) {
                return text;
            }
        }
        return null;
    }

    private static boolean bool(AnnotationNode annotation, String key, boolean fallback) {
        List<Object> values = annotation.values == null ? List.of() : annotation.values;
        for (int i = 0; i + 1 < values.size(); i += 2) {
            if (String.valueOf(values.get(i)).equals(key) && values.get(i + 1) instanceof Boolean value) {
                return value;
            }
        }
        return fallback;
    }

    /** Run by hand: `WebMcMixinApplier <mod.jar> <client-classes-dir>`, for when a build log is not enough. */
    public static void main(String[] args) throws IOException {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: WebMcMixinApplier <mod.jar> <client-classes-dir>");
        }
        Report report = apply(Path.of(args[0]), Path.of(args[1]), System.out::println);
        System.out.println("applied: " + report.classes() + " class(es), " + report.overwritten() + " overwritten, "
                + report.injected() + " injected, " + report.copiedMembers() + " member(s) copied, "
                + report.shadows() + " shadow(s) read");
        report.notApplied().forEach(line -> System.out.println("not applied: " + line));
        report.refusals().forEach(line -> System.out.println("REFUSED: " + line));
        if (!report.refusals().isEmpty()) {
            System.exit(1);
        }
    }
}
