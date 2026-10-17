package org.slf4j.event;

/** The five levels, as slf4j 2.x names them. */
public enum Level {
    TRACE(00), DEBUG(10), INFO(20), WARN(30), ERROR(40);

    private final int value;

    Level(int value) {
        this.value = value;
    }

    public int toInt() {
        return this.value;
    }

    public static Level intToLevel(int value) {
        for (Level level : values()) {
            if (level.value == value) {
                return level;
            }
        }
        return INFO;
    }
}
