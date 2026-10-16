package com.llamalad7.mixinextras.injector;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** @WrapOperation: the handler replaces a call and is given an Operation it can forward to. */
@Retention(RetentionPolicy.CLASS)
@Target(ElementType.METHOD)
public @interface WrapOperation {
    String[] method() default {};

    org.spongepowered.asm.mixin.injection.At[] at() default {};
}
