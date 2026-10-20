package net.fabricmc.fabric.api.event;

public interface Event<T> {
    T invoker();

    void register(T listener);
}
