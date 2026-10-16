package com.llamalad7.mixinextras.injector.wrapmethod;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * @WrapMethod: the handler replaces a whole method and is given an Operation that calls the original.
 *
 * The original is kept under a private name of its own, so "call the original" stays possible - which
 * is the whole point of wrapping rather than overwriting.
 */
@Retention(RetentionPolicy.CLASS)
@Target(ElementType.METHOD)
public @interface WrapMethod {
    String method() default "";
}
