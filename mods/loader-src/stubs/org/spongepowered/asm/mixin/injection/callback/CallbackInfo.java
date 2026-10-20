package org.spongepowered.asm.mixin.injection.callback;

/**
 * Link-time stub, compiled into the client jar only - never into the bake.
 *
 * When Fabric's Mixin injects into a game method during the bake, it copies the handler
 * into the game class, and that merged method carries a CallbackInfo parameter. The class
 * we capture therefore references this type, so the ahead-of-time compiler needs it to
 * exist. The real Mixin library provides it during the bake; this file exists so the
 * AOT compile of the captured class links. Nothing here is ever executed: the injected
 * handlers do not call it.
 */
public class CallbackInfo {

    private final String name;
    private final boolean cancellable;
    private boolean cancelled;

    public CallbackInfo(String name, boolean cancellable) {
        this.name = name;
        this.cancellable = cancellable;
    }

    public String getId() {
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
            throw new UnsupportedOperationException("Not cancellable");
        }
        this.cancelled = true;
    }
}
