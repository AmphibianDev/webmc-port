#!/usr/bin/env python3
"""Turn the measured LWJGL 3 surface into the shim's skeleton.

    python3 tools/lwjgl3_scaffold.py --surface work/surface-1.20.4.json --out lwjgl3-webgl
    python3 tools/lwjgl3_scaffold.py --surface work/surface-1.13.2.json --surface work/surface-1.20.4.json --merge

The point of generating these classes rather than writing them by hand: the surface is a measurement,
not an opinion, so the skeleton can never drift from what the clients actually ask for - and every
member the game calls has exactly one place waiting for it.

Each generated method throws NotImplemented, with its own name in the message, so the first thing a
client does - and therefore the next thing to implement - is what the exception says.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

HANDWRITTEN = "// HANDWRITTEN:"

# Provided by hand-written implementations, so the generator must not emit them at all - and a
# generated file cannot have the same JVM name as a hand-written one.
EXTERNAL = {
    "org.lwjgl.system.MemoryUtil$MemoryAllocator",
    "org.lwjgl.system.MemoryUtil$MemoryAllocator$1",
    "org.lwjgl.system.MemoryStack$1",
    "org.lwjgl.system.FunctionProvider$Default",
    "org.lwjgl.system.CustomBuffer$1",
}

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

PRIMITIVES = {
    "V": "void", "Z": "boolean", "B": "byte", "C": "char", "S": "short",
    "I": "int", "J": "long", "F": "float", "D": "double",
}

# Types the shim provides itself, or that Java already has: everything else is a nested LWJGL class
# (a callback interface, a struct) which the skeleton also has to declare.
NESTED_HINT = ("GLFW", "Callbacks", "Memory", "Pointer", "Callback", "Function", "STB", "ALC", "AL", "NVG")

# Every constant LWJGL declares, with its real value and type (9,533 of them, read out of the jars by
# tools/lwjgl3_constants.py). A wrong constant is worse than a missing one: it silently changes what
# the client asks for.
CONSTANTS_PATH = ROOT / "fixtures" / "lwjgl3-constants.json"
CONSTANTS = json.loads(CONSTANTS_PATH.read_text()) if CONSTANTS_PATH.is_file() else {}


def java_type(descriptor: str) -> str:
    """A JVM descriptor as Java source, for the common cases a client actually uses."""
    array_depth = 0
    while descriptor.startswith("["):
        array_depth += 1
        descriptor = descriptor[1:]
    if descriptor.startswith("L") and descriptor.endswith(";"):
        internal = descriptor[1:-1]
        simple = internal.split("/")[-1]
        name = internal.replace("/", ".") if not internal.startswith("java/") else internal.replace("/", ".")
        suffix = "$" in simple
        if internal.startswith("java/"):
            name = internal.replace("/", ".")
        if internal.startswith("org/lwjgl") and not suffix:
            name = internal.replace("/", ".")
        return name + "[]" * array_depth
    return PRIMITIVES.get(descriptor, "Object") + "[]" * array_depth


def split_member(entry: str) -> tuple[str, str, str, str]:
    """(kind, owner, name, descriptor) out of one measured reference."""
    if ":" in entry[:3]:
        kind, rest = entry.split(":", 1)
    else:
        kind, rest = "M", entry  # a scan from before references carried their kind
    owner, rest = rest.split("#", 1)
    if "(" in rest:
        name, tail = rest.split("(", 1)
        descriptor = "(" + tail
    else:
        # a field reference carries its descriptor after a colon: "name:I"
        name, _, field_descriptor = rest.partition(":")
        descriptor = field_descriptor
    return kind, owner, name, descriptor


def field_type(descriptor: str) -> str:
    return java_type(descriptor) if descriptor else "long"


def constant_for(owner: str, name: str) -> tuple[str, str] | None:
    """The declared value of a constant, straight out of the real LWJGL jars."""
    entry = CONSTANTS.get(f"{owner}.{name}")
    if not entry:
        return None
    return field_type(entry["descriptor"]), repr(entry["value"]) if not isinstance(entry["value"], float) \
        else f"{entry['value']}f"


def parameters(descriptor: str) -> list[str]:
    """The argument list of a method descriptor, as Java declarations."""
    if not descriptor.startswith("("):
        return []
    inside, _, _ = descriptor[1:].partition(")")
    out, index = [], 0
    while index < len(inside):
        start = index
        while index < len(inside) and inside[index] == "[":
            index += 1
        if index < len(inside) and inside[index] == "L":
            index = inside.index(";", index) + 1
        else:
            index += 1
        out.append(java_type(inside[start:index]))
    return out


def return_type(descriptor: str) -> str:
    if ")" not in descriptor:
        return "void"
    return java_type(descriptor.split(")", 1)[1])


def build(surfaces: list[dict], merge: bool) -> dict:
    """owner -> {member: kind}, unioned across surfaces when asked."""
    owners: dict[str, dict[str, str]] = collections.defaultdict(dict)
    order: list[str] = []
    # a surface file holds a list of scans (one per jar); accept a bare dict too
    flattened = []
    for surface in surfaces:
        flattened.extend(surface if isinstance(surface, list) else [surface])
    for surface in flattened:
        for group, references in surface.get("references", {}).items():
            for entry in references:
                # entries are "M:owner#name(desc)" or "F:owner#name:desc"; older scans lack the kind
                normalised = entry if ":" in entry[:3] else f"M:{entry}"
                kind, owner, name, descriptor = split_member(normalised)
                if owner not in owners:
                    order.append(owner)
                owners[owner].setdefault(normalised, kind)
        if not merge:
            break
    return {"owners": owners, "order": order}


CALLBACK_HINTS = ("Callback", "Function", "Allocator", "I$")


def is_callback(simple: str) -> bool:
    return simple.endswith("I") or any(hint.rstrip("$") in simple for hint in CALLBACK_HINTS)


def referenced_types(members: dict[str, str]) -> set[str]:
    """Every org.lwjgl type named in a signature."""
    found: set[str] = set()
    for entry in members:
        _, _, _, descriptor = split_member(entry)
        if not descriptor:
            continue
        for candidate in re.findall(r"L(org/lwjgl/[^;]+);", descriptor):
            found.add(candidate.replace("/", "."))
    return found


def emit_placeholder(name: str) -> str:
    package, _, simple = name.rpartition(".")
    kind = "interface" if is_callback(simple) else "class"
    return (f"package {package};\n\n"
            f"// Generated by tools/lwjgl3_scaffold.py: a type the surface names but whose members the\n"
            f"// clients never call directly. Fill it in when something needs it.\n"
            f"public {kind} {simple} {{\n}}\n")


def emit_owner(owner: str, members: dict[str, str]) -> str:
    package, _, simple = owner.rpartition(".")
    simple = re.sub(r"[^A-Za-z0-9_$]", "_", simple)
    lines = [
        f"package {package};",
        "",
        "// Generated by tools/lwjgl3_scaffold.py from the surface a real client uses.",
        "// Every member below is called by the game; each one is a place to fill in.",
        f"public final class {simple} {{",
        f"    private {simple}() {{}}",
        "",
    ]
    for entry, kind in sorted(members.items()):
        _, _, name, descriptor = split_member(entry)
        # a constructor or a static initialiser is not a static method; the shim's own classes
        # declare those themselves, and emitting them produced a file that would not compile
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name):
            continue
        if kind == "F":
            known = constant_for(owner, name)
            if known:
                declared, value = known
                lines.append(f"    public static final {declared} {name} = {value};")
            else:
                lines.append(f"    public static {field_type(descriptor)} {name};  // set by the shim")
            continue
        args = ", ".join(f"{type_name} arg{index}" for index, type_name in enumerate(parameters(descriptor)))
        returns = return_type(descriptor)
        body = (f"throw new UnsupportedOperationException(\"{owner}.{name}\");")
        lines.append(f"    public static {returns} {name}({args}) {{ {body} }}")
        lines.append("")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", action="append", default=[], help="a surface json from lwjgl3_surface.py")
    parser.add_argument("--out", default=str(ROOT / "lwjgl3-webgl"), help="where the skeleton goes")
    parser.add_argument("--merge", action="store_true", help="union every surface into one shim")
    args = parser.parse_args(argv)

    surfaces = [json.loads(pathlib.Path(path).read_text()) for path in args.surface]
    if not surfaces:
        print("no --surface given: run tools/lwjgl3_surface.py first", file=sys.stderr)
        return 1
    if not args.merge and len(surfaces) > 1:
        surfaces = surfaces[:1]

    built = build(surfaces, args.merge)
    out = pathlib.Path(args.out) / "src" / "main" / "java"
    written = 0
    kept = 0
    for owner in built["order"]:
        members = built["owners"][owner]
        if not members or owner in EXTERNAL:
            continue
        path = out / pathlib.PurePosixPath(owner.replace(".", "/") + ".java")
        if path.is_file() and HANDWRITTEN in path.read_text():
            kept += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(emit_owner(owner, members))
        written += 1

    # a name provided by hand-written code must not also exist as a generated file: two classes with
    # the same JVM name is a compile error, and a stale one from an earlier run is easy to leave behind
    for name in EXTERNAL:
        stale = out / pathlib.PurePosixPath(name.replace(".", "/") + ".java")
        if stale.is_file() and HANDWRITTEN not in stale.read_text():
            stale.unlink()
            print(f"  removed a generated file that a hand-written class now provides: {name}")

    # and every type those classes mention, so the skeleton compiles as a whole
    declared = set(built["owners"])
    mentioned: set[str] = set()
    for members in built["owners"].values():
        mentioned |= referenced_types(members)
    placeholders = 0
    for name in sorted(mentioned - declared):
        if name in EXTERNAL:
            continue
        path = out / pathlib.PurePosixPath(name.replace(".", "/") + ".java")
        if path.is_file():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(emit_placeholder(name))
        placeholders += 1
    print(f"  plus {placeholders} type placeholder(s) named in signatures but not called")

    # the generator has to produce compilable Java, so check its own output: every name it emits
    # must be a legal identifier and every file must exist
    illegal = []
    for owner, members in built["owners"].items():
        for entry in members:
            _, _, name, _ = split_member(entry)
            if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name):
                illegal.append(f"{owner}#{name}")
    if illegal:
        print(f"  skipping {len(illegal)} non-identifier member(s): {illegal[:4]}")

    total = sum(len(m) for m in built["owners"].values())
    print(f"wrote {written} classes, {total} members, into {out} ({kept} hand-written kept)")
    groups: dict[str, int] = collections.Counter()
    for owner, members in built["owners"].items():
        groups[owner.split(".")[2] if owner.startswith("org.lwjgl.") else "other"] += len(members)
    for group, count in groups.most_common():
        print(f"  {group:<10} {count:>5} members")
    return 0


if __name__ == "__main__":
    sys.exit(main())
