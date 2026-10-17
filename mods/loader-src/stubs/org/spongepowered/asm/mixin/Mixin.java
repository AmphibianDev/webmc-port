package org.spongepowered.asm.mixin;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;

/**
 * Compile-time stub of Mixin's own annotation, so the loader's mixin class can be built
 * without Mixin on the classpath. Mixin reads these annotations out of the bytecode
 * (they are CLASS retention, so nothing resolves them at load time); the loader mod jar
 * ships without this file, so it can never shadow the real Mixin library during a bake.
 */
@Retention(RetentionPolicy.CLASS)
public @interface Mixin {

    Class<?>[] value() default {};

    String[] targets() default {};

    int priority() default 1000;

    boolean remap() default true;
}
