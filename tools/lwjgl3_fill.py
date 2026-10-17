#!/usr/bin/env python3
"""Declare every member a client demands that the shim does not have yet.

    python3 tools/lwjgl3_fill.py --surface /tmp/surface-1.21.11.json

The scaffold (tools/lwjgl3_scaffold.py) writes whole files and only ever keeps the ones marked
HANDWRITTEN, which is right when the shim is being born and wrong once it works: 1.21.11 demands 321
members, the shim already answers 229 of them in hand-written code, and the remaining 92 - in the classes
that already exist - would be lost by regenerating.

So this only ever *adds*: a member the shim already declares is left exactly as it is (implemented or
not), and a member it does not declare is appended as a stub that throws with its own name, which is the
first thing the client will report and therefore the next thing to implement. Nothing is ever rewritten.

Adding a stub for a member Minecraft never actually calls costs nothing at run time: a Java method that
is never invoked is never resolved.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIM = ROOT / "lwjgl3-webgl" / "src" / "main" / "java"

# Provided by the platform or by hand-written code: generating anything here would collide with it.
SKIP = {
    "org.lwjgl.system.MemoryUtil$MemoryAllocator",
    "org.lwjgl.system.MemoryUtil$MemoryAllocator$1",
    "org.lwjgl.system.WebMC",
}

# The Java name of each primitive, for building a signature out of the surface's descriptor.
PRIMITIVE = {"Z": "boolean", "B": "byte", "C": "char", "S": "short", "I": "int", "J": "long",
             "F": "float", "D": "double", "V": "void"}


def java_name(type_name: str) -> str:
    """A JVM type descriptor as Java source: `Lorg/lwjgl/glfw/GLFW` + `;` becomes `org.lwjgl.glfw.GLFW`.

    Getting this wrong is not a cosmetic problem: the generator emitted `public static Lorg.lwjgl...; foo()`
    and the shim stopped compiling, which is what the first run of this tool did.
    """
    if not type_name:
        return "java.lang.Object"
    if type_name.startswith("["):
        return java_name(type_name[1:]) + "[]"
    if type_name.startswith("L") and type_name.endswith(";"):
        return type_name[1:-1].replace("/", ".")
    return PRIMITIVE.get(type_name, type_name.replace("/", "."))


# names the compiler generates for itself: a string concat bootstrap is not something a client calls
NOT_A_CALL = {"makeConcatWithConstants", "makeConcat", "$deserializeLambda$", "values", "valueOf"}


def parameters(descriptor: str) -> list[tuple[str, str]]:
    """The parameter types of a method descriptor, in order."""
    inner = descriptor[descriptor.index("(") + 1:descriptor.index(")")]
    found: list[str] = []
    index = 0
    while index < len(inner):
        char = inner[index]
        if char == "[":
            index += 1
            while inner[index] == "[":
                index += 1
            if inner[index] == "L":
                end = inner.index(";", index)
                found.append(inner[index:end + 1])
                index = end + 1
            else:
                found.append("[" + inner[index])
                index += 1
        elif char == "L":
            end = inner.index(";", index)
            found.append(inner[index:end + 1])
            index = end + 1
        else:
            found.append(char)
            index += 1
    names = ["arg" + str(position) for position in range(len(found))]
    return list(zip([java_name(type_name) for type_name in found], names))


def returns(descriptor: str) -> str:
    tail = descriptor[descriptor.index(")") + 1:]
    return java_name(tail)


def declares(text: str, member: str) -> bool:
    """Whether a class already declares this member, in any of the forms the shim uses."""
    if member == "<init>":
        return True  # constructors belong to hand-written code
    if re.search(r"\b" + re.escape(member) + r"\s*[(;=]", text):
        return True
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--surface", required=True)
    parser.add_argument("--shim", default=str(SHIM))
    parser.add_argument("--apply", action="store_true", help="write the stubs (otherwise just report)")
    args = parser.parse_args(argv)

    surface = json.loads(pathlib.Path(args.surface).read_text())
    if isinstance(surface, list):
        surface = surface[0]

    owners: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for references in surface["references"].values():
        for reference in references:
            kind, rest = reference.split(":", 1)
            owner, signature = rest.split("#", 1)
            if "(" in signature:
                member, descriptor = signature.split("(", 1)
                descriptor = "(" + descriptor
            elif ":" in signature:
                member, descriptor = signature.split(":", 1)   # a field: name:descriptor
            else:
                member, descriptor = signature, "()V"
            owners[owner][member] = descriptor

    root = pathlib.Path(args.shim)
    added = kept = missing_file = 0
    per_file = collections.Counter()
    for owner in sorted(owners):
        if owner in SKIP:
            continue
        path = root / pathlib.PurePosixPath(owner.replace(".", "/") + ".java")
        if not path.is_file():
            missing_file += 1
            continue
        text = path.read_text()
        to_add = []
        for member, descriptor in sorted(owners[owner].items()):
            if member in NOT_A_CALL or member == "<init>" or declares(text, member):
                kept += 1
                continue
            params = parameters(descriptor)
            declaration = ", ".join(f"{type_name} {name}" for type_name, name in params)
            to_add.append(
                f"\n    // demanded by the client; the JavaScript behind it is in natives/webmc-lwjgl3.js\n"
                f"    public static {returns(descriptor)} {member}({declaration}) "
                f'{{ throw new UnsupportedOperationException("{owner}.{member}"); }}\n')
        if to_add:
            added += len(to_add)
            per_file[owner] = len(to_add)
            if args.apply:
                text = text.rstrip()
                assert text.endswith("}"), path
                path.write_text(text[:-1].rstrip("\n") + "\n" + "".join(to_add) + "}\n")

    print(f"members already declared, left alone: {kept}")
    print(f"members added as stubs{'':<6}: {added}" if args.apply else f"members missing (use --apply): {added}")
    if missing_file:
        print(f"classes with no file at all: {missing_file} (run tools/lwjgl3_scaffold.py for those)")
    for owner, count in per_file.most_common(12):
        print(f"  {owner}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
