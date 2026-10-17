package org.spongepowered.asm.mixin.injection;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** @ModifyVariable, as the applier reads it. */
@Retention(RetentionPolicy.CLASS)
@Target(ElementType.METHOD)
public @interface ModifyVariable {
    String[] method() default {};
    At[] at() default {};
    int index() default 0;
    String name() default "";
}
