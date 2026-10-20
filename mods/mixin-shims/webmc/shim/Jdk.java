package webmc.shim;

// HANDWRITTEN: a stand-in for the few java.base methods this project's compiler lacks.
//
// The compiler here is TeaVM, whose class library stops short of a handful of Java 9+ methods. A mod that
// calls one cannot be rewritten away - the call is in code somebody else compiled - so the call is pointed
// at this class during the mod remap instead, and the semantics are implemented the way the JDK documents
// them: Class.getPackageName() is the package of the class's name, "java.lang" for primitives, and the
// component's package for arrays.
public final class Jdk {

    private Jdk() {
    }

    public static String packageName(Class<?> type) {
        String name = type.getName();
        while (name.startsWith("[")) {
            name = name.substring(1);
        }
        if (name.startsWith("L") && name.endsWith(";")) {
            name = name.substring(1, name.length() - 1);
        }
        if (name.indexOf('.') < 0) {
            return "";
        }
        int last = name.lastIndexOf('.');
        return name.substring(0, last);
    }

    public static String descriptorString(Class<?> type) {
        if (type.isArray()) {
            return type.getName().replace('.', '/');
        }
        if (type.isPrimitive()) {
            return java.lang.reflect.Array.newInstance(type, 0).getClass().getSimpleName().substring(0, 1);
        }
        return "L" + type.getName().replace('.', '/') + ";";
    }
}
