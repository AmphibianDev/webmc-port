package org.spongepowered.asm.mixin.injection.callback;

/**
 * The handle an injected method is given. Cancelling is how a mixin stops the rest of the target method; with
 * `cancellable = false` in the annotation it is just a marker the bake still has to construct.
 */
public class CallbackInfo {
    private final String name;
    private final boolean cancellable;
    private boolean cancelled;

    public CallbackInfo(String name, boolean cancellable) {
        this.name = name;
        this.cancellable = cancellable;
    }

    public String getName() {
        return this.name;
    }

    public boolean isCancellable() {
        return this.cancellable;
    }

    public boolean isCancelled() {
        return this.cancelled;
    }

    public void cancel() {
        if (!this.cancellable) {
            throw new IllegalStateException("this injection is not cancellable");
        }
        this.cancelled = true;
    }
}
