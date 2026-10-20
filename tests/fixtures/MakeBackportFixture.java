// Writes a small jar that looks like Java 21 bytecode: it calls the members that did not exist before Java
// 21, plus three calls that must NOT be touched (they broke the client when they were). Used by
// tests/test_backport.py.
import org.objectweb.asm.*;

import java.io.*;
import java.nio.file.*;
import java.util.jar.*;

public class MakeBackportFixture {
    private static final String OWNER = "fixture/Sample";

    public static void main(String[] args) throws Exception {
        Path output = Path.of(args[0]);
        Files.createDirectories(output.getParent());
        // a nearby class that is NOT a List but has members with the same names: Iterator.getFirst(a,b) and
        // Comparator.reversed() are the shapes that were rewritten by mistake before
        ClassWriter other = new ClassWriter(0);
        other.visit(65, Opcodes.ACC_PUBLIC, "fixture/Trap", null, "java/lang/Object", null);
        MethodVisitor twoArg = other.visitMethod(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC,
                "getFirst", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", null, null);
        twoArg.visitCode();
        twoArg.visitVarInsn(Opcodes.ALOAD, 0);
        twoArg.visitInsn(Opcodes.ARETURN);
        twoArg.visitMaxs(1, 2);
        twoArg.visitEnd();
        MethodVisitor reversed = other.visitMethod(Opcodes.ACC_PUBLIC,
                "reversed", "()Lfixture/Trap;", null, null);
        reversed.visitCode();
        reversed.visitVarInsn(Opcodes.ALOAD, 0);
        reversed.visitInsn(Opcodes.ARETURN);
        reversed.visitMaxs(1, 1);
        reversed.visitEnd();
        other.visitEnd();

        ClassWriter writer = new ClassWriter(0);
        writer.visit(65, Opcodes.ACC_PUBLIC, OWNER, null, "java/lang/Object", null);

        // a field of a type that only exists in Java 21, to exercise the type remapping
        writer.visitField(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC, "names",
                "Ljava/util/SequencedMap;", null, null).visitEnd();

        MethodVisitor first = writer.visitMethod(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC,
                "first", "(Ljava/util/List;)Ljava/lang/Object;", null, null);
        first.visitCode();
        first.visitVarInsn(Opcodes.ALOAD, 0);
        first.visitMethodInsn(Opcodes.INVOKEINTERFACE, "java/util/List", "getFirst",
                "()Ljava/lang/Object;", true);
        first.visitInsn(Opcodes.ARETURN);
        first.visitMaxs(1, 1);
        first.visitEnd();

        MethodVisitor last = writer.visitMethod(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC,
                "lastReversed", "(Ljava/util/List;)Ljava/util/List;", null, null);
        last.visitCode();
        last.visitVarInsn(Opcodes.ALOAD, 0);
        last.visitMethodInsn(Opcodes.INVOKEINTERFACE, "java/util/List", "reversed",
                "()Ljava/util/List;", true);
        last.visitInsn(Opcodes.ARETURN);
        last.visitMaxs(1, 1);
        last.visitEnd();

        MethodVisitor clamp = writer.visitMethod(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC,
                "clampIt", "(J)J", null, null);
        clamp.visitCode();
        clamp.visitVarInsn(Opcodes.LLOAD, 0);
        clamp.visitInsn(Opcodes.LCONST_1);
        clamp.visitLdcInsn(5L);
        clamp.visitMethodInsn(Opcodes.INVOKESTATIC, "java/lang/Math", "clamp", "(JJJ)J", false);
        clamp.visitInsn(Opcodes.LRETURN);
        clamp.visitMaxs(6, 2);
        clamp.visitEnd();

        MethodVisitor sleep = writer.visitMethod(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC,
                "sleepDuration", "(Ljava/time/Duration;)V", null, null);
        sleep.visitCode();
        sleep.visitVarInsn(Opcodes.ALOAD, 0);
        sleep.visitMethodInsn(Opcodes.INVOKESTATIC, "java/lang/Thread", "sleep",
                "(Ljava/time/Duration;)V", false);
        sleep.visitInsn(Opcodes.RETURN);
        sleep.visitMaxs(1, 1);
        sleep.visitEnd();

        // Thread.sleep(long) is Java 1.0 and must survive untouched, as must the two traps
        MethodVisitor oldSleep = writer.visitMethod(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC,
                "sleepMillis", "(J)V", null, null);
        oldSleep.visitCode();
        oldSleep.visitVarInsn(Opcodes.LLOAD, 0);
        oldSleep.visitMethodInsn(Opcodes.INVOKESTATIC, "java/lang/Thread", "sleep", "(J)V", false);
        oldSleep.visitInsn(Opcodes.RETURN);
        oldSleep.visitMaxs(2, 2);
        oldSleep.visitEnd();

        MethodVisitor traps = writer.visitMethod(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC,
                "traps", "(Lfixture/Trap;Ljava/lang/Object;)Ljava/lang/Object;", null, null);
        traps.visitCode();
        traps.visitVarInsn(Opcodes.ALOAD, 1);
        traps.visitVarInsn(Opcodes.ALOAD, 0);
        traps.visitMethodInsn(Opcodes.INVOKESTATIC, "fixture/Trap", "getFirst",
                "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", false);
        traps.visitVarInsn(Opcodes.ALOAD, 0);
        traps.visitMethodInsn(Opcodes.INVOKEVIRTUAL, "fixture/Trap", "reversed",
                "()Lfixture/Trap;", false);
        traps.visitInsn(Opcodes.POP);
        traps.visitInsn(Opcodes.ARETURN);
        traps.visitMaxs(2, 2);
        traps.visitEnd();

        writer.visitEnd();

        try (JarOutputStream jar = new JarOutputStream(Files.newOutputStream(output))) {
            add(jar, OWNER + ".class", writer.toByteArray());
            add(jar, "fixture/Trap.class", other.toByteArray());
        }
        System.out.println("wrote " + output);
    }

    private static void add(JarOutputStream jar, String name, byte[] bytes) throws IOException {
        jar.putNextEntry(new JarEntry(name));
        jar.write(bytes);
        jar.closeEntry();
    }
}
