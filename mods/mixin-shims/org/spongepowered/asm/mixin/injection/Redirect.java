package org.spongepowered.asm.mixin.injection;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** @Redirect, as the applier reads it. */
@Retention(RetentionPolicy.CLASS)
@Target(ElementType.METHOD)
public @interface Redirect {
    String[] method() default {};
    At[] at() default {};
}
