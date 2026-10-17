package net.fabricmc.loader.api;

/**
 * A version, as the loader's API exposes it: a friendly string plus the comparison the loader does.
 *
 * Fabric API and mods use this for dependency checks, so it is implemented rather than stubbed - a
 * mod that refuses to load because its version check lies is worse than one that refuses for a
 * reason we can read.
 */
public final class Version implements Comparable<Version> {
    private final String friendly;
    private final int[] parts;
    private final String prerelease;

    private Version(String friendly) {
        this.friendly = friendly;
        String[] split = friendly.split("\\+", 2)[0].split("-", 2);
        this.prerelease = split.length > 1 ? split[1] : "";
        String[] numbers = split[0].split("\\.");
        this.parts = new int[numbers.length];
        for (int i = 0; i < numbers.length; i++) {
            try {
                this.parts[i] = Integer.parseInt(numbers[i].replaceAll("[^0-9]", ""));
            } catch (NumberFormatException error) {
                this.parts[i] = 0;
            }
        }
    }

    public static Version parse(String string) {
        return new Version(string == null ? "0" : string.trim());
    }

    public String getFriendlyString() {
        return this.friendly;
    }

    @Override
    public int compareTo(Version other) {
        int length = Math.max(this.parts.length, other.parts.length);
        for (int i = 0; i < length; i++) {
            int left = i < this.parts.length ? this.parts[i] : 0;
            int right = i < other.parts.length ? other.parts[i] : 0;
            if (left != right) {
                return Integer.compare(left, right);
            }
        }
        if (this.prerelease.isEmpty() == other.prerelease.isEmpty()) {
            return this.prerelease.compareTo(other.prerelease);
        }
        return this.prerelease.isEmpty() ? 1 : -1; // a prerelease is lower than the release
    }

    @Override
    public boolean equals(Object other) {
        return other instanceof Version version && this.friendly.equals(version.friendly);
    }

    @Override
    public int hashCode() {
        return this.friendly.hashCode();
    }

    @Override
    public String toString() {
        return this.friendly;
    }
}
