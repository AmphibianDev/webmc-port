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
    stats = rewrite(pathlib.Path(args.java), table)
    print(f"  rewrote {stats['natives']} native declaration(s) in {stats['classes']} class(es); "
          f"{stats['still_stubs']} members still throw")
    (ROOT / "lwjgl3-webgl" / "implemented.json").write_text(json.dumps(
        {owner: sorted(methods) for owner, methods in sorted(table.items())}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
