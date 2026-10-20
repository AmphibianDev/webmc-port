package com.llamalad7.mixinextras.injector;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** MixinExtras' @ModifyReceiver, read by name: the applier does not need the library to know what it means. */
@Retention(RetentionPolicy.CLASS)
@Target(ElementType.METHOD)
public @interface ModifyReceiver {
    String[] method() default {};

    org.spongepowered.asm.mixin.injection.At[] at() default {};
}
