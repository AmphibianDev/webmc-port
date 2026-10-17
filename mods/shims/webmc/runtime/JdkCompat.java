package webmc.runtime;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The Java methods a mod calls that this client's class library does not have yet.
 *
 * TeaVM implements the Java standard library itself, and it is not the whole of Java 21: a mod compiled
 * against 21 can call something as ordinary as `String.splitWithDelimiters`, and the compile stops there.
 * Rather than change the mod, the bake rewrites those calls to the methods here - which do the same thing,
 * written the way this client can run.
 *
 * Each one is named after the JDK method it stands in for, so a stack trace reads honestly.
 */
public final class JdkCompat {
    private JdkCompat() {
    }

    /** {@code String.splitWithDelimiters(String regex, int limit)}: the values and the separators. */
    public static String[] splitWithDelimiters(String input, String regex, int limit) {
        if (input == null) {
            throw new NullPointerException("input is null");
        }
        if (regex == null) {
            throw new NullPointerException("regex is null");
        }
        if (input.isEmpty()) {
            return new String[] { "" };
        }
        Pattern pattern = Pattern.compile(regex);
        Matcher matcher = pattern.matcher(input);
        List<String> parts = new ArrayList<>();
        int index = 0;
        int count = 0;
        while (matcher.find()) {
            if (limit > 0 && count >= limit - 1) {
                break;
            }
            parts.add(input.substring(index, matcher.start()));
            parts.add(matcher.group());
            index = matcher.end();
            count += 1;
        }
        parts.add(input.substring(index));
        return parts.toArray(new String[0]);
    }
}
