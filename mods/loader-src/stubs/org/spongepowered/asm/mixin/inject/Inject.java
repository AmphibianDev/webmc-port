package org.spongepowered.asm.mixin.injection;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;

/** Compile-time stub of Mixin's @Inject (see Mixin.java in this folder). */
@Retention(RetentionPolicy.CLASS)
public @interface Inject {

    String[] method() default {};

    At[] at() default {};

    boolean cancellable() default false;

    int require() default -1;

    int expect() default 1;
}
