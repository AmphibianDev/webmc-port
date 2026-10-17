package org.spongepowered.asm.mixin.injection;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;

/** Where an injection goes. HEAD, RETURN and TAIL are the three this project's bake applies. */
@Retention(RetentionPolicy.CLASS)
public @interface At {
    String value();

    String target() default "";

    int ordinal() default -1;

    String[] args() default {};
}
