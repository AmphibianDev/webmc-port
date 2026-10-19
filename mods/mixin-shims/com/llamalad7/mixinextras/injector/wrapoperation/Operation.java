package com.llamalad7.mixinextras.injector.wrapoperation;

/**
 * The original operation a @WrapOperation handler was given: call it with the arguments it should use.
 *
 * The bake generates one implementation of this per wrapped call site (it has to: the operation, the
 * owner and the descriptor are only known there), and the handler receives one instead of the call
 * itself. Arguments are boxed, exactly as MixinExtras does it.
 */
public interface Operation<T> {
    T call(Object... args);
}
