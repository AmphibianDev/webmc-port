#!/usr/bin/env python3
"""Read the real shape of every demanded LWJGL member, from the real LWJGL jars.

    python3 tools/lwjgl3_shapes.py --libs work/client/libs/1.21.11 --surface /tmp/surface-1.21.11.json
    python3 tools/lwjgl3_shapes.py --libs work/client/libs/1.21.11 --surface ... --check lwjgl3-webgl/src/main/java

The surface says *what* a client calls; it cannot say whether a member is a field or a method, static or
instance - that lives in the declaration, and the declaration is in the jars the client ships with. Without
it the skeleton guessed, and guessed wrong in a way that showed up immediately: `GLCapabilities` came out
with `public static void GL_ARB_debug_outputZ()` for what is really the instance field
`public final boolean GL_ARB_debug_output` (the trailing Z is the descriptor, run together with the name),
and `GLFWVidMode.width()` came out static when it is an accessor on a struct.

With the real jars there is nothing to guess. Each demanded member is looked up in the class that declares
it and reported as name, kind, staticness, descriptor and return type.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import struct
import sys

ACC_STATIC = 0x0008
ACC_FINAL = 0x0010
PRIMITIVE = {"Z": "boolean", "B": "byte", "C": "char", "S": "short", "I": "int", "J": "long",
             "F": "float", "D": "double", "V": "void"}


def java_type(descriptor: str) -> str:
    if not descriptor:
        return "java.lang.Object"
    if descriptor.startswith("["):
        return java_type(descriptor[1:]) + "[]"
    if descriptor.startswith("L") and descriptor.endswith(";"):
        return descriptor[1:-1].replace("/", ".")
    return PRIMITIVE.get(descriptor, descriptor)


def read_class(data: bytes) -> dict:
    """Fields and methods with their access flags and descriptors, from a class file header."""
    offset = 8
    count = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    pool: list = [None] * count
    index = 1
    while index < count:
        tag = data[offset]
        if tag == 1:
            length = struct.unpack_from(">H", data, offset + 1)[0]
            pool[index] = data[offset + 3:offset + 3 + length].decode("utf-8", "replace")
            offset += 3 + length
        elif tag == 7:
            pool[index] = ("class", struct.unpack_from(">H", data, offset + 1)[0])
            offset += 3
        elif tag in (8, 16, 19, 20):
            offset += 3
        elif tag in (15,):
            offset += 4
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            offset += 5
        elif tag in (5, 6):
            offset += 9
            index += 1
        else:
            raise ValueError(f"constant pool tag {tag}")
        index += 1
    def class_at(index: int) -> str:
        entry = pool[index] if index < len(pool) else None
        if isinstance(entry, tuple):
            return pool[entry[1]]
        return ""

    offset += 2  # access flags
    this_class, super_class = struct.unpack_from(">HH", data, offset)
    offset += 4
    interfaces_count = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    parents = [class_at(struct.unpack_from(">H", data, offset + 2 * position)[0])
               for position in range(interfaces_count)]
    offset += interfaces_count * 2

    def skip_attributes(position: int) -> int:
        number = struct.unpack_from(">H", data, position)[0]
        position += 2
        for _ in range(number):
            length = struct.unpack_from(">I", data, position + 2)[0]
            position += 6 + length
        return position

    def member(position: int) -> tuple[dict, int]:
        flags, name_index, descriptor_index = struct.unpack_from(">HHH", data, position)
        entry = {"flags": flags, "name": pool[name_index], "descriptor": pool[descriptor_index]}
        return entry, skip_attributes(position + 6)

    def members(position: int) -> tuple[list[dict], int]:
        found = []
        number = struct.unpack_from(">H", data, position)[0]
        position += 2
        for _ in range(number):
            entry, position = member(position)
            found.append(entry)
        return found, position

    fields, offset = members(offset)
    methods, offset = members(offset)
    return {"fields": fields, "methods": methods, "name": class_at(this_class),
            "super": class_at(super_class), "interfaces": parents}


def find_member(classes: dict[str, dict], internal: str, name: str, descriptor: str):
    """A member where it is *declared*: `GLFWErrorCallback.free()` is really `Callback.free()`.

    Without walking the chain, 30 of the demanded members look unresolvable and a generator would invent a
    shape for them - which is how the skeleton ended up declaring callbacks with static methods the real
    library has nowhere.
    """
    seen: set[str] = set()
    queue = [internal]
    while queue:
        current = queue.pop(0)
        if current in seen or current not in classes:
            continue
        seen.add(current)
        entry = classes[current]
        for m in entry["methods"]:
            if m["name"] == name and m["descriptor"] == descriptor:
                return m, None, current
        for f in entry["fields"]:
            if f["name"] == name and f["descriptor"] == descriptor:
                return None, f, current
        queue.append(entry.get("super") or "")
        queue.extend(entry.get("interfaces") or [])
    return None, None, None


def load(libs: pathlib.Path) -> dict[str, dict]:
    """Every org.lwjgl class in the client's libraries, by internal name."""
    classes: dict[str, dict] = {}
    import zipfile
    for jar in sorted(libs.glob("*.jar")):
        try:
            with zipfile.ZipFile(jar) as archive:
                for name in archive.namelist():
                    if not name.endswith(".class") or not name.startswith("org/lwjgl/"):
                        continue
                    internal = name[:-6]
                    if internal not in classes:
                        classes[internal] = read_class(archive.read(name))
        except zipfile.BadZipFile:
            continue
    return classes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--libs", required=True)
    parser.add_argument("--surface", required=True)
    parser.add_argument("--check", help="a shim source tree to compare against")
    parser.add_argument("--json")
    args = parser.parse_args(argv)

    classes = load(pathlib.Path(args.libs).expanduser())
    print(f"org.lwjgl classes in the libraries: {len(classes)}")
    if not classes:
        print("no LWJGL jars found: pass the directory the client's libraries are in", file=sys.stderr)
        return 1

    surface = json.loads(pathlib.Path(args.surface).read_text())
    if isinstance(surface, list):
        surface = surface[0]

    shapes = {}
    unresolved = []
    for references in surface["references"].values():
        for reference in references:
            kind, rest = reference.split(":", 1)
            owner, signature = rest.split("#", 1)
            if "(" in signature:
                name, descriptor = signature.split("(", 1)
                descriptor = "(" + descriptor
            elif ":" in signature:
                name, descriptor = signature.split(":", 1)  # a field: name:descriptor
            else:
                name, descriptor = signature, "()V"
            if name in ("<init>", "makeConcatWithConstants", "makeConcat"):
                continue
            internal = owner.replace(".", "/")
            entry = classes.get(internal)
            if entry is None:
                unresolved.append(f"{owner}#{name} (no such class in the libraries)")
                continue
            method, field, declaring = find_member(classes, internal, name, descriptor)
            if method:
                m = method
                shapes[f"{owner}#{name}"] = {
                    "kind": "method", "static": bool(m["flags"] & ACC_STATIC), "descriptor": descriptor,
                    "declared_in": declaring, "inherited": declaring != internal,
                    "returns": java_type(descriptor[descriptor.index(")") + 1:]),
                }
            elif field:
                f = field
                shapes[f"{owner}#{name}"] = {
                    "kind": "field", "static": bool(f["flags"] & ACC_STATIC), "descriptor": descriptor,
                    "declared_in": declaring, "inherited": declaring != internal,
                    "type": java_type(descriptor), "final": bool(f["flags"] & ACC_FINAL),
                }
            else:
                # the name matched but not the descriptor: the surface and the jar disagree, which means the
                # surface read a *different* overload - worth naming rather than silently generating
                near = [m["descriptor"] for m in entry["methods"] if m["name"] == name]
                near += [f["descriptor"] for f in entry["fields"] if f["name"] == name]
                unresolved.append(f"{owner}#{name}{descriptor} (in the jar: {', '.join(near) or 'nothing'})")

    kinds = collections.Counter(entry["kind"] for entry in shapes.values())
    print(f"demanded members resolved against the real jars: {len(shapes)} "
          f"({kinds['method']} methods, {kinds['field']} fields)")
    if unresolved:
        print(f"could not be resolved: {len(unresolved)}")
        for line in unresolved[:8]:
            print(f"  {line}")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(shapes, indent=1, sort_keys=True))

    if args.check:
        root = pathlib.Path(args.check)
        problems = []
        for key, shape in sorted(shapes.items()):
            owner, name = key.split("#")
            path = root / (owner.replace(".", "/") + ".java")
            if not path.is_file():
                problems.append(f"{key}: no file in the shim")
                continue
            text = path.read_text()
            declared_as_method = re_search(text, rf"\b{re.escape(name)}\s*\(")
            declared_as_field = re_search(text, rf"\b{re.escape(name)}\s*(=|;)")
            if shape["kind"] == "field":
                if declared_as_method:
                    problems.append(f"{key}: a method in the shim, really a field")
                elif not declared_as_field:
                    problems.append(f"{key}: field missing from the shim")
            else:
                if declared_as_field and not declared_as_method:
                    problems.append(f"{key}: a field in the shim, really a method")
                elif not declared_as_method:
                    problems.append(f"{key}: method missing from the shim")
                elif shape["static"] and not re_search(text, rf"static[^;{{}}]*\b{re.escape(name)}\s*\("):
                    problems.append(f"{key}: instance in the shim, really static")
        print(f"\nthe shim against the real shapes: {len(problems)} problem(s) out of {len(shapes)} members")
        for line in problems[:25]:
            print(f"  {line}")

    static_methods = sum(1 for s in shapes.values() if s["kind"] == "method" and s["static"])
    print(f"\nmethods called statically: {static_methods}; "
          f"instance methods: {sum(1 for s in shapes.values() if s['kind'] == 'method') - static_methods}; "
          f"fields: {kinds['field']}")
    return 0


def re_search(text: str, pattern: str) -> bool:
    return re.search(pattern, text) is not None


if __name__ == "__main__":
    raise SystemExit(main())
