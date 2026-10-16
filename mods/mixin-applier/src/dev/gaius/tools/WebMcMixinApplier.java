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
import org.objectweb.asm.tree.FieldInsnNode;
import org.objectweb.asm.tree.InsnNode;
import org.objectweb.asm.tree.JumpInsnNode;
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
    private static final String REDIRECT = "Lorg/spongepowered/asm/mixin/injection/Redirect;";
    private static final String MODIFY_ARG = "Lorg/spongepowered/asm/mixin/injection/ModifyArg;";
    private static final String MODIFY_VARIABLE = "Lorg/spongepowered/asm/mixin/injection/ModifyVariable;";
    private static final String MODIFY_CONSTANT = "Lorg/spongepowered/asm/mixin/injection/ModifyConstant;";
    private static final String MODIFY_ARGS = "Lorg/spongepowered/asm/mixin/injection/ModifyArgs;";
    private static final String ARGS = "org/spongepowered/asm/mixin/injection/invoke/arg/Args";
    private static final String ARGS_CTOR = "([Ljava/lang/Object;)V";
    private static final String WRAP_OPERATION = "Lcom/llamalad7/mixinextras/injector/WrapOperation;";
    private static final String WRAP_METHOD = "Lcom/llamalad7/mixinextras/injector/wrapmethod/WrapMethod;";
    /** What a wrapped method's original body is renamed to, so the Operation has something to call. */
    private static final String ORIGINAL_PREFIX = "webmc$original$";
    private static final String OPERATION = "com/llamalad7/mixinextras/injector/wrapoperation/Operation";
    private static final String OPERATION_DESC = "L" + OPERATION + ";";
    private static final String OPERATION_CALL = "([Ljava/lang/Object;)Ljava/lang/Object;";
    private static final String ACCESSOR = "Lorg/spongepowered/asm/mixin/gen/Accessor;";
    private static final String INVOKER = "Lorg/spongepowered/asm/mixin/gen/Invoker;";
    private static final String MODIFY_EXPRESSION = "Lcom/llamalad7/mixinextras/injector/ModifyExpressionValue;";
    private static final String MODIFY_RETURN = "Lcom/llamalad7/mixinextras/injector/ModifyReturnValue;";
    private static final String WRAP_CONDITION = "Lcom/llamalad7/mixinextras/injector/WrapWithCondition;";
    private static final String MODIFY_RECEIVER = "Lcom/llamalad7/mixinextras/injector/ModifyReceiver;";
    private static final String CALLBACK_CTOR = "(Ljava/lang/String;Z)V";
    /** Annotations that mean nothing to a bake: reading them as "unimplemented injection" would be wrong. */
    private static final java.util.Set<String> IGNORED = java.util.Set.of(
            "Ljava/lang/Deprecated;", "Ljava/lang/SuppressWarnings;", "Ljava/lang/Override;",
            "Ljava/lang/SafeVarargs;", "Ljava/lang/FunctionalInterface;",
            "Ljavax/annotation/Nullable;", "Ljavax/annotation/Nonnull;", "Lorg/jetbrains/annotations/Nullable;",
            "Lorg/jetbrains/annotations/NotNull;", "Lorg/jetbrains/annotations/ApiStatus$Internal;",
            "Lorg/spongepowered/asm/mixin/Debug;");

    /** What one mod's mixins did, so a build log can be specific instead of optimistic. */
    public record Report(int classes, int overwritten, int injected, int redirected, int modified, int generated,
                         int copiedMembers, int shadows, List<String> notApplied, List<String> refusals) {
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
        int redirected = 0;
        int modified = 0;
        int generated = 0;
        int copied = 0;
        int shadows = 0;

        // An access widener is a mod asking for permission to reach into the client: apply it first, so the
        // classes the mixins touch already have the access the mod's own code needs.
        try (ZipFile archive = new ZipFile(modJar.toFile())) {
            applyAccessWidener(archive, classesRoot, log, notApplied);
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
                    List<ClassNode> helpers = new ArrayList<>();
                    Outcome outcome = merge(mixinClass, clientClass, log, helpers);
                    overwritten += outcome.overwritten();
                    injected += outcome.injected();
                    redirected += outcome.redirected();
                    modified += outcome.modified();
                    generated += outcome.generated();
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
                        // A generated Operation lives in its own class file: the client has to carry it too.
                        for (ClassNode helper : helpers) {
                            ClassWriter helperWriter = new ClassWriter(ClassWriter.COMPUTE_FRAMES
                                    | ClassWriter.COMPUTE_MAXS) {
                                @Override
                                protected String getCommonSuperClass(String type1, String type2) {
                                    return "java/lang/Object";
                                }
                            };
                            helper.accept(helperWriter);
                            Files.write(classesRoot.resolve(helper.name + ".class"), helperWriter.toByteArray());
                        }
                        touched.add(target);
                    }
                }
            }
        }
        return new Report(touched.size(), overwritten, injected, redirected, modified, generated, copied, shadows,
                notApplied, refusals);
    }

    private record Outcome(int overwritten, int injected, int redirected, int modified, int generated, int copied,
                           int shadows, List<String> notApplied, List<String> refusals) {
        boolean changed() {
            return overwritten + injected + redirected + modified + generated + copied > 0;
        }
    }

    private static Outcome merge(ClassNode mixin, ClassNode target, Consumer<String> log) {
        return merge(mixin, target, log, new ArrayList<>());
    }

    /** `generatedClasses` receives the helper classes a @WrapOperation needs; the caller writes them. */
    private static Outcome merge(ClassNode mixin, ClassNode target, Consumer<String> log,
                                 List<ClassNode> generatedClasses) {
        int overwritten = 0;
        int injected = 0;
        int redirected = 0;
        int modified = 0;
        int generated = 0;
        int copied = 0;
        int shadows = 0;
        List<String> notApplied = new ArrayList<>();
        List<String> refusals = new ArrayList<>();
        String from = mixin.name;
        String to = target.name;

        // An @Accessor/@Invoker mixin is an interface with no bodies: its members are *generated* into
        // the target, because an accessor's whole meaning is "read this field for me".
        boolean accessorMixin = annotation(mixin, ACCESSOR) != null || annotation(mixin, INVOKER) != null;

        for (MethodNode method : new ArrayList<>(mixin.methods)) {
            if (annotation(method, SHADOW) != null && !accessorMixin) {
                shadows++;
                continue; // a shadow is a promise about the target, never a copy
            }
            if (method.name.equals("<init>") || method.name.equals("<clinit>")) {
                continue; // a mixin's own constructors are how the mixin is built, not something it contributes
            }
            String where = to.replace('/', '.') + "#" + method.name;
            AnnotationNode overwrite = annotation(method, OVERWRITE);
            AnnotationNode inject = annotation(method, INJECT);
            AnnotationNode redirect = annotation(method, REDIRECT);
            AnnotationNode modifyArg = annotation(method, MODIFY_ARG);
            AnnotationNode modifyVariable = annotation(method, MODIFY_VARIABLE);
            AnnotationNode modifyConstant = annotation(method, MODIFY_CONSTANT);
            AnnotationNode modifyExpression = annotation(method, MODIFY_EXPRESSION);
            AnnotationNode modifyReturn = annotation(method, MODIFY_RETURN);
            AnnotationNode wrapCondition = annotation(method, WRAP_CONDITION);
            AnnotationNode modifyReceiver = annotation(method, MODIFY_RECEIVER);
            AnnotationNode modifyArgs = annotation(method, MODIFY_ARGS);
            AnnotationNode wrapOperation = annotation(method, WRAP_OPERATION);
            AnnotationNode wrapMethod = annotation(method, WRAP_METHOD);
            AnnotationNode accessor = annotation(method, ACCESSOR);
            AnnotationNode invoker = annotation(method, INVOKER);

            if (accessor != null || invoker != null || accessorMixin) {
                String problem = generate(target, method, accessor, invoker, to);
                if (problem == null) {
                    generated++;
                } else {
                    notApplied.add(where + ": " + problem);
                }
                continue;
            }
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
                injected += inject(victim, method, inject, to, notApplied, where + " -> " + name);
                if (!addIfAbsent(target, cloneMethod(method, from, to, false))) {
                    refusals.add(where + ": a method of that name and descriptor is already in the target");
                }
                continue;
            }
            if (redirect != null) {
                redirected += rewrite(target, from, method, redirect, Rewrite.REDIRECT, notApplied, where);
                continue;
            }
            if (modifyArg != null) {
                modified += rewrite(target, from, method, modifyArg, Rewrite.MODIFY_ARG, notApplied, where);
                continue;
            }
            if (modifyReceiver != null) {
                modified += rewrite(target, from, method, modifyReceiver, Rewrite.MODIFY_RECEIVER, notApplied, where);
                continue;
            }
            if (wrapCondition != null) {
                modified += rewrite(target, from, method, wrapCondition, Rewrite.WRAP_CONDITION, notApplied, where);
                continue;
            }
            if (modifyExpression != null) {
                modified += consumeResult(target, from, method, modifyExpression, notApplied, where);
                continue;
            }
            if (modifyReturn != null) {
                modified += consumeResult(target, from, method, modifyReturn, notApplied, where);
                continue;
            }
            if (modifyConstant != null) {
                modified += modifyConstant(target, from, method, modifyConstant, notApplied, where);
                continue;
            }
            if (modifyVariable != null) {
                modified += modifyVariable(target, from, method, modifyVariable, notApplied, where);
                continue;
            }
            if (modifyArgs != null) {
                modified += modifyArgs(target, from, method, modifyArgs, notApplied, where);
                continue;
            }
            if (wrapOperation != null) {
                modified += wrapOperation(target, from, method, wrapOperation, generatedClasses, notApplied, where);
                continue;
            }
            if (wrapMethod != null) {
                modified += wrapMethod(target, from, method, wrapMethod, generatedClasses, notApplied, where);
                continue;
            }
            if (hasOnlyIgnorableAnnotations(method)) {
                if (addIfAbsent(target, cloneMethod(method, from, to, false))) {
                    copied++;
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
            if (annotation(field, SHADOW) != null && !accessorMixin) {
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
                + " overwritten, " + injected + " injected, " + redirected + " redirected, " + modified
                + " modified, " + generated + " generated, " + copied + " member(s) copied, " + shadows
                + " shadow(s) read");
        return new Outcome(overwritten, injected, redirected, modified, generated, copied, shadows, notApplied,
                refusals);
    }

    /** @Deprecated and friends are not injection annotations: a mixin carrying one is still a mixin. */
    private static boolean hasOnlyIgnorableAnnotations(MethodNode method) {
        for (List<AnnotationNode> list : java.util.Arrays.asList(method.visibleAnnotations, method.invisibleAnnotations)) {
            for (AnnotationNode node : list == null ? List.<AnnotationNode>of() : list) {
                if (!IGNORED.contains(node.desc)) {
                    return false;
                }
            }
        }
        return true;
    }

    /** The target method an annotation names, or null with a refusal recorded. */
    private static MethodNode target(ClassNode owner, MethodNode handler, AnnotationNode annotation,
                                     List<String> refusals, String where) {
        String name = named(handler, annotation);
        MethodNode victim = find(owner, name, null);
        if (victim == null) {
            refusals.add(where + ": no method " + name + " in this client to modify");
        }
        return victim;
    }

    // ------------------------------------------------------------------ injection points

    /** Where an @At points, resolved against a real method. */
    private record Point(String kind, String owner, String name, String desc, int ordinal, String shift,
                         String problem) {
    }

    private static Point point(AnnotationNode at) {
        String kind = string(at, "value");
        if (kind == null) {
            kind = "INVOKE";
        }
        String target = string(at, "target");
        Integer ordinal = integer(at, "ordinal");
        String shift = string(at, "shift");
        int index = ordinal == null ? 0 : ordinal;
        if (kind.equals("INVOKE_ASSIGN")) {
            // Mixin's "after the call returned": the same target list as INVOKE, resolved the same
            // way, and the injection goes after the instruction instead of before it.
            kind = "INVOKE";
        }
        if (kind.equals("HEAD") || kind.equals("RETURN") || kind.equals("TAIL") || kind.equals("LOAD")
                || kind.equals("STORE")) {
            return new Point(kind, null, null, null, index, shift, null);
        }
        if (kind.equals("CONSTANT")) {
            return new Point(kind, null, target, null, index, shift, null);
        }
        if (!kind.equals("INVOKE") && !kind.equals("FIELD") && !kind.equals("NEW")) {
            return new Point(kind, null, null, null, index, shift,
                    "@At(\"" + kind + "\") is not a selector this applier implements");
        }
        if (target == null || target.isBlank()) {
            return new Point(kind, null, null, null, index, shift,
                    "@At(\"" + kind + "\") has no target, so there is nothing to match");
        }
        String owner;
        String name;
        String desc = null;
        if (kind.equals("NEW")) {
            owner = stripOwner(target);
            name = null;
        } else if (kind.equals("INVOKE")) {
            int paren = target.indexOf('(');
            if (paren < 0) {
                return new Point(kind, null, null, null, index, shift,
                        "an INVOKE target is owner.name(descriptor), got " + target);
            }
            String head = target.substring(0, paren);
            desc = target.substring(paren);
            int cut = head.indexOf(';') >= 0 ? head.indexOf(';') + 1 : head.lastIndexOf('.') + 1;
            owner = stripOwner(head.substring(0, cut));
            name = head.substring(cut);
        } else {
            int colon = target.indexOf(':');
            String head = colon < 0 ? target : target.substring(0, colon);
            desc = colon < 0 ? null : target.substring(colon + 1);
            int cut = head.indexOf(';') >= 0 ? head.indexOf(';') + 1 : head.lastIndexOf('.') + 1;
            owner = stripOwner(head.substring(0, cut));
            name = head.substring(cut);
        }
        return new Point(kind, owner, name, desc, index, shift, null);
    }

    private static String stripOwner(String text) {
        String value = text.trim();
        if (value.startsWith("L")) {
            value = value.substring(1);
        }
        if (value.endsWith(";")) {
            value = value.substring(0, value.length() - 1);
        }
        return value;
    }

    private static boolean matches(String pattern, String value) {
        if (pattern == null) {
            return true;
        }
        if (pattern.endsWith("*")) {
            return value.startsWith(pattern.substring(0, pattern.length() - 1));
        }
        return pattern.equals(value);
    }

    /** The instruction a point selects, or nothing with the reason recorded. */
    private static List<AbstractInsnNode> locate(MethodNode victim, Point point, List<String> notApplied,
                                                String where) {
        List<AbstractInsnNode> found = new ArrayList<>();
        for (AbstractInsnNode node = victim.instructions.getFirst(); node != null; node = node.getNext()) {
            if (point.kind().equals("INVOKE") && node instanceof MethodInsnNode call) {
                if (matches(point.owner(), call.owner) && matches(point.name(), call.name)
                        && matches(point.desc(), call.desc)) {
                    found.add(node);
                }
            } else if (point.kind().equals("FIELD") && node instanceof FieldInsnNode field) {
                if (matches(point.owner(), field.owner) && matches(point.name(), field.name)
                        && matches(point.desc(), field.desc)) {
                    found.add(node);
                }
            } else if (point.kind().equals("NEW") && node instanceof TypeInsnNode type
                    && type.getOpcode() == Opcodes.NEW) {
                if (matches(point.owner(), type.desc)) {
                    found.add(node);
                }
            } else if (point.kind().equals("CONSTANT") && isConstant(node)) {
                if (point.name() == null || constantMatches(node, point.name())) {
                    found.add(node);
                }
            }
        }
        if (point.ordinal() >= found.size()) {
            notApplied.add(where + ": @At(" + describe(point) + ") matched " + found.size()
                    + " place(s), and the injection asks for ordinal " + point.ordinal());
            return List.of();
        }
        return List.of(found.get(point.ordinal()));
    }

    private static String describe(Point point) {
        StringBuilder text = new StringBuilder(point.kind());
        if (point.name() != null) {
            text.append(' ').append(point.name());
        }
        if (point.desc() != null) {
            text.append(point.desc());
        }
        if (point.ordinal() != 0) {
            text.append(" ordinal=").append(point.ordinal());
        }
        return text.toString();
    }

    private static boolean isConstant(AbstractInsnNode node) {
        if (node instanceof LdcInsnNode) {
            return true;
        }
        int opcode = node.getOpcode();
        return opcode >= Opcodes.ICONST_M1 && opcode <= Opcodes.ICONST_5 || opcode == Opcodes.BIPUSH
                || opcode == Opcodes.SIPUSH || opcode == Opcodes.LCONST_0 || opcode == Opcodes.LCONST_1
                || opcode == Opcodes.FCONST_0 || opcode == Opcodes.FCONST_1 || opcode == Opcodes.FCONST_2
                || opcode == Opcodes.DCONST_0 || opcode == Opcodes.DCONST_1 || opcode == Opcodes.ACONST_NULL;
    }

    private static boolean constantMatches(AbstractInsnNode node, String wanted) {
        String text = String.valueOf(wanted).replace("\"", "");
        if (node instanceof LdcInsnNode ldc) {
            return String.valueOf(ldc.cst).equals(text);
        }
        try {
            double value = Double.parseDouble(text);
            if (node.getOpcode() >= Opcodes.ICONST_M1 && node.getOpcode() <= Opcodes.ICONST_5) {
                return node.getOpcode() - Opcodes.ICONST_0 == value;
            }
            return false;
        } catch (NumberFormatException error) {
            return false;
        }
    }

    // ------------------------------------------------------------------ the handler's own shape

    /** How a handler wants to be called: which arguments it reads, and whether it takes the target
     *  instance, the return value and a callback. */
    private record Plan(boolean mixinInstance, boolean targetInstance, List<Integer> slots, List<Type> types,
                        boolean takesReturn, String callback, boolean cancellable, String problem) {
    }

    private static Plan plan(MethodNode handler, MethodNode victim, String victimName, boolean atReturn,
                             boolean cancellable) {
        Type[] parameters = Type.getArgumentTypes(handler.desc);
        int callbackIndex = -1;
        for (int i = 0; i < parameters.length; i++) {
            String name = parameters[i].getSort() == Type.OBJECT ? parameters[i].getInternalName() : "";
            if (name.equals(CALLBACK_INFO) || name.equals(CALLBACK_RETURNABLE)) {
                callbackIndex = i;
            }
        }
        if (callbackIndex >= 0 && callbackIndex != parameters.length - 1) {
            return new Plan(false, false, List.of(), List.of(), false, null, cancellable,
                    "the callback is not the handler's last parameter");
        }
        int last = callbackIndex < 0 ? parameters.length : callbackIndex;
        List<Type> wanted = new ArrayList<>(java.util.Arrays.asList(parameters).subList(0, last));
        boolean mixinInstance = (handler.access & Opcodes.ACC_STATIC) == 0;
        boolean staticVictim = (victim.access & Opcodes.ACC_STATIC) != 0;

        boolean targetInstance = false;
        if (!wanted.isEmpty() && !staticVictim && wanted.get(0).getSort() == Type.OBJECT
                && wanted.get(0).getInternalName().equals(victimName)) {
            targetInstance = true;
            wanted.remove(0);
        }
        boolean takesReturn = false;
        Type returnType = Type.getReturnType(victim.desc);
        if (atReturn && returnType.getSort() != Type.VOID && !wanted.isEmpty() && wanted.get(0).equals(returnType)) {
            takesReturn = true;
            wanted.remove(0);
        }
        Type[] arguments = Type.getArgumentTypes(victim.desc);
        int slot = staticVictim ? 0 : 1;
        List<Integer> slots = new ArrayList<>();
        List<Type> types = new ArrayList<>();
        for (int i = 0; i < wanted.size(); i++) {
            if (i >= arguments.length || !wanted.get(i).equals(arguments[i])) {
                return new Plan(mixinInstance, targetInstance, List.of(), List.of(), takesReturn, null, cancellable,
                        "the handler's parameters (" + String.join(", ",
                                wanted.stream().map(Type::getClassName).toList()) + ") do not line up with the target's ("
                                + String.join(", ", java.util.Arrays.stream(arguments).map(Type::getClassName).toList())
                                + ")");
            }
            slots.add(slot);
            types.add(arguments[i]);
            slot += arguments[i].getSize();
        }
        String callback = callbackIndex < 0 ? null
                : (parameters[callbackIndex].getInternalName().equals(CALLBACK_RETURNABLE)
                        ? CALLBACK_RETURNABLE : CALLBACK_INFO);
        return new Plan(mixinInstance, targetInstance, slots, types, takesReturn, callback, cancellable, null);
    }

    private static int loadOpcode(Type type) {
        return switch (type.getSort()) {
            case Type.BOOLEAN, Type.BYTE, Type.CHAR, Type.SHORT, Type.INT -> Opcodes.ILOAD;
            case Type.LONG -> Opcodes.LLOAD;
            case Type.FLOAT -> Opcodes.FLOAD;
            case Type.DOUBLE -> Opcodes.DLOAD;
            default -> Opcodes.ALOAD;
        };
    }

    private static int storeOpcode(Type type) {
        return switch (type.getSort()) {
            case Type.BOOLEAN, Type.BYTE, Type.CHAR, Type.SHORT, Type.INT -> Opcodes.ISTORE;
            case Type.LONG -> Opcodes.LSTORE;
            case Type.FLOAT -> Opcodes.FSTORE;
            case Type.DOUBLE -> Opcodes.DSTORE;
            default -> Opcodes.ASTORE;
        };
    }

    private static int returnOpcode(Type type) {
        return switch (type.getSort()) {
            case Type.VOID -> Opcodes.RETURN;
            case Type.BOOLEAN, Type.BYTE, Type.CHAR, Type.SHORT, Type.INT -> Opcodes.IRETURN;
            case Type.LONG -> Opcodes.LRETURN;
            case Type.FLOAT -> Opcodes.FRETURN;
            case Type.DOUBLE -> Opcodes.DRETURN;
            default -> Opcodes.ARETURN;
        };
    }

    private static int popOpcode(Type type) {
        return switch (type.getSort()) {
            case Type.LONG, Type.DOUBLE -> Opcodes.POP2;
            default -> Opcodes.POP;
        };
    }

    /** The type of a value already on the stack, for a handler that has to be given it. */
    private static Type typeOfSort(int sort) {
        return switch (sort) {
            case Type.BOOLEAN, Type.BYTE, Type.CHAR, Type.SHORT, Type.INT -> Type.INT_TYPE;
            case Type.LONG -> Type.LONG_TYPE;
            case Type.FLOAT -> Type.FLOAT_TYPE;
            case Type.DOUBLE -> Type.DOUBLE_TYPE;
            default -> Type.getObjectType("java/lang/Object");
        };
    }

    private static int freshLocal(MethodNode victim, Type type) {
        int slot = victim.maxLocals;
        victim.maxLocals += Math.max(1, type.getSize());
        return slot;
    }

    /** Turn the Object a CallbackInfoReturnable carries into the target's own return type. */
    private static void emitCast(InsnList code, Type type) {
        switch (type.getSort()) {
            case Type.OBJECT -> code.add(new TypeInsnNode(Opcodes.CHECKCAST, type.getInternalName()));
            case Type.ARRAY -> code.add(new TypeInsnNode(Opcodes.CHECKCAST, type.getDescriptor()));
            case Type.BOOLEAN -> unbox(code, "java/lang/Boolean", "booleanValue", "()Z");
            case Type.BYTE -> unbox(code, "java/lang/Byte", "byteValue", "()B");
            case Type.CHAR -> unbox(code, "java/lang/Character", "charValue", "()C");
            case Type.SHORT -> unbox(code, "java/lang/Short", "shortValue", "()S");
            case Type.INT -> unbox(code, "java/lang/Integer", "intValue", "()I");
            case Type.LONG -> unbox(code, "java/lang/Long", "longValue", "()J");
            case Type.FLOAT -> unbox(code, "java/lang/Float", "floatValue", "()F");
            case Type.DOUBLE -> unbox(code, "java/lang/Double", "doubleValue", "()D");
            default -> { }
        }
    }

    private static void unbox(InsnList code, String owner, String name, String desc) {
        code.add(new TypeInsnNode(Opcodes.CHECKCAST, owner));
        code.add(new MethodInsnNode(Opcodes.INVOKEVIRTUAL, owner, name, desc, false));
    }

    /**
     * Call a handler with the arguments it asked for.
     *
     * <p>Reading the target's own arguments out of its local slots is exact at an injection point, which is
     * why a handler that takes them is a normal case here rather than a refusal. A cancellable injection then
     * gets the early-return check, which is the whole reason `cancel()` exists.</p>
     */
    private static InsnList handlerCall(MethodNode victim, MethodNode handler, Plan plan, String targetName,
                                        List<String> problems, String where, boolean cancellable) {
        if (plan.problem() != null) {
            problems.add(where + ": " + plan.problem());
            return null;
        }
        InsnList code = new InsnList();
        Type returnType = Type.getReturnType(victim.desc);
        Integer returnSlot = null;
        if (plan.takesReturn()) {
            returnSlot = freshLocal(victim, returnType);
            code.add(new VarInsnNode(storeOpcode(returnType), returnSlot));
        }
        if (plan.mixinInstance()) {
            code.add(new VarInsnNode(Opcodes.ALOAD, 0));
        }
        if (plan.targetInstance()) {
            code.add(new VarInsnNode(Opcodes.ALOAD, 0));
        }
        for (int i = 0; i < plan.slots().size(); i++) {
            code.add(new VarInsnNode(loadOpcode(plan.types().get(i)), plan.slots().get(i)));
        }
        if (plan.takesReturn()) {
            code.add(new VarInsnNode(loadOpcode(returnType), returnSlot));
        }
        Integer callbackSlot = null;
        if (plan.callback() != null) {
            callbackSlot = freshLocal(victim, Type.getObjectType(plan.callback()));
            code.add(new TypeInsnNode(Opcodes.NEW, plan.callback()));
            code.add(new InsnNode(Opcodes.DUP));
            code.add(new LdcInsnNode(victim.name));
            code.add(new InsnNode(plan.cancellable() ? Opcodes.ICONST_1 : Opcodes.ICONST_0));
            code.add(new MethodInsnNode(Opcodes.INVOKESPECIAL, plan.callback(), "<init>", CALLBACK_CTOR, false));
            code.add(new VarInsnNode(Opcodes.ASTORE, callbackSlot));
            code.add(new VarInsnNode(Opcodes.ALOAD, callbackSlot));
        }
        code.add(new MethodInsnNode(plan.mixinInstance() ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC, targetName,
                handler.name, handler.desc, false));
        Type handlerReturn = Type.getReturnType(handler.desc);
        if (!handlerReturn.equals(Type.VOID_TYPE)) {
            code.add(new InsnNode(popOpcode(handlerReturn)));
        }
        if (cancellable && callbackSlot != null) {
            LabelNode done = new LabelNode();
            code.add(new VarInsnNode(Opcodes.ALOAD, callbackSlot));
            code.add(new MethodInsnNode(Opcodes.INVOKEVIRTUAL, CALLBACK_INFO, "isCancelled", "()Z", false));
            code.add(new JumpInsnNode(Opcodes.IFEQ, done));
            if (returnType.equals(Type.VOID_TYPE)) {
                code.add(new InsnNode(Opcodes.RETURN));
            } else if (plan.callback().equals(CALLBACK_RETURNABLE)) {
                code.add(new VarInsnNode(Opcodes.ALOAD, callbackSlot));
                code.add(new MethodInsnNode(Opcodes.INVOKEVIRTUAL, CALLBACK_RETURNABLE, "getReturnValue",
                        "()Ljava/lang/Object;", false));
                emitCast(code, returnType);
                code.add(new InsnNode(returnOpcode(returnType)));
            }
            code.add(done);
        }
        if (plan.takesReturn() && returnSlot != null) {
            // The exit's own value was put aside for the handler, and the exit instruction that follows
            // still owes the JVM a value: put it back.
            code.add(new VarInsnNode(loadOpcode(returnType), returnSlot));
        }
        return code;
    }

    /** @Inject, at every point it names. Returns how many calls were planted. */
    private static int inject(MethodNode victim, MethodNode handler, AnnotationNode inject, String targetName,
                              List<String> notApplied, String where) {
        boolean cancellable = bool(inject, "cancellable", false);
        List<AnnotationNode> ats = annotations(inject, "at");
        if (ats.isEmpty()) {
            ats = List.of(new AnnotationNode("Lorg/spongepowered/asm/mixin/injection/At;"));
        }
        int planted = 0;
        for (AnnotationNode at : ats) {
            Point point = point(at);
            if (point.problem() != null) {
                notApplied.add(where + ": " + point.problem());
                continue;
            }
            if (point.shift() != null && !point.shift().equals("BEFORE")) {
                notApplied.add(where + ": @At(" + describe(point) + ") shift=" + point.shift()
                        + " is not applied (BEFORE is the only shift)");
                continue;
            }
            if (point.kind().equals("HEAD")) {
                InsnList code = handlerCall(victim, handler, plan(handler, victim, "", false, cancellable),
                        targetName, notApplied, where, cancellable);
                if (code != null) {
                    victim.instructions.insert(code);
                    planted++;
                }
                continue;
            }
            if (point.kind().equals("RETURN") || point.kind().equals("TAIL")) {
                List<AbstractInsnNode> exits = new ArrayList<>();
                for (AbstractInsnNode node = victim.instructions.getFirst(); node != null; node = node.getNext()) {
                    int opcode = node.getOpcode();
                    if (opcode >= Opcodes.IRETURN && opcode <= Opcodes.RETURN) {
                        exits.add(node);
                    }
                }
                if (exits.isEmpty()) {
                    notApplied.add(where + ": " + point.kind() + " was asked for and this method has no exits");
                    continue;
                }
                List<AbstractInsnNode> chosen = point.kind().equals("TAIL")
                        ? List.of(exits.get(exits.size() - 1)) : exits;
                for (AbstractInsnNode exit : chosen) {
                    InsnList code = handlerCall(victim, handler, plan(handler, victim, "", true, cancellable),
                            targetName, notApplied, where, cancellable);
                    if (code != null) {
                        victim.instructions.insertBefore(exit, code);
                        planted++;
                    }
                }
                continue;
            }
            for (AbstractInsnNode node : locate(victim, point, notApplied, where)) {
                InsnList code = handlerCall(victim, handler, plan(handler, victim, "", false, cancellable),
                        targetName, notApplied, where, cancellable);
                if (code != null) {
                    victim.instructions.insertBefore(node, code);
                    planted++;
                }
            }
        }
        return planted;
    }

    // ------------------------------------------------------------------ rewriting a call

    private enum Rewrite { REDIRECT, MODIFY_ARG, MODIFY_RECEIVER, WRAP_CONDITION }

    /** @Redirect, @ModifyArg, @ModifyReceiver and @WrapWithCondition: all four rewrite one call. */
    private static int rewrite(ClassNode target, String from, MethodNode handler, AnnotationNode annotation,
                               Rewrite kind, List<String> notApplied, String where) {
        List<AnnotationNode> ats = annotations(annotation, "at");
        Point point = point(ats.isEmpty() ? new AnnotationNode("Lorg/spongepowered/asm/mixin/injection/At;") : ats.get(0));
        if (point.problem() != null) {
            notApplied.add(where + ": " + point.problem());
            return 0;
        }
        if (point.kind().equals("NEW")) {
            notApplied.add(where + ": a constructor is not rewritten (NEW is not applied)");
            return 0;
        }
        if (!point.kind().equals("INVOKE") && !point.kind().equals("FIELD")) {
            notApplied.add(where + ": " + kind + " at " + describe(point) + " is not applied (INVOKE and FIELD are)");
            return 0;
        }
        String name = named(handler, annotation);
        MethodNode victim = find(target, name, null);
        if (victim == null) {
            notApplied.add(where + ": no method " + name + " in this client to modify");
            return 0;
        }
        Integer index = integer(annotation, "index");
        boolean mixinInstance = (handler.access & Opcodes.ACC_STATIC) == 0;
        Type[] parameters = Type.getArgumentTypes(handler.desc);
        int done = 0;
        for (AbstractInsnNode node : locate(victim, point, notApplied, where)) {
            List<Type> stack = new ArrayList<>();
            int receiverCount = 0;
            if (node instanceof MethodInsnNode call) {
                if (call.getOpcode() != Opcodes.INVOKESTATIC) {
                    stack.add(Type.getObjectType(call.owner));
                    receiverCount = 1;
                }
                stack.addAll(java.util.Arrays.asList(Type.getArgumentTypes(call.desc)));
            } else {
                FieldInsnNode field = (FieldInsnNode) node;
                boolean isStatic = field.getOpcode() == Opcodes.GETSTATIC || field.getOpcode() == Opcodes.PUTSTATIC;
                if (!isStatic) {
                    stack.add(Type.getObjectType(field.owner));
                    receiverCount = 1;
                }
                if (field.getOpcode() == Opcodes.PUTFIELD || field.getOpcode() == Opcodes.PUTSTATIC) {
                    stack.add(Type.getType(field.desc));
                }
            }
            // What the handler is given: the target instance (if it asks for it), then the call's own stack.
            int skip = 0;
            boolean targetInstance = parameters.length > 0 && parameters[0].getSort() == Type.OBJECT
                    && parameters[0].getInternalName().equals(target.name);
            if (targetInstance) {
                skip = 1;
            }
            List<Type> given = new ArrayList<>(java.util.Arrays.asList(parameters).subList(skip, parameters.length));
            if (!given.equals(stack)) {
                notApplied.add(where + ": the handler's parameters (" + String.join(", ",
                        given.stream().map(Type::getClassName).toList()) + ") do not line up with the call ("
                        + String.join(", ", stack.stream().map(Type::getClassName).toList()) + ")");
                continue;
            }
            InsnList code = new InsnList();
            List<Integer> slots = new ArrayList<>();
            for (int i = stack.size() - 1; i >= 0; i--) {
                int slot = freshLocal(victim, stack.get(i));
                code.add(new VarInsnNode(storeOpcode(stack.get(i)), slot));
                slots.add(0, slot);
            }
            if (kind == Rewrite.MODIFY_ARG) {
                // Mixin's index counts the call's own arguments, and the stack holds the receiver first.
                int position = index == null ? stack.size() - 1 : receiverCount + index;
                if (position < 0 || position >= stack.size()) {
                    notApplied.add(where + ": argument " + index + " does not exist on the call it names");
                    continue;
                }
                for (int i = 0; i < stack.size(); i++) {
                    if (i == position) {
                        if (mixinInstance) {
                            code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                        }
                        code.add(new VarInsnNode(loadOpcode(stack.get(i)), slots.get(i)));
                        code.add(new MethodInsnNode(mixinInstance ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC,
                                target.name, handler.name, handler.desc, false));
                    } else {
                        code.add(new VarInsnNode(loadOpcode(stack.get(i)), slots.get(i)));
                    }
                }
                victim.instructions.insertBefore(node, code);
            } else if (kind == Rewrite.MODIFY_RECEIVER) {
                Type receiverType = stack.get(0);
                int replacement = freshLocal(victim, receiverType);
                if (mixinInstance) {
                    code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                }
                if (targetInstance) {
                    code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                }
                code.add(new VarInsnNode(loadOpcode(receiverType), slots.get(0)));
                code.add(new MethodInsnNode(mixinInstance ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC, target.name,
                        handler.name, handler.desc, false));
                code.add(new VarInsnNode(storeOpcode(receiverType), replacement));
                code.add(new VarInsnNode(loadOpcode(receiverType), replacement));
                for (int i = 1; i < stack.size(); i++) {
                    code.add(new VarInsnNode(loadOpcode(stack.get(i)), slots.get(i)));
                }
                victim.instructions.insertBefore(node, code);
            } else if (kind == Rewrite.WRAP_CONDITION) {
                Type produced = node instanceof MethodInsnNode call ? Type.getReturnType(call.desc)
                        : Type.getType(((FieldInsnNode) node).desc);
                LabelNode doCall = new LabelNode();
                LabelNode after = new LabelNode();
                if (mixinInstance) {
                    code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                }
                if (targetInstance) {
                    code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                }
                for (int i = 0; i < stack.size(); i++) {
                    code.add(new VarInsnNode(loadOpcode(stack.get(i)), slots.get(i)));
                }
                code.add(new MethodInsnNode(mixinInstance ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC, target.name,
                        handler.name, handler.desc, false));
                code.add(new JumpInsnNode(Opcodes.IFNE, doCall));
                if (!produced.equals(Type.VOID_TYPE)) {
                    code.add(new InsnNode(produced.getSort() == Type.OBJECT || produced.getSort() == Type.ARRAY
                            ? Opcodes.ACONST_NULL : produced.getSort() == Type.LONG ? Opcodes.LCONST_0
                            : produced.getSort() == Type.DOUBLE ? Opcodes.DCONST_0
                            : produced.getSort() == Type.FLOAT ? Opcodes.FCONST_0 : Opcodes.ICONST_0));
                }
                code.add(new JumpInsnNode(Opcodes.GOTO, after));
                code.add(doCall);
                for (int i = 0; i < stack.size(); i++) {
                    code.add(new VarInsnNode(loadOpcode(stack.get(i)), slots.get(i)));
                }
                // The label has to land *after* the guarded call: a jump to a label that sits just
                // before it would run it with an empty stack.
                victim.instructions.insertBefore(node, code);
                victim.instructions.insert(node, after);
            } else {
                if (mixinInstance) {
                    code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                }
                if (targetInstance) {
                    code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                }
                for (int i = 0; i < stack.size(); i++) {
                    code.add(new VarInsnNode(loadOpcode(stack.get(i)), slots.get(i)));
                }
                code.add(new MethodInsnNode(mixinInstance ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC, target.name,
                        handler.name, handler.desc, false));
                victim.instructions.insertBefore(node, code);
                victim.instructions.remove(node);
            }
            done++;
        }
        addIfAbsent(target, cloneMethod(handler, from, target.name, false));
        return done;
    }

    /**
     * @ModifyArgs: the call's arguments are boxed into an {@code Args} the handler can read and replace.
     *
     * <p>The handler never sees the call itself, only its arguments - which is why this rebuilds the stack
     * around the call instead of replacing it: pop the arguments, hand them over boxed, then push back
     * whatever the handler left in the box.</p>
     */
    private static int modifyArgs(ClassNode target, String from, MethodNode handler, AnnotationNode annotation,
                                  List<String> notApplied, String where) {
        List<AnnotationNode> ats = annotations(annotation, "at");
        Point point = point(ats.isEmpty() ? new AnnotationNode("Lorg/spongepowered/asm/mixin/injection/At;") : ats.get(0));
        if (point.problem() != null) {
            notApplied.add(where + ": " + point.problem());
            return 0;
        }
        if (!point.kind().equals("INVOKE") && !point.kind().equals("FIELD")) {
            notApplied.add(where + ": @ModifyArgs at " + describe(point) + " is not applied (INVOKE and FIELD are)");
            return 0;
        }
        boolean hasArgs = false;
        for (Type parameter : Type.getArgumentTypes(handler.desc)) {
            if (parameter.getSort() == Type.OBJECT && parameter.getInternalName().equals(ARGS)) {
                hasArgs = true;
            }
        }
        if (!hasArgs) {
            notApplied.add(where + ": a @ModifyArgs handler has to take an Args parameter");
            return 0;
        }
        String name = named(handler, annotation);
        MethodNode victim = find(target, name, null);
        if (victim == null) {
            notApplied.add(where + ": no method " + name + " in this client to modify");
            return 0;
        }
        boolean mixinInstance = (handler.access & Opcodes.ACC_STATIC) == 0;
        Type[] parameters = Type.getArgumentTypes(handler.desc);
        boolean wantsTarget = parameters.length > 1 && parameters[0].getSort() == Type.OBJECT
                && parameters[0].getInternalName().equals(target.name);
        int done = 0;
        for (AbstractInsnNode node : locate(victim, point, notApplied, where)) {
            List<Type> stack = new ArrayList<>();
            boolean receiver;
            if (node instanceof MethodInsnNode call) {
                receiver = call.getOpcode() != Opcodes.INVOKESTATIC;
                if (receiver) {
                    stack.add(Type.getObjectType(call.owner));
                }
                stack.addAll(java.util.Arrays.asList(Type.getArgumentTypes(call.desc)));
            } else {
                FieldInsnNode field = (FieldInsnNode) node;
                boolean isStatic = field.getOpcode() == Opcodes.GETSTATIC || field.getOpcode() == Opcodes.PUTSTATIC;
                receiver = !isStatic;
                if (receiver) {
                    stack.add(Type.getObjectType(field.owner));
                }
                if (field.getOpcode() == Opcodes.PUTFIELD || field.getOpcode() == Opcodes.PUTSTATIC) {
                    stack.add(Type.getType(field.desc));
                }
            }
            int firstArgument = receiver ? 1 : 0;
            InsnList code = new InsnList();
            List<Integer> slots = new ArrayList<>();
            for (int i = stack.size() - 1; i >= 0; i--) {
                int slot = freshLocal(victim, stack.get(i));
                code.add(new VarInsnNode(storeOpcode(stack.get(i)), slot));
                slots.add(0, slot);
            }
            int count = stack.size() - firstArgument;
            code.add(new LdcInsnNode(count));
            code.add(new TypeInsnNode(Opcodes.ANEWARRAY, "java/lang/Object"));
            for (int i = 0; i < count; i++) {
                Type type = stack.get(firstArgument + i);
                code.add(new InsnNode(Opcodes.DUP));
                code.add(new LdcInsnNode(i));
                code.add(new VarInsnNode(loadOpcode(type), slots.get(firstArgument + i)));
                emitBox(code, type);
                code.add(new InsnNode(Opcodes.AASTORE));
            }
            int argsSlot = freshLocal(victim, Type.getObjectType(ARGS));
            code.add(new TypeInsnNode(Opcodes.NEW, ARGS));
            code.add(new InsnNode(Opcodes.DUP_X1));
            code.add(new InsnNode(Opcodes.SWAP));
            code.add(new MethodInsnNode(Opcodes.INVOKESPECIAL, ARGS, "<init>", ARGS_CTOR, false));
            code.add(new VarInsnNode(Opcodes.ASTORE, argsSlot));
            if (mixinInstance) {
                code.add(new VarInsnNode(Opcodes.ALOAD, 0));
            }
            if (wantsTarget) {
                code.add(new VarInsnNode(Opcodes.ALOAD, 0));
            }
            code.add(new VarInsnNode(Opcodes.ALOAD, argsSlot));
            code.add(new MethodInsnNode(mixinInstance ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC, target.name,
                    handler.name, handler.desc, false));
            if (receiver) {
                code.add(new VarInsnNode(loadOpcode(stack.get(0)), slots.get(0)));
            }
            for (int i = 0; i < count; i++) {
                Type type = stack.get(firstArgument + i);
                code.add(new VarInsnNode(Opcodes.ALOAD, argsSlot));
                code.add(new LdcInsnNode(i));
                code.add(new MethodInsnNode(Opcodes.INVOKEVIRTUAL, ARGS, "get", "(I)Ljava/lang/Object;", false));
                emitCast(code, type);
            }
            victim.instructions.insertBefore(node, code);
            done++;
        }
        addIfAbsent(target, cloneMethod(handler, from, target.name, false));
        return done;
    }

    /**
     * @WrapOperation: the handler replaces a call, and is handed an {@code Operation} that performs the
     * original one.
     *
     * <p>The Operation has to be generated: it is the only place that knows the owner, the name and the
     * descriptor of the call being wrapped. One small class per wrapped site - it unboxes what the handler
     * passes it, makes the call, and boxes the result - and then the handler itself is called with the
     * call's own arguments and that Operation.</p>
     */
    private static int wrapOperation(ClassNode target, String from, MethodNode handler, AnnotationNode annotation,
                                     List<ClassNode> generatedClasses, List<String> notApplied, String where) {
        List<AnnotationNode> ats = annotations(annotation, "at");
        Point point = point(ats.isEmpty() ? new AnnotationNode("Lorg/spongepowered/asm/mixin/injection/At;") : ats.get(0));
        if (point.problem() != null) {
            notApplied.add(where + ": " + point.problem());
            return 0;
        }
        if (!point.kind().equals("INVOKE") && !point.kind().equals("FIELD")) {
            notApplied.add(where + ": @WrapOperation at " + describe(point) + " is not applied (INVOKE and FIELD are)");
            return 0;
        }
        Type[] parameters = Type.getArgumentTypes(handler.desc);
        boolean hasOperation = parameters.length > 0 && parameters[parameters.length - 1].getSort() == Type.OBJECT
                && parameters[parameters.length - 1].getInternalName().equals(OPERATION);
        if (!hasOperation) {
            notApplied.add(where + ": a @WrapOperation handler has to take an Operation as its last parameter");
            return 0;
        }
        String name = named(handler, annotation);
        MethodNode victim = find(target, name, null);
        if (victim == null) {
            notApplied.add(where + ": no method " + name + " in this client to modify");
            return 0;
        }
        boolean mixinInstance = (handler.access & Opcodes.ACC_STATIC) == 0;
        boolean wantsTarget = parameters.length > 1 && parameters[0].getSort() == Type.OBJECT
                && parameters[0].getInternalName().equals(target.name);
        int done = 0;
        for (AbstractInsnNode node : locate(victim, point, notApplied, where)) {
            List<Type> stack = new ArrayList<>();
            boolean isStaticCall;
            if (node instanceof MethodInsnNode call) {
                isStaticCall = call.getOpcode() == Opcodes.INVOKESTATIC;
                if (!isStaticCall) {
                    stack.add(Type.getObjectType(call.owner));
                }
                stack.addAll(java.util.Arrays.asList(Type.getArgumentTypes(call.desc)));
            } else {
                FieldInsnNode field = (FieldInsnNode) node;
                isStaticCall = field.getOpcode() == Opcodes.GETSTATIC || field.getOpcode() == Opcodes.PUTSTATIC;
                if (!isStaticCall) {
                    stack.add(Type.getObjectType(field.owner));
                }
                if (field.getOpcode() == Opcodes.PUTFIELD || field.getOpcode() == Opcodes.PUTSTATIC) {
                    stack.add(Type.getType(field.desc));
                }
            }
            Type produced = node instanceof MethodInsnNode call ? Type.getReturnType(call.desc)
                    : (node instanceof FieldInsnNode field && (field.getOpcode() == Opcodes.PUTFIELD
                            || field.getOpcode() == Opcodes.PUTSTATIC) ? Type.VOID_TYPE
                            : Type.getType(((FieldInsnNode) node).desc));
            // One helper class: `call(Object[])` unboxes, performs the original operation, boxes the result.
            String helper = target.name + "$webmc$wrap$" + generatedClasses.size();
            generatedClasses.add(operationClass(helper, node, stack, produced, isStaticCall));
            InsnList code = new InsnList();
            if (mixinInstance) {
                code.add(new VarInsnNode(Opcodes.ALOAD, 0));
            }
            if (wantsTarget) {
                code.add(new VarInsnNode(Opcodes.ALOAD, 0));
            }
            code.add(new TypeInsnNode(Opcodes.NEW, helper));
            code.add(new InsnNode(Opcodes.DUP));
            code.add(new MethodInsnNode(Opcodes.INVOKESPECIAL, helper, "<init>", "()V", false));
            code.add(new MethodInsnNode(mixinInstance ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC, target.name,
                    handler.name, handler.desc, false));
            victim.instructions.insertBefore(node, code);
            victim.instructions.remove(node);
            done++;
        }
        addIfAbsent(target, cloneMethod(handler, from, target.name, false));
        return done;
    }

    /**
     * @WrapMethod: the method is renamed and the handler takes its place, with an Operation that calls
     * the renamed original.
     *
     * <p>This is the one shape that changes a method's *identity*: the body moves to
     * {@code webmc$original$<name>} (private), and the method itself becomes a call to the handler.
     * Everything that called it still calls it, the handler can forward, and nothing about the call
     * sites has to change - which is why it is worth doing properly rather than refusing.</p>
     */
    private static int wrapMethod(ClassNode target, String from, MethodNode handler, AnnotationNode annotation,
                                  List<ClassNode> generatedClasses, List<String> notApplied, String where) {
        String name = string(annotation, "method");
        if (name == null || name.isBlank()) {
            name = handler.name;
        }
        MethodNode victim = find(target, name, null);
        if (victim == null) {
            notApplied.add(where + ": no method " + name + " in this client to wrap");
            return 0;
        }
        if (victim.name.startsWith(ORIGINAL_PREFIX)) {
            notApplied.add(where + ": " + name + " is already a wrapped original");
            return 0;
        }
        Type[] parameters = Type.getArgumentTypes(handler.desc);
        boolean hasOperation = parameters.length > 0 && parameters[parameters.length - 1].getSort() == Type.OBJECT
                && parameters[parameters.length - 1].getInternalName().equals(OPERATION);
        if (!hasOperation) {
            notApplied.add(where + ": a @WrapMethod handler has to take an Operation as its last parameter");
            return 0;
        }
        Type victimReturn = Type.getReturnType(victim.desc);
        Type handlerReturn = Type.getReturnType(handler.desc);
        if (!victimReturn.equals(handlerReturn)) {
            notApplied.add(where + ": a @WrapMethod handler has to return what " + name + " returns ("
                    + victimReturn.getClassName() + "), not " + handlerReturn.getClassName());
            return 0;
        }
        boolean mixinInstance = (handler.access & Opcodes.ACC_STATIC) == 0;
        boolean staticVictim = (victim.access & Opcodes.ACC_STATIC) != 0;

        // 1. keep the original body under a private name
        String original = ORIGINAL_PREFIX + victim.name;
        if (find(target, original, victim.desc) != null) {
            notApplied.add(where + ": " + original + " already exists in the target");
            return 0;
        }
        MethodNode moved = cloneMethod(victim, target.name, target.name, false);
        moved.name = original;
        // Package-private, not private: the Operation that calls it is generated in the same package
        // (a separate class cannot reach a private member of another class, and widening the original
        // only as far as its own package keeps it out of the rest of the client).
        moved.access = (moved.access | Opcodes.ACC_SYNTHETIC)
                & ~(Opcodes.ACC_PUBLIC | Opcodes.ACC_PROTECTED | Opcodes.ACC_PRIVATE);
        target.methods.add(moved);

        // 2. an Operation that calls the renamed original
        MethodInsnNode call = new MethodInsnNode(staticVictim ? Opcodes.INVOKESTATIC : Opcodes.INVOKEVIRTUAL,
                target.name, original, victim.desc, false);
        String helper = target.name + "$webmc$wrapmethod$" + generatedClasses.size();
        generatedClasses.add(operationClassForMethod(helper, call, Type.getArgumentTypes(victim.desc), victimReturn,
                staticVictim));

        // 3. the method itself becomes the handler's call
        InsnList code = new InsnList();
        if (mixinInstance) {
            code.add(new VarInsnNode(Opcodes.ALOAD, 0));
        }
        boolean wantsTarget = !staticVictim && parameters.length > 1 && parameters[0].getSort() == Type.OBJECT
                && parameters[0].getInternalName().equals(target.name);
        if (wantsTarget) {
            code.add(new VarInsnNode(Opcodes.ALOAD, 0));
        }
        int slot = staticVictim ? 0 : 1;
        for (Type parameter : Type.getArgumentTypes(victim.desc)) {
            code.add(new VarInsnNode(loadOpcode(parameter), slot));
            slot += parameter.getSize();
        }
        code.add(new TypeInsnNode(Opcodes.NEW, helper));
        code.add(new InsnNode(Opcodes.DUP));
        if (!staticVictim) {
            code.add(new VarInsnNode(Opcodes.ALOAD, 0));
            code.add(new MethodInsnNode(Opcodes.INVOKESPECIAL, helper, "<init>", "(L" + target.name + ";)V", false));
        } else {
            code.add(new MethodInsnNode(Opcodes.INVOKESPECIAL, helper, "<init>", "()V", false));
        }
        code.add(new MethodInsnNode(mixinInstance ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC, target.name,
                handler.name, handler.desc, false));
        code.add(new InsnNode(returnOpcode(handlerReturn)));

        victim.instructions = code;
        victim.tryCatchBlocks = null;
        victim.localVariables = null;
        victim.maxStack = 0;
        victim.maxLocals = 0;
        addIfAbsent(target, cloneMethod(handler, from, target.name, false));
        return 1;
    }

    /**
     * An Operation for a wrapped method: `call(arguments...)` forwards to the original.
     *
     * <p>The receiver is *not* one of the arguments: a wrapped method is called on the instance the
     * handler runs on, so the helper holds that instance and `call` takes only the method's own
     * arguments - which is what a handler writing `original.call(x)` expects.</p>
     */
    private static ClassNode operationClassForMethod(String name, MethodInsnNode call, Type[] parameters, Type result,
                                                     boolean isStatic) {
        ClassNode helper = new ClassNode();
        helper.version = Opcodes.V21;
        helper.access = Opcodes.ACC_PUBLIC | Opcodes.ACC_SUPER;
        helper.name = name;
        helper.superName = "java/lang/Object";
        helper.interfaces = new ArrayList<>(List.of(OPERATION));
        String instanceField = null;
        if (!isStatic) {
            instanceField = "webmc$instance";
            helper.fields.add(new FieldNode(Opcodes.ACC_PRIVATE | Opcodes.ACC_FINAL, instanceField,
                    "L" + call.owner + ";", null, null));
        }
        MethodNode constructor = new MethodNode(Opcodes.ACC_PUBLIC, "<init>",
                instanceField == null ? "()V" : "(L" + call.owner + ";)V", null, null);
        constructor.instructions.add(new VarInsnNode(Opcodes.ALOAD, 0));
        constructor.instructions.add(new MethodInsnNode(Opcodes.INVOKESPECIAL, "java/lang/Object", "<init>", "()V", false));
        if (instanceField != null) {
            constructor.instructions.add(new VarInsnNode(Opcodes.ALOAD, 0));
            constructor.instructions.add(new VarInsnNode(Opcodes.ALOAD, 1));
            constructor.instructions.add(new FieldInsnNode(Opcodes.PUTFIELD, name, instanceField, "L" + call.owner + ";"));
        }
        constructor.instructions.add(new InsnNode(Opcodes.RETURN));
        helper.methods.add(constructor);

        MethodNode body = new MethodNode(Opcodes.ACC_PUBLIC, "call", OPERATION_CALL, null, null);
        InsnList code = body.instructions;
        int index = 0;
        if (instanceField != null) {
            code.add(new VarInsnNode(Opcodes.ALOAD, 0));
            code.add(new FieldInsnNode(Opcodes.GETFIELD, name, instanceField, "L" + call.owner + ";"));
        }
        for (Type parameter : parameters) {
            code.add(new VarInsnNode(Opcodes.ALOAD, 1));
            code.add(new LdcInsnNode(index++));
            code.add(new InsnNode(Opcodes.AALOAD));
            emitCast(code, parameter);
        }
        code.add(call);
        if (result.equals(Type.VOID_TYPE)) {
            code.add(new InsnNode(Opcodes.ACONST_NULL));
        } else {
            emitBox(code, result);
        }
        code.add(new InsnNode(Opcodes.ARETURN));
        helper.methods.add(body);
        return helper;
    }

    /** The Operation implementation for one wrapped site: `call(Object[] args)`. */
    private static ClassNode operationClass(String name, AbstractInsnNode node, List<Type> stack, Type produced,
                                            boolean isStaticCall) {
        ClassNode helper = new ClassNode();
        helper.version = Opcodes.V21;
        helper.access = Opcodes.ACC_PUBLIC | Opcodes.ACC_SUPER;
        helper.name = name;
        helper.superName = "java/lang/Object";
        helper.interfaces = new ArrayList<>(List.of(OPERATION));
        MethodNode constructor = new MethodNode(Opcodes.ACC_PUBLIC, "<init>", "()V", null, null);
        constructor.instructions.add(new VarInsnNode(Opcodes.ALOAD, 0));
        constructor.instructions.add(new MethodInsnNode(Opcodes.INVOKESPECIAL, "java/lang/Object", "<init>", "()V", false));
        constructor.instructions.add(new InsnNode(Opcodes.RETURN));
        helper.methods.add(constructor);

        MethodNode call = new MethodNode(Opcodes.ACC_PUBLIC, "call", OPERATION_CALL, null, null);
        InsnList code = call.instructions;
        int slot = 1; // arg 0 is `Object[] args` in a static sense; local 1 is the array
        for (int i = 0; i < stack.size(); i++) {
            code.add(new VarInsnNode(Opcodes.ALOAD, 1));
            code.add(new LdcInsnNode(i));
            code.add(new InsnNode(Opcodes.AALOAD));
            emitCast(code, stack.get(i));
        }
        if (node instanceof MethodInsnNode original) {
            code.add(new MethodInsnNode(original.getOpcode(), original.owner, original.name, original.desc, original.itf));
        } else {
            FieldInsnNode original = (FieldInsnNode) node;
            code.add(new FieldInsnNode(original.getOpcode(), original.owner, original.name, original.desc));
        }
        if (produced.equals(Type.VOID_TYPE)) {
            code.add(new InsnNode(Opcodes.ACONST_NULL));
            code.add(new InsnNode(Opcodes.ARETURN));
        } else {
            emitBox(code, produced);
            code.add(new InsnNode(Opcodes.ARETURN));
        }
        helper.methods.add(call);
        slot += 1;
        return helper;
    }

    /** Box a primitive so it can go into an Args. */
    private static void emitBox(InsnList code, Type type) {
        switch (type.getSort()) {
            case Type.BOOLEAN -> box(code, "java/lang/Boolean", "(Z)Ljava/lang/Boolean;");
            case Type.BYTE -> box(code, "java/lang/Byte", "(B)Ljava/lang/Byte;");
            case Type.CHAR -> box(code, "java/lang/Character", "(C)Ljava/lang/Character;");
            case Type.SHORT -> box(code, "java/lang/Short", "(S)Ljava/lang/Short;");
            case Type.INT -> box(code, "java/lang/Integer", "(I)Ljava/lang/Integer;");
            case Type.LONG -> box(code, "java/lang/Long", "(J)Ljava/lang/Long;");
            case Type.FLOAT -> box(code, "java/lang/Float", "(F)Ljava/lang/Float;");
            case Type.DOUBLE -> box(code, "java/lang/Double", "(D)Ljava/lang/Double;");
            default -> { }
        }
    }

    private static void box(InsnList code, String owner, String desc) {
        code.add(new MethodInsnNode(Opcodes.INVOKESTATIC, owner, "valueOf", desc, false));
    }

    /** @ModifyExpressionValue / @ModifyReturnValue: the handler consumes what the instruction produced. */
    private static int consumeResult(ClassNode target, String from, MethodNode handler, AnnotationNode annotation,
                                     List<String> notApplied, String where) {
        List<AnnotationNode> ats = annotations(annotation, "at");
        Point point = point(ats.isEmpty() ? new AnnotationNode("Lorg/spongepowered/asm/mixin/injection/At;") : ats.get(0));
        if (point.problem() != null) {
            notApplied.add(where + ": " + point.problem());
            return 0;
        }
        if (!point.kind().equals("INVOKE") && !point.kind().equals("FIELD") && !point.kind().equals("NEW")) {
            notApplied.add(where + ": " + describe(point) + " is not a place this applier wraps a value");
            return 0;
        }
        String name = named(handler, annotation);
        MethodNode victim = find(target, name, null);
        if (victim == null) {
            notApplied.add(where + ": no method " + name + " in this client to modify");
            return 0;
        }
        boolean mixinInstance = (handler.access & Opcodes.ACC_STATIC) == 0;
        Type[] handlerParameters = Type.getArgumentTypes(handler.desc);
        Type produced = handlerParameters.length > 0 ? handlerParameters[0] : Type.getObjectType("java/lang/Object");
        int done = 0;
        for (AbstractInsnNode node : locate(victim, point, notApplied, where)) {
            InsnList code = new InsnList();
            if (mixinInstance) {
                // The value the instruction produced is already on the stack; the receiver has to go
                // *under* it, so the value takes a detour through a local.
                int slot = freshLocal(victim, produced);
                code.add(new VarInsnNode(storeOpcode(produced), slot));
                code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                code.add(new VarInsnNode(loadOpcode(produced), slot));
            }
            code.add(new MethodInsnNode(mixinInstance ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC, target.name,
                    handler.name, handler.desc, false));
            victim.instructions.insert(node, code);
            done++;
        }
        addIfAbsent(target, cloneMethod(handler, from, target.name, false));
        return done;
    }

    /** @ModifyConstant: the handler consumes a constant and returns the one to use instead. */
    private static int modifyConstant(ClassNode target, String from, MethodNode handler, AnnotationNode annotation,
                                      List<String> notApplied, String where) {
        List<AnnotationNode> ats = annotations(annotation, "at");
        String constant = null;
        if (!ats.isEmpty()) {
            Point point = point(ats.get(0));
            if (point.problem() != null) {
                notApplied.add(where + ": " + point.problem());
                return 0;
            }
            constant = point.name();
        }
        String name = named(handler, annotation);
        MethodNode victim = find(target, name, null);
        if (victim == null) {
            notApplied.add(where + ": no method " + name + " in this client to modify");
            return 0;
        }
        boolean mixinInstance = (handler.access & Opcodes.ACC_STATIC) == 0;
        Type[] handlerParameters = Type.getArgumentTypes(handler.desc);
        Type produced = handlerParameters.length > 0 ? handlerParameters[0] : Type.INT_TYPE;
        int done = 0;
        for (AbstractInsnNode node = victim.instructions.getFirst(); node != null; node = node.getNext()) {
            if (!isConstant(node) || !constantMatches(node, constant)) {
                continue;
            }
            InsnList code = new InsnList();
            if (mixinInstance) {
                int slot = freshLocal(victim, produced);
                code.add(new VarInsnNode(storeOpcode(produced), slot));
                code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                code.add(new VarInsnNode(loadOpcode(produced), slot));
            }
            code.add(new MethodInsnNode(mixinInstance ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC, target.name,
                    handler.name, handler.desc, false));
            victim.instructions.insert(node, code);
            done++;
        }
        if (done == 0) {
            notApplied.add(where + ": no constant matching " + (constant == null ? "any" : constant)
                    + " in this client's build");
        }
        addIfAbsent(target, cloneMethod(handler, from, target.name, false));
        return done;
    }

    /** @ModifyVariable at a LOAD or STORE: the handler rewrites the value on its way past. */
    private static int modifyVariable(ClassNode target, String from, MethodNode handler, AnnotationNode annotation,
                                      List<String> notApplied, String where) {
        List<AnnotationNode> ats = annotations(annotation, "at");
        Point point = point(ats.isEmpty() ? new AnnotationNode("Lorg/spongepowered/asm/mixin/injection/At;") : ats.get(0));
        String kind = point.kind();
        if (!kind.equals("LOAD") && !kind.equals("STORE")) {
            notApplied.add(where + ": @ModifyVariable at " + describe(point)
                    + " is not applied (a LOAD or STORE point is)");
            return 0;
        }
        String name = named(handler, annotation);
        MethodNode victim = find(target, name, null);
        if (victim == null) {
            notApplied.add(where + ": no method " + name + " in this client to modify");
            return 0;
        }
        Type[] parameters = Type.getArgumentTypes(handler.desc);
        Type wanted = parameters.length > 0 ? parameters[0] : null;
        boolean mixinInstance = (handler.access & Opcodes.ACC_STATIC) == 0;
        int ordinal = point.ordinal();
        int seen = 0;
        int done = 0;
        for (AbstractInsnNode node = victim.instructions.getFirst(); node != null; node = node.getNext()) {
            if (!(node instanceof VarInsnNode variable)) {
                continue;
            }
            int opcode = variable.getOpcode();
            boolean isLoad = opcode >= Opcodes.ILOAD && opcode <= Opcodes.ALOAD;
            boolean isStore = opcode >= Opcodes.ISTORE && opcode <= Opcodes.ASTORE;
            if (kind.equals("LOAD") ? !isLoad : !isStore) {
                continue;
            }
            int sort = switch (opcode) {
                case Opcodes.LLOAD, Opcodes.LSTORE -> Type.LONG;
                case Opcodes.FLOAD, Opcodes.FSTORE -> Type.FLOAT;
                case Opcodes.DLOAD, Opcodes.DSTORE -> Type.DOUBLE;
                case Opcodes.ALOAD, Opcodes.ASTORE -> Type.OBJECT;
                default -> Type.INT;
            };
            if (wanted != null && wanted.getSort() != sort) {
                continue;
            }
            if (variable.var == 0 && isLoad) {
                continue; // local 0 is `this` in an instance method, not a variable a mod can rewrite
            }
            if (seen++ < ordinal) {
                continue;
            }
            InsnList code = new InsnList();
            Type valueType = typeOfSort(sort);
            if (mixinInstance) {
                // Both a LOAD and a STORE have the value on the stack, and the receiver has to go under
                // it: through a local, then back.
                int slot = freshLocal(victim, valueType);
                code.add(new VarInsnNode(storeOpcode(valueType), slot));
                code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                code.add(new VarInsnNode(loadOpcode(valueType), slot));
            }
            code.add(new MethodInsnNode(mixinInstance ? Opcodes.INVOKEVIRTUAL : Opcodes.INVOKESTATIC, target.name,
                    handler.name, handler.desc, false));
            if (kind.equals("LOAD")) {
                victim.instructions.insert(node, code);        // the loaded value is on the stack
            } else {
                victim.instructions.insertBefore(node, code);  // the value about to be stored is on the stack
            }
            done++;
            break;
        }
        if (done == 0) {
            notApplied.add(where + ": no " + kind + " of a matching local in this client's build");
        }
        addIfAbsent(target, cloneMethod(handler, from, target.name, false));
        return done;
    }

    /** @Accessor/@Invoker: write the member the mixin only declared. */
    private static String generate(ClassNode target, MethodNode method, AnnotationNode accessor, AnnotationNode invoker,
                                   String targetName) {
        if ((method.access & Opcodes.ACC_ABSTRACT) == 0 && method.instructions != null
                && method.instructions.size() > 1) {
            return "an @Accessor member with a body of its own is not generated";
        }
        String explicit = accessor != null ? string(accessor, "value") : string(invoker, "value");
        boolean isStatic = (method.access & Opcodes.ACC_STATIC) != 0;
        if (invoker != null) {
            String called = explicit == null || explicit.isBlank()
                    ? derive(method.name, "call", "invoke") : explicit.replaceAll("\\(.*$", "");
            MethodNode generated = new MethodNode(method.access & ~Opcodes.ACC_ABSTRACT, method.name, method.desc,
                    null, null);
            InsnList code = generated.instructions;
            if (!isStatic) {
                code.add(new VarInsnNode(Opcodes.ALOAD, 0));
            }
            int slot = isStatic ? 0 : 1;
            for (Type parameter : Type.getArgumentTypes(method.desc)) {
                code.add(new VarInsnNode(loadOpcode(parameter), slot));
                slot += parameter.getSize();
            }
            code.add(new MethodInsnNode(isStatic ? Opcodes.INVOKESTATIC : Opcodes.INVOKEVIRTUAL, targetName, called,
                    method.desc, false));
            code.add(new InsnNode(returnOpcode(Type.getReturnType(method.desc))));
            return addIfAbsent(target, generated) ? null : "@Invoker(" + called + "): " + method.name
                    + " is already in the target";
        }
        boolean setter = Type.getArgumentTypes(method.desc).length == 1;
        String field = explicit != null && explicit.indexOf('(') < 0 ? explicit
                : derive(method.name, setter ? "set" : "get", "is");
        Type fieldType = setter ? Type.getArgumentTypes(method.desc)[0] : Type.getReturnType(method.desc);
        if (setter && !Type.getReturnType(method.desc).equals(Type.VOID_TYPE)) {
            return "a setter has to return void";
        }
        MethodNode generated = new MethodNode(method.access & ~Opcodes.ACC_ABSTRACT, method.name, method.desc, null,
                null);
        InsnList code = generated.instructions;
        if (setter) {
            if (!isStatic) {
                code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                code.add(new VarInsnNode(loadOpcode(fieldType), 1));
                code.add(new FieldInsnNode(Opcodes.PUTFIELD, targetName, field, fieldType.getDescriptor()));
            } else {
                code.add(new VarInsnNode(loadOpcode(fieldType), 0));
                code.add(new FieldInsnNode(Opcodes.PUTSTATIC, targetName, field, fieldType.getDescriptor()));
            }
            code.add(new InsnNode(Opcodes.RETURN));
        } else {
            if (!isStatic) {
                code.add(new VarInsnNode(Opcodes.ALOAD, 0));
                code.add(new FieldInsnNode(Opcodes.GETFIELD, targetName, field, fieldType.getDescriptor()));
            } else {
                code.add(new FieldInsnNode(Opcodes.GETSTATIC, targetName, field, fieldType.getDescriptor()));
            }
            code.add(new InsnNode(returnOpcode(fieldType)));
        }
        return addIfAbsent(target, generated) ? null : "there is already a method " + method.name + method.desc;
    }

    private static String derive(String methodName, String... prefixes) {
        for (String prefix : prefixes) {
            if (methodName.length() > prefix.length() && methodName.startsWith(prefix)) {
                String rest = methodName.substring(prefix.length());
                return Character.toLowerCase(rest.charAt(0)) + rest.substring(1);
            }
        }
        return methodName;
    }

    /**
     * Apply a mod's access widener (or class tweaker) to the client's classes.
     *
     * <p>An access widener is how a mod asks for the right to reach a private field or call a protected method.
     * A runtime loader applies it to the classes as they load; here it is applied to the class files, which is
     * the same change made earlier. Three verbs: `accessible` (make it public), `extendable` (public and not
     * final), `mutable` (not final).</p>
     */
    private static void applyAccessWidener(ZipFile archive, Path classesRoot, Consumer<String> log,
                                          List<String> notApplied) throws IOException {
        ZipEntry descriptor = archive.getEntry("fabric.mod.json");
        if (descriptor == null) {
            return;
        }
        String file = firstStringAfter(read(archive, descriptor), "accessWidener");
        if (file == null) {
            return;
        }
        ZipEntry entry = archive.getEntry(file);
        if (entry == null) {
            notApplied.add(file + ": the descriptor names an access widener that is not in the jar");
            return;
        }
        int done = 0;
        for (String line : read(archive, entry).split("\n")) {
            String text = line.trim();
            if (text.isEmpty() || text.startsWith("#") || text.startsWith("accessWidener")
                    || text.startsWith("accesswidener") || text.startsWith("classtweaker")) {
                continue;
            }
            String[] parts = text.split("\\s+");
            if (parts.length < 3) {
                continue;
            }
            String verb = parts[0];
            String kind = parts[1];
            if (kind.equals("class")) {
                String owner = parts[2].replace('.', '/');
                Path target = classesRoot.resolve(owner + ".class");
                if (!Files.isRegularFile(target)) {
                    notApplied.add(file + ": " + verb + " class " + owner + " which is not in this client");
                    continue;
                }
                ClassNode node = read(target);
                if (verb.equals("accessible")) {
                    node.access = (node.access | Opcodes.ACC_PUBLIC) & ~(Opcodes.ACC_PRIVATE | Opcodes.ACC_PROTECTED);
                } else if (verb.equals("extendable")) {
                    node.access = (node.access | Opcodes.ACC_PUBLIC) & ~(Opcodes.ACC_FINAL | Opcodes.ACC_PRIVATE
                            | Opcodes.ACC_PROTECTED);
                } else {
                    notApplied.add(file + ": \"" + verb + " class\" is not a verb this applier knows");
                    continue;
                }
                write(node, target);
                done++;
                continue;
            }
            if (parts.length < 4) {
                continue;
            }
            String owner = parts[2].replace('.', '/');
            String member = parts[3];
            Path target = classesRoot.resolve(owner + ".class");
            if (!Files.isRegularFile(target)) {
                notApplied.add(file + ": " + verb + " " + kind + " on " + owner + ", which is not in this client");
                continue;
            }
            ClassNode node = read(target);
            boolean changed = false;
            if (kind.equals("method")) {
                int paren = member.indexOf('(');
                String name = paren < 0 ? member : member.substring(0, paren);
                String desc = paren < 0 ? null : member.substring(paren);
                for (MethodNode method : node.methods) {
                    if (!method.name.equals(name) || (desc != null && !method.desc.equals(desc))) {
                        continue;
                    }
                    method.access = touch(method.access, verb);
                    changed = true;
                }
            } else if (kind.equals("field")) {
                int colon = member.indexOf(':');
                String name = colon < 0 ? member : member.substring(0, colon);
                String desc = colon < 0 ? null : member.substring(colon + 1);
                for (FieldNode field : node.fields) {
                    if (!field.name.equals(name) || (desc != null && !field.desc.equals(desc))) {
                        continue;
                    }
                    field.access = touch(field.access, verb);
                    changed = true;
                }
            } else {
                notApplied.add(file + ": \"" + kind + "\" is not something an access widener can open");
                continue;
            }
            if (changed) {
                write(node, target);
                done++;
            } else {
                notApplied.add(file + ": " + verb + " " + kind + " " + owner + "." + member + " found nothing");
            }
        }
        if (done > 0) {
            log.accept("  access widener " + file + ": " + done + " change(s) applied to the client's classes");
        }
    }

    private static int touch(int access, String verb) {
        return switch (verb) {
            case "accessible" -> (access | Opcodes.ACC_PUBLIC) & ~(Opcodes.ACC_PRIVATE | Opcodes.ACC_PROTECTED);
            case "extendable" -> (access | Opcodes.ACC_PUBLIC) & ~(Opcodes.ACC_FINAL | Opcodes.ACC_PRIVATE
                    | Opcodes.ACC_PROTECTED);
            case "mutable" -> access & ~Opcodes.ACC_FINAL;
            default -> access;
        };
    }

    private static ClassNode read(Path file) throws IOException {
        ClassNode node = new ClassNode();
        try (InputStream stream = Files.newInputStream(file)) {
            new ClassReader(stream).accept(node, 0);
        }
        return node;
    }

    private static void write(ClassNode node, Path file) throws IOException {
        ClassWriter writer = new ClassWriter(ClassWriter.COMPUTE_FRAMES | ClassWriter.COMPUTE_MAXS) {
            @Override
            protected String getCommonSuperClass(String type1, String type2) {
                return "java/lang/Object";
            }
        };
        node.accept(writer);
        Files.write(file, writer.toByteArray());
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

    private static Integer integer(AnnotationNode annotation, String key) {
        List<Object> values = annotation.values == null ? List.of() : annotation.values;
        for (int i = 0; i + 1 < values.size(); i += 2) {
            if (String.valueOf(values.get(i)).equals(key) && values.get(i + 1) instanceof Integer value) {
                return value;
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

    /**
     * Fold a mod's entrypoint class into the client class, so no *new* class has to reach the compiler.
     *
     * TeaVM compiles the patched client jar, but a class that only a patch introduced did not survive to the output
     * twice over - the bake check can see the classes and the injected call in the jar while the compiled client has
     * no trace of either. Merging the entrypoint's code into the client's own class removes that question entirely:
     * the code lives where the compiler is already looking.
     *
     * Only methods that do not touch `this` are merged, as static methods; anything else is left alone and
     * reported, because an instance method moved to another class needs its fields moved with it.
     *
     * @return the names of the static methods now on the target, in the order they should be called
     */
    public static List<String> mergeEntrypoint(Path classesRoot, String entrypoint, String targetInternalName,
                                               Consumer<String> log) throws IOException {
        Path source = classesRoot.resolve(entrypoint + ".class");
        Path destination = classesRoot.resolve(targetInternalName + ".class");
        if (!Files.isRegularFile(source) || !Files.isRegularFile(destination)) {
            return List.of();
        }
        ClassNode mod = new ClassNode();
        try (InputStream stream = Files.newInputStream(source)) {
            new ClassReader(stream).accept(mod, 0);
        }
        ClassNode client = new ClassNode();
        try (InputStream stream = Files.newInputStream(destination)) {
            new ClassReader(stream).accept(client, 0);
        }
        List<String> merged = new ArrayList<>();
        for (MethodNode method : new ArrayList<>(mod.methods)) {
            if (method.name.equals("<init>") || method.name.equals("<clinit>")) {
                continue;
            }
            if (usesThis(method) || find(client, method.name, method.desc) != null) {
                log.accept("  entrypoint " + entrypoint.replace('/', '.') + "." + method.name
                        + " stays where it is (it uses its own instance, or the name is taken)");
                continue;
            }
            MethodNode copy = cloneMethod(method, mod.name, targetInternalName, false);
            copy.access = (copy.access | Opcodes.ACC_STATIC) & ~(Opcodes.ACC_PRIVATE | Opcodes.ACC_PROTECTED
                    | Opcodes.ACC_PUBLIC) | Opcodes.ACC_PUBLIC;
            client.methods.add(copy);
            merged.add(method.name + method.desc);
        }
        if (!merged.isEmpty()) {
            ClassWriter writer = new ClassWriter(ClassWriter.COMPUTE_FRAMES | ClassWriter.COMPUTE_MAXS) {
                @Override
                protected String getCommonSuperClass(String type1, String type2) {
                    return "java/lang/Object";
                }
            };
            client.accept(writer);
            Files.write(destination, writer.toByteArray());
        }
        return merged;
    }

    /** Whether a method's body reads local 0, i.e. `this` (a static method cannot be moved without it). */
    private static boolean usesThis(MethodNode method) {
        if ((method.access & Opcodes.ACC_STATIC) != 0) {
            return false;
        }
        for (AbstractInsnNode node = method.instructions.getFirst(); node != null; node = node.getNext()) {
            if (node instanceof VarInsnNode load && load.var == 0 && load.getOpcode() == Opcodes.ALOAD) {
                return true;
            }
        }
        return false;
    }

    /** Run by hand: `WebMcMixinApplier <mod.jar> <client-classes-dir>`, for when a build log is not enough. */
    public static void main(String[] args) throws IOException {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: WebMcMixinApplier <mod.jar> <client-classes-dir>");
        }
        Report report = apply(Path.of(args[0]), Path.of(args[1]), System.out::println);
        System.out.println("applied: " + report.classes() + " class(es), " + report.overwritten() + " overwritten, "
                + report.injected() + " injected, " + report.redirected() + " redirected, " + report.modified()
                + " modified, " + report.generated() + " generated, " + report.copiedMembers()
                + " member(s) copied, " + report.shadows() + " shadow(s) read");
        report.notApplied().forEach(line -> System.out.println("not applied: " + line));
        report.refusals().forEach(line -> System.out.println("REFUSED: " + line));
        if (!report.refusals().isEmpty()) {
            System.exit(1);
        }
    }
}
