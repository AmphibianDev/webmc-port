package org.quiltmc.loader.api;

/** A version, in the shape Quilt's API declares it: a semantic version with a raw string. */
public interface Version {
    String raw();

    Semantic semantic();

    /** The semantic version: major.minor.patch, with the prerelease kept. */
    final class Semantic implements Comparable<Semantic> {
        private final String raw;
        private final String preRelease;

        private Semantic(String raw) {
            this.raw = raw;
            String[] split = raw.split("+", 2)[0].split("-", 2);
            this.preRelease = split.length > 1 ? split[1] : "";
        }

        public static Semantic of(String text) throws VersionFormatException {
            String value = text == null ? "" : text.trim();
            if (value.isEmpty()) {
                throw new VersionFormatException("a version is not empty");
            }
            return new Semantic(value);
        }

        public String raw() {
            return this.raw;
        }

        public String preRelease() {
            return this.preRelease;
        }

        @Override
        public int compareTo(Semantic other) {
            int[] left = numbers(this.raw);
            int[] right = numbers(other.raw);
            for (int i = 0; i < Math.max(left.length, right.length); i++) {
                int a = i < left.length ? left[i] : 0;
                int b = i < right.length ? right[i] : 0;
                if (a != b) {
                    return Integer.compare(a, b);
                }
            }
            if (this.preRelease.isEmpty() == other.preRelease.isEmpty()) {
                return this.preRelease.compareTo(other.preRelease);
            }
            return this.preRelease.isEmpty() ? 1 : -1;
        }

        private static int[] numbers(String text) {
            String[] parts = text.split("[.+-]");
            int[] out = new int[parts.length];
            for (int i = 0; i < parts.length; i++) {
                try {
                    out[i] = Integer.parseInt(parts[i].replaceAll("[^0-9]", ""));
                } catch (NumberFormatException error) {
                    out[i] = 0;
                }
            }
            return out;
        }

        @Override
        public String toString() {
            return this.raw;
        }
    }
}
