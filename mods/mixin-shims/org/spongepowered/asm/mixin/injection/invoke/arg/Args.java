package org.spongepowered.asm.mixin.injection.invoke.arg;

/**
 * The arguments of a call, boxed, so a @ModifyArgs handler can read and replace them.
 *
 * Mixin's real class is abstract with a static factory; this one is the whole thing, because the bake
 * builds it directly with `new Args(Object[])` and a handler only ever uses these five methods.
 */
public class Args {
    private final Object[] values;

    public Args(Object[] values) {
        this.values = values;
    }

    public int size() {
        return this.values.length;
    }

    public Object get(int index) {
        return this.values[index];
    }

    public void set(int index, Object value) {
        this.values[index] = value;
    }

    public Object[] getAll() {
        return this.values.clone();
    }

    public void setAll(Object[] values) {
        System.arraycopy(values, 0, this.values, 0, Math.min(values.length, this.values.length));
    }
}
