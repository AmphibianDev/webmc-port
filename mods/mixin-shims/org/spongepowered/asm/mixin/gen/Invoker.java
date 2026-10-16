package org.spongepowered.asm.mixin.gen;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** Call a method of the target, named by {@code value} or implied by the method's own name. */
@Retention(RetentionPolicy.CLASS)
@Target({ElementType.METHOD, ElementType.TYPE})
public @interface Invoker {
    String value() default "";
}
