#!/usr/bin/env python3
"""Mark the members this shim actually implements in JavaScript, and make the rest say so.

    python3 tools/lwjgl3_natives.py --js lwjgl3-webgl/natives/webmc-lwjgl3.js --java lwjgl3-webgl/src/main/java

The JavaScript file is the source of truth: a function named Java_<package>_<Class>_<method> means that
member is implemented. This walks the generated Java classes and, for every member with an
implementation, turns the stub into a `native` declaration - and leaves the others throwing, so a client
that reaches an unfinished member gets an exception naming it instead of silence.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def implemented(js_path: pathlib.Path) -> dict[str, set[str]]:
    """owner class -> method names, read out of the JavaScript."""
    found: dict[str, set[str]] = {}
    for match in re.finditer(r"function Java_([A-Za-z0-9_]+)\s*\(", js_path.read_text()):
        # the captured name is everything after "Java_", so for
        # Java_org_lwjgl_glfw_GLFW_glfwCreateWindow it reads org_lwjgl_glfw_GLFW_glfwCreateWindow:
        # the last segment is the method, the one before it the class, the rest the package
        parts = match.group(1).split("_")
        if len(parts) < 3:
            continue
        owner = ".".join(parts[:-2]) + "." + parts[-2]
        if not owner.startswith("org."):
            continue
        found.setdefault(owner, set()).add(parts[-1])
    return found


# Members the JavaScript implements that no measured client happened to call, so the scaffold has no
# reason to declare them. They are real LWJGL members - the boot test reaches for some of them - and
# without this table they would be JavaScript that nothing can ever call.
EXTRA_SIGNATURES = {
    "org.lwjgl.opengl.GL15": [
        ("nglBufferData", "int target, long size, long data, int usage"),
        ("nglGenBuffers", "int count, long pointers"),
    ],
    "org.lwjgl.opengl.GL30": [("nglGenVertexArrays", "int count, long pointers")],
    "org.lwjgl.opengl.GL11": [
        ("glReadPixels", "int x, int y, int width, int height, int format, int type, long pixels"),
        ("nglDrawArrays", "int mode, int first, int count"),
    ],
    "org.lwjgl.opengl.GL20": [
        ("nglVertexAttribPointer", "int index, int size, int type, boolean normalized, int stride, long pointer"),
    ],
}


def add_extras(java_root: pathlib.Path, log=print) -> int:
    """Declare the members the JavaScript implements but the measurement never saw."""
    added = 0
    for owner, members in EXTRA_SIGNATURES.items():
        path = java_root / pathlib.PurePosixPath(owner.replace(".", "/") + ".java")
        if not path.is_file():
            continue
        text = path.read_text()
        for name, parameters in members:
            if re.search(rf"\b{name}\(", text):
                continue
            marker = text.rindex("}")
            text = (text[:marker]
                    + "    // implemented in natives/webmc-lwjgl3.js (declared here because no measured\n"
                    + "    // client calls it, so the generated surface does not mention it)\n"
                    + f"    public static native void {name}({parameters});\n" + text[marker:])
            added += 1
            log(f"  added {owner}.{name}")
        path.write_text(text)
    return added


def rewrite(java_root: pathlib.Path, table: dict[str, set[str]]) -> dict:
    """Turn implemented stubs into native declarations."""
    stats = {"classes": 0, "natives": 0, "still_stubs": 0}
    for owner, methods in sorted(table.items()):
        path = java_root / pathlib.PurePosixPath(owner.replace(".", "/") + ".java")
        if not path.is_file():
            continue
        text = path.read_text()
        if "HANDWRITTEN" in text:
            continue
        lines = text.split("\n")
        out = []
        touched = False
        for line in lines:
            match = re.match(r"^(    public static )([\w.<>\[\]$]+ )(\w+)\((.*)\) \{ (throw .*?) \}$", line)
            if match and match.group(3) in methods:
                out.append(f"    // implemented in natives/webmc-lwjgl3.js")
                out.append(f"    public static native {match.group(2)}{match.group(3)}({match.group(4)});")
                stats["natives"] += 1
                touched = True
            else:
                if "UnsupportedOperationException" in line:
                    stats["still_stubs"] += 1
                out.append(line)
        if touched:
            path.write_text("\n".join(out))
            stats["classes"] += 1
    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--js", default=str(ROOT / "lwjgl3-webgl" / "natives" / "webmc-lwjgl3.js"))
    parser.add_argument("--java", default=str(ROOT / "lwjgl3-webgl" / "src" / "main" / "java"))
    parser.add_argument("--list", action="store_true", help="print what is implemented and stop")
    args = parser.parse_args(argv)

    table = implemented(pathlib.Path(args.js))
    total = sum(len(v) for v in table.values())
    print(f"the JavaScript implements {total} members across {len(table)} classes")
    for owner in sorted(table):
        print(f"  {owner:<44} {len(table[owner]):>3}")
    if args.list:
        return 0
    java_root = pathlib.Path(args.java)
    stats = rewrite(java_root, table)
    stats["extras"] = add_extras(java_root)
    print(f"  rewrote {stats['natives']} native declaration(s) in {stats['classes']} class(es); "
          f"{stats['still_stubs']} members still throw")
    (ROOT / "lwjgl3-webgl" / "implemented.json").write_text(json.dumps(
        {owner: sorted(methods) for owner, methods in sorted(table.items())}, indent=1) + "\n")

    # CheerpJ 4 does not pick implementations up from java.library.path: they are handed to
    # cheerpjInit as an object whose keys are the JNI names. The file is a module whose functions are
    # module-local (so they can call each other), and this export is the bridge. Generating it here
    # keeps it in step with the implementations: a function added to the file appears in the export, and
    # a name that stops existing leaves with it.
    js_path = pathlib.Path(args.js)
    text = js_path.read_text()
    # the regex captures the JNI name whole (it starts with Java_), so it needs no prefix. Names ending
    # in _inner are the traced implementations; the wrapper that carries the real name is the one
    # CheerpJ must be given.
    names = sorted({name for name in re.findall(r"function (Java_[A-Za-z0-9_]+)\s*\(", text)
                    if not name.endswith("_inner")})
    block = ("\n// ------------------------------------------------------------------ what CheerpJ is given\n"
             "// cheerpjInit({ natives }) takes exactly this: JNI names to implementations. It is\n"
             "// generated by tools/lwjgl3_natives.py from the functions above, so it cannot drift.\n"
             "//\n"
             # Each entry goes through webmcNamed + webmcNative, and the wrapper takes one *rest* parameter.
             # CheerpJ marshals a Java long into a wrapped native happily; a multi-parameter function with a
             # long in it stops with "TypeError: object is not a function" from inside its dispatcher, before
             # the native is entered - which looks exactly like a broken shim and is not one.
             "export const natives = {\n"
             + "".join(f'  {name}: webmcNamed("{name}", webmcNative({name})),\n' for name in names)
             + "};\n\nexport const count = " + str(len(names)) + ";\n")
    marker = "// ------------------------------------------------------------------ what CheerpJ is given"
    if marker in text:
        text = text[:text.index(marker)] + block.lstrip("\n")
    else:
        text = text.rstrip("\n") + "\n" + block
    js_path.write_text(text)
    print(f"  exported {len(names)} natives for cheerpjInit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
