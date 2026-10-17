package webmc.j21;

/**
 * Java 21's {@code java.lang.MatchException}, which Java 17 does not have.
 *
 * Pattern matching in a switch throws it when no case matches a sealed hierarchy, and the 1.21.11 client
 * has it in 151 of its classes' catch clauses. The backport remaps those references to this class: it is
 * the same kind of exception with the same supertype, so `catch (MatchException e)` behaves the same way,
 * and a JVM that has never heard of the name can still run the code.
 */
public class MatchException extends RuntimeException {
    private static final long serialVersionUID = 1L;

    public MatchException(String message, Throwable cause) {
        super(message, cause);
    }

    public MatchException(String message) {
        super(message);
    }
}
