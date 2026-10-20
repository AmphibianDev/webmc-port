package org.spongepowered.asm.mixin.injection;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;

/** Compile-time stub of Mixin's @At (see Mixin.java in this folder). */
@Retention(RetentionPolicy.CLASS)
public @interface At {

    String value();

    String target() default "";

    String[] args() default {};

    int ordinal() default -1;

    String remap() default "";

    boolean shift() default false;
}
