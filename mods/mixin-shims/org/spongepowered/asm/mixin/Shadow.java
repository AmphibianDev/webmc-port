package org.spongepowered.asm.mixin;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * A member of the target class, declared so the mixin can use it.
 *
 * <p>A shadow is a promise about a class that already exists, so the bake never copies one: the merged method
 * resolves against the target's own member, which is the whole point.</p>
 */
@Retention(RetentionPolicy.CLASS)
@Target({ElementType.METHOD, ElementType.FIELD})
public @interface Shadow {
    String prefix() default "shadow$";

    String[] aliases() default {};
}
