package org.spongepowered.asm.mixin.injection.callback;

/** The same handle for a method that returns something, carrying the value an injection may replace. */
public class CallbackInfoReturnable<T> extends CallbackInfo {
    private T value;

    public CallbackInfoReturnable(String name, boolean cancellable) {
        super(name, cancellable);
    }

    public T getReturnValue() {
        return this.value;
    }

    public void setReturnValue(T value) {
        if (!isCancellable()) {
            throw new IllegalStateException("this injection is not cancellable");
        }
        this.value = value;
        cancel();
    }
}
