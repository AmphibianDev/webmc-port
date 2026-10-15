package org.spongepowered.asm.mixin.injection;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** Inject this method's body into the target method, at the place {@code at} names. */
@Retention(RetentionPolicy.CLASS)
@Target(ElementType.METHOD)
public @interface Inject {
    String[] method() default {};

    At[] at() default {};

    boolean cancellable() default false;
}
