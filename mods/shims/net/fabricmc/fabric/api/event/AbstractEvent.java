package net.fabricmc.fabric.api.event;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Listener list shared by every event in this shim layer.
 *
 * Deliberately no reflection and no array creation: the client is compiled ahead of time
 * with TeaVM, so a plain list plus an explicit invoker is both smaller and safer.
 */
public abstract class AbstractEvent<T> implements Event<T> {
    private final List<T> listeners = new CopyOnWriteArrayList<>();

    @Override
    public void register(T listener) {
        if (listener == null) {
            throw new NullPointerException("listener");
        }
        listeners.add(listener);
    }

    protected List<T> listeners() {
        return listeners;
    }
}
