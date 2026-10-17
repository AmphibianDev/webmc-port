package org.spongepowered.asm.mixin.injection;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** @ModifyArgs: the handler is given the call's arguments, boxed, and may replace them. */
@Retention(RetentionPolicy.CLASS)
@Target(ElementType.METHOD)
public @interface ModifyArgs {
    String[] method() default {};

    At[] at() default {};
}
