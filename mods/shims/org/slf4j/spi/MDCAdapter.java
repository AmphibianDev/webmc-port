package org.slf4j.spi;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashMap;
import java.util.Map;

/** The mapped diagnostic context. A tab has one "thread", so a map is all it needs. */
public interface MDCAdapter {
    void put(String key, String value);

    String get(String key);

    void remove(String key);

    void clear();

    Map<String, String> getCopyOfContextMap();

    void setContextMap(Map<String, String> contextMap);

    void pushByKey(String key, String value);

    String popByKey(String key);

    Deque<String> getCopyOfDequeByKey(String key);

    void clearDequeByKey(String key);

    /** A working implementation, because Fabric API's logging path calls it. */
    final class Simple implements MDCAdapter {
        private final Map<String, String> context = new HashMap<>();
        private final Map<String, Deque<String>> stacks = new HashMap<>();

        @Override
        public void put(String key, String value) {
            this.context.put(key, value);
        }

        @Override
        public String get(String key) {
            return this.context.get(key);
        }

        @Override
        public void remove(String key) {
            this.context.remove(key);
        }

        @Override
        public void clear() {
            this.context.clear();
            this.stacks.clear();
        }

        @Override
        public Map<String, String> getCopyOfContextMap() {
            return new HashMap<>(this.context);
        }

        @Override
        public void setContextMap(Map<String, String> contextMap) {
            this.context.clear();
            if (contextMap != null) {
                this.context.putAll(contextMap);
            }
        }

        @Override
        public void pushByKey(String key, String value) {
            this.stacks.computeIfAbsent(key, ignored -> new ArrayDeque<>()).push(value);
        }

        @Override
        public String popByKey(String key) {
            Deque<String> stack = this.stacks.get(key);
            return stack == null || stack.isEmpty() ? null : stack.pop();
        }

        @Override
        public Deque<String> getCopyOfDequeByKey(String key) {
            Deque<String> stack = this.stacks.get(key);
            return stack == null ? null : new ArrayDeque<>(stack);
        }

        @Override
        public void clearDequeByKey(String key) {
            this.stacks.remove(key);
        }
    }
}
