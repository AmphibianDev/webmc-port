#!/usr/bin/env python3
"""Give the shim the real shape of every demanded member.

    python3 tools/lwjgl3_shapes.py --libs work/client/libs/1.21.11 --surface s.json --json shapes.json
    python3 tools/lwjgl3_declare.py --shapes shapes.json --shim lwjgl3-webgl/src/main/java [--apply]

The skeleton was generated from names alone, so it got the shapes wrong wherever the name did not carry one:
`GLCapabilities` grew a static method `GL_ARB_debug_outputZ()` for what is really the instance field
`GL_ARB_debug_output` (the Z is the descriptor), and struct accessors came out static. Those are not calls
Minecraft ever makes in that shape, so they are noise that hides the real work.

This writes what the real LWJGL jar declares, and only where the shim disagrees:

  * a demanded method the shim lacks, or has as a field        -> declared (native, with the real staticness)
  * a demanded field  the shim lacks, or has as a method       -> declared as a final field
  * a member whose name the surface mangled (a field name glued to its descriptor) -> removed

A method it declares with the right shape is never touched, so implementations are never clobbered.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re

PRIMITIVE = {"Z": "boolean", "B": "byte", "C": "char", "S": "short", "I": "int", "J": "long",
             "F": "float", "D": "double", "V": "void"}


def java_type(descriptor: str, resolve: dict[str, str]) -> str:
    if descriptor.startswith("["):
        return java_type(descriptor[1:], resolve) + "[]"
    if descriptor.startswith("L") and descriptor.endswith(";"):
        return descriptor[1:-1].replace("/", ".")
    return PRIMITIVE.get(descriptor, descriptor)


def parameters(descriptor: str, resolve: dict[str, str]) -> str:
    inner = descriptor[1:descriptor.index(")")]
    parts, index = [], 0
    while index < len(inner):
        if inner[index] == "[":
            start = index
            while inner[index] == "[":
                index += 1
            if inner[index] == "L":
                index = inner.index(";", index)
                parts.append(java_type(inner[start:index + 1], resolve))
            else:
                parts.append(java_type(inner[start:index + 1], resolve))
            index += 1
        elif inner[index] == "L":
            end = inner.index(";", index)
            parts.append(java_type(inner[index:end + 1], resolve))
            index = end + 1
        else:
            parts.append(java_type(inner[index], resolve))
            index += 1
    return ", ".join(f"{type_name} arg{position}" for position, type_name in enumerate(parts))


def remove_mangled(text: str, owner: str) -> tuple[str, int]:
    """Drop the bogus members an earlier, shape-blind run invented (`NAMEZ()V`)."""
    removed = 0
    pattern = re.compile(r"\n\s*public static (?:final )?[\w\.\[\]<>]+ ([A-Z][A-Z0-9_]+)Z\(\)"
                         r" \{[^}]*\}")
    def drop(match: re.Match) -> str:
        nonlocal removed
        removed += 1
        return ""
    return pattern.sub(drop, text), removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shapes", required=True)
    parser.add_argument("--shim", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    shapes = json.loads(pathlib.Path(args.shapes).read_text())
    root = pathlib.Path(args.shim)
    resolve: dict[str, str] = {}

    declared = missing_file = ok = wrong_shape = 0
    additions: dict[str, list[str]] = {}
    removals: dict[str, int] = {}
    for key in sorted(shapes):
        shape = shapes[key]
        owner, name = key.split("#")
        path = root / (owner.replace(".", "/") + ".java")
        if not path.is_file():
            missing_file += 1
            continue
        text = path.read_text()
        as_method = re.search(r"\b" + re.escape(name) + r"\s*\(", text) is not None
        as_field = re.search(r"\b" + re.escape(name) + r"\s*[=;]", text) is not None

        if shape["kind"] == "field":
            if as_method:
                wrong_shape += 1
                cleaned, removed = remove_mangled(text, owner)
                if removed:
                    removals[owner] = removals.get(owner, 0) + removed
                    text = cleaned
                    as_method = re.search(r"\b" + re.escape(name) + r"\s*\(", text) is not None
            if as_field and not as_method:
                ok += 1
                continue
            if as_field or as_method:
                ok += 1
                continue
            declared += 1
            type_name = java_type(shape["descriptor"], resolve)
            removals.setdefault(owner, 0)
            additions.setdefault(owner, []).append(
                f"\n    // demanded by the client; createCapabilities() fills this in, as in the real LWJGL\n"
                f"    public {'static ' if shape['static'] else ''}{type_name} {name} = "
                f"{'false' if type_name == 'boolean' else '0'};\n")
            # and drop the method the shape-blind run invented for it: the field name glued to its descriptor
            mangled = re.search(r"\n\s*public static (?:final )?[\w\.\[\]<>]+ "
                                + re.escape(name) + re.escape(shape["descriptor"]) + r"\(\)[^\n]*\n", text)
            if mangled:
                text = text.replace(mangled.group(0), "\n")
                removals[owner] = removals.get(owner, 0) + 1
                path.write_text(text) if not args.apply else None
            continue

        if as_method:
            if shape["static"] and not re.search(r"static[^;{}]*\b" + re.escape(name) + r"\s*\(", text):
                wrong_shape += 1
                additions.setdefault(owner, []).append(
                    f"\n    // the real declaration is static (tools/lwjgl3_shapes.py reads it from the jar)\n"
                    f"    public static native {java_type(shape['returns'], resolve)} {name}"
                    f"({parameters(shape['descriptor'], resolve)});\n")
                continue
            ok += 1
            continue
        if as_field:
            wrong_shape += 1
            continue
        declared += 1
        assert shape["kind"] == "method", shape
        additions.setdefault(owner, []).append(
            f"\n    // demanded by the client; the JavaScript behind it is in natives/webmc-lwjgl3.js\n"
            f"    public {'static ' if shape['static'] else ''}native "
            f"{java_type(shape['returns'], resolve)} {name}({parameters(shape['descriptor'], resolve)});\n")

    print(f"members already in the right shape: {ok}")
    print(f"members declared now: {declared}")
    print(f"wrong shape, needs attention: {wrong_shape}")
    if missing_file:
        print(f"classes with no file: {missing_file}")

    if args.apply:
        for owner, lines in additions.items():
            path = root / (owner.replace(".", "/") + ".java")
            text = path.read_text().rstrip()
            if not text.endswith("}"):
                print(f"  cannot append to {path}: it does not end with a brace")
                continue
            path.write_text(text[:-1].rstrip("\n") + "\n" + "".join(lines) + "}\n")
        for owner, count in removals.items():
            path = root / (owner.replace(".", "/") + ".java")
            text, removed = remove_mangled(path.read_text(), owner)
            path.write_text(text)
        print(f"applied: {len(additions)} file(s) gained members, {len(removals)} lost mangled ones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
