package org.spongepowered.asm.mixin;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * The one annotation a mixin class cannot do without: it says which class the mixin edits.
 *
 * <p>These declarations are <b>compile-time stubs</b>. A mod is compiled against them so a developer can write
 * mixins here without downloading Sponge's toolchain, and our bake reads the same annotations out of the
 * compiled class file - where they are present as descriptors - to apply the mixin at build time. They are not
 * Mixin itself and do not pretend to be: see MIXINS.md for exactly which parts of Mixin are applied and which
 * are refused out loud.</p>
 */
@Retention(RetentionPolicy.CLASS)
@Target(ElementType.TYPE)
public @interface Mixin {
    Class<?>[] value() default {};

    String[] targets() default {};
}
