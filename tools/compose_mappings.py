#!/usr/bin/env python3
"""intermediary -> official, composed from the two mapping files that already exist.

    python3 tools/compose_mappings.py --official work/mappings/client.txt \
        --intermediary work/mappings/intermediary/mappings/mappings.tiny --out work/mappings/int-to-official.tsv

Why: a Fabric mod is compiled against **intermediary** names (`net/minecraft/class_2561`) because that is
what Fabric Loader hands it at play time. A compiled client in this project is remapped to **Mojang's
official** names (`net/minecraft/network/chat/Component`) by the engine's own patch chain. Nothing bridged
the two, so baking Meteor into a compiled client produced references the compiler could not resolve - the
build ended with "Class net.minecraft.class_2561 was not found" and a gap report, which is exactly what
this fixes.

Neither file on its own can do it. Mojang's client.txt says official <-> obfuscated; Fabric's
mappings.tiny says obfuscated <-> intermediary. Composing them gives intermediary <-> official, which is
the mapping a mod needs.

Members are joined on their **descriptor**, not just their name. That is not a detail: obfuscated names
repeat (bridge methods, overrides), so a name-only join silently binds an intermediary name to whichever
official member happened to be read last, and the compiler then reports a method that does not exist on
the class - `Method net.minecraft.util.Tuple.setA() was not found` was the first of those.

Output is three TSV files: classes, fields and methods, each "from<TAB>to".
"""

from __future__ import annotations

import argparse
import pathlib
import re


PRIMITIVES = {"void": "V", "boolean": "Z", "byte": "B", "char": "C", "short": "S", "int": "I", "long": "J",
              "float": "F", "double": "D"}


def type_descriptor(source: str) -> str:
    """`java.lang.String[]` -> `[Ljava/lang/String;`, `int` -> `I`, generics dropped."""
    source = re.sub(r"<[^<>]*>", "", source).strip()      # List<String> is List in a descriptor
    arrays = source.count("[]")
    source = source.replace("[]", "").strip()
    if source in PRIMITIVES:
        return "[" * arrays + PRIMITIVES[source]
    return "[" * arrays + "L" + source.replace(".", "/") + ";"


def method_descriptor(signature: str) -> str:
    """`9:10:void youJustLostTheGame(int,java.lang.String)` -> `(ILjava/lang/String;)V`."""
    signature = re.sub(r"^\d+:\d+:", "", signature.strip())
    returns, arguments = signature.split(" ", 1)
    inside = arguments[arguments.index("(") + 1:arguments.rindex(")")]
    arguments = "".join(type_descriptor(part) for part in inside.split(",") if part.strip())
    return "(" + arguments + ")" + type_descriptor(returns)


def official_descriptor(descriptor: str, official_classes: dict) -> str:
    """Rewrite a descriptor's class names from obfuscated to official: `La;` -> `Lcom/mojang/math/Axis;`.

    The two files disagree on the namespace of their type fields - Fabric's descriptor says `La;` because
    its first column is the obfuscated game, Mojang's signature says the source name - and comparing them
    unreconciled threw away 56,840 members ("same arity, different descriptor"), which would have left the
    compiler with exactly the unresolved references this whole exercise is meant to remove.
    """
    out = []
    index = 0
    while index < len(descriptor):
        character = descriptor[index]
        if character == "L":
            end = descriptor.find(";", index)
            if end == -1:
                return descriptor
            name = descriptor[index + 1:end]
            out.append("L" + official_classes.get(name, name) + ";")
            index = end + 1
        else:
            out.append(character)
            index += 1
    return "".join(out)


def rename_in_descriptor(descriptor: str, names: dict) -> str:
    """Rewrite the class names inside a descriptor through a mapping, leaving primitives and arrays alone."""
    out = []
    index = 0
    while index < len(descriptor):
        character = descriptor[index]
        if character == "L":
            end = descriptor.find(";", index)
            if end == -1:
                return descriptor
            out.append("L" + names.get(descriptor[index + 1:end], descriptor[index + 1:end]) + ";")
            index = end + 1
        else:
            out.append(character)
            index += 1
    return "".join(out)


def read_official(path: pathlib.Path):
    """Mojang's ProGuard mapping: official on the left, obfuscated on the right, members indented.

    Returns (classes, members) with members as (kind, obf_class, obf_member, official_name, descriptor).
    """
    classes = {}
    members = []
    official_owner = obf_owner = None
    with path.open(errors="replace") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            if not line.startswith(" ") and "->" in line:
                left, right = line.split("->")
                obf_owner = right.strip().rstrip(":")
                official_owner = left.strip()
                classes[obf_owner] = official_owner
                continue
            if official_owner is None:
                continue
            # 9:10:java.lang.Object getA() -> a   (the line numbers are optional)
            match = re.match(r"^\s+(\d+:\d+:)?(.+\(.*?\))\s*->\s*(\S+)\s*$", line)
            if match:
                # the left side is "return type name(args)", and the name is what goes in the table: an
                # earlier rewrite captured the whole signature there, which made every mapped method a
                # string like "21:21:java.lang.Object getB()"
                signature = match.group(2)
                name = re.search(r"([^\s(]+)\s*\(", signature).group(1)
                members.append(("m", obf_owner, match.group(3), name, method_descriptor(signature)))
                continue
            match = re.match(r"^\s+(?:\d+:\d+:)?(\S+)\s+(\S+)\s*->\s*(\S+)\s*$", line)
            if match:
                members.append(("f", obf_owner, match.group(3), match.group(2), type_descriptor(match.group(1))))
    return classes, members


def read_intermediary(path: pathlib.Path):
    """Fabric's Tiny v2: `official` first, `intermediary` second, members under each class.

    Returns (classes, members) with members as
    (kind, obf_class, obf_member, intermediary_class, intermediary_member, descriptor).
    """
    classes = {}
    members = []
    with path.open(errors="replace") as handle:
        header = handle.readline().split()
        columns = header[3:]
        if "official" not in columns or "intermediary" not in columns:
            raise SystemExit(f"unexpected tiny header: {header}")
        official_at, intermediary_at = columns.index("official"), columns.index("intermediary")
        obf_class = intermediary_class = None
        for line in handle:
            if line.startswith("c\t"):
                parts = line.rstrip("\n").split("\t")
                names = parts[1:]
                obf_class, intermediary_class = names[official_at], names[intermediary_at]
                classes[obf_class] = intermediary_class
            elif line.lstrip("\t").startswith(("f\t", "m\t")) and obf_class:
                # "\tf\t<descriptor>\tofficial\tintermediary" - the descriptor is the first field, which
                # the earlier version of this dropped, and dropping it is what let overloads collide
                parts = line.lstrip("\t").rstrip("\n").split("\t")
                names = parts[2:]
                members.append((parts[0], obf_class, names[official_at], intermediary_class,
                                names[intermediary_at], parts[1]))
    return classes, members


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--official", required=True)
    parser.add_argument("--intermediary", required=True)
    parser.add_argument("--out", required=True, help="prefix for the three tsv files")
    args = parser.parse_args(argv)

    off_classes, off_members = read_official(pathlib.Path(args.official))
    int_classes, int_members = read_intermediary(pathlib.Path(args.intermediary))
    print(f"official mapping: {len(off_classes)} class(es), {len(off_members)} member line(s)")
    print(f"intermediary mapping: {len(int_classes)} class(es), {len(int_members)} member line(s)")

    # (kind, obf class, obf member, descriptor) -> official name. The descriptor is in the key because
    # obfuscated names repeat, and a key without it produced mappings that pointed at a sibling overload.
    # obfuscated class names, as they appear inside the tiny file's descriptors, to official ones
    obf_to_official_class = {}
    for obf, official in off_classes.items():
        obf_to_official_class[obf] = official.replace(".", "/")
    for obf, intermediary in int_classes.items():
        official = off_classes.get(obf)
        if official:
            obf_to_official_class.setdefault(intermediary, official.replace(".", "/"))

    official_by_member = {}
    by_name = {}
    for kind, obf_class, obf_member, official_name, descriptor in off_members:
        official_by_member.setdefault((kind, obf_class, obf_member, descriptor), official_name)
        by_name.setdefault((kind, obf_class, obf_member), set()).add(official_name)
    unique_by_name = {key: next(iter(names)) for key, names in by_name.items() if len(names) == 1}

    classes = {}
    for obf, intermediary in int_classes.items():
        official = off_classes.get(obf)
        if official and official.replace(".", "/") != intermediary:
            # internal names, with slashes: the remapper puts this value straight into a class reference,
            # and Mojang's file spells classes the source way, so a dotted value produced a Meteor jar whose
            # every call was to "net.minecraft.client.Minecraft" - a name that does not resolve as a member
            # owner, which is what the compiler then complained about.
            classes[intermediary] = official.replace(".", "/")

    # the mod jar's own descriptors: a mod compiled against intermediary has "Lnet/minecraft/class_310;"
    # in its descriptors, while the tiny file writes them in its first column's namespace ("La;"), so the
    # table keys have to be built on the intermediary form
    obf_to_intermediary = {obf: intermediary for obf, intermediary in int_classes.items()}

    fields = {}
    methods = {}
    fields_by_descriptor = {}
    methods_by_descriptor = {}
    unnamed = 0
    ambiguous_name = 0
    for kind, obf_class, obf_member, intermediary_class, intermediary_member, descriptor in int_members:
        # the descriptor first, and only where that leaves nothing, a name that stands for exactly one
        # official member. A name shared by several is skipped on purpose: guessing is what produced
        # "Method net.minecraft.util.Tuple.setA() was not found" in the compiler.
        official_name = official_by_member.get(
            (kind, obf_class, obf_member, official_descriptor(descriptor, obf_to_official_class)))
        if not official_name:
            official_name = unique_by_name.get((kind, obf_class, obf_member))
            if official_name:
                ambiguous_name += 1
        if not official_name:
            if (kind, obf_class, obf_member) in by_name:
                unnamed += 1
            continue
        intermediary_descriptor = rename_in_descriptor(descriptor, obf_to_intermediary)
        (fields if kind == "f" else methods)[(intermediary_class, intermediary_member)] = official_name
        (fields_by_descriptor if kind == "f" else methods_by_descriptor)[
            (intermediary_class, intermediary_member, intermediary_descriptor)] = official_name

    out = pathlib.Path(args.out)
    if out.suffix != ".tsv":
        # one naming rule, so a caller can pass a prefix and know where the files land: <prefix>.tsv,
        # <prefix>.fields.tsv, <prefix>.methods.tsv. The remapper accepts either form.
        out = out.with_suffix(".tsv")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(f"{was}\t{now}" for was, now in sorted(classes.items())) + "\n")
    # members are written with their descriptor when one is known: obfuscated names repeat inside a class
    # (Tuple's field, getA and setA are all "a"), so a name-only key can bind the wrong overload, and the
    # remapper prefers the four-column form for exactly that reason
    out.with_suffix(".fields.tsv").write_text(
        "\n".join(f"{owner}\t{was}\t{descriptor}\t{now}" for (owner, was, descriptor), now
                   in sorted(fields_by_descriptor.items()))
        + "\n" + "\n".join(f"{owner}\t{was}\t{now}" for (owner, was), now in sorted(fields.items())
                             if (owner, was, rename_in_descriptor("", {})) or True) + "\n")
    out.with_suffix(".methods.tsv").write_text(
        "\n".join(f"{owner}\t{was}\t{descriptor}\t{now}" for (owner, was, descriptor), now
                   in sorted(methods_by_descriptor.items()))
        + "\n" + "\n".join(f"{owner}\t{was}\t{now}" for (owner, was), now in sorted(methods.items())) + "\n")
    print(f"intermediary -> official: {len(classes)} class(es), {len(fields)} field(s), {len(methods)} method(s)"
          + (f", {ambiguous_name} by name alone" if ambiguous_name else "")
          + (f", {unnamed} ambiguous and left out" if unnamed else ""))
    print(f"  wrote {out}, {out.with_suffix('.fields.tsv')}, {out.with_suffix('.methods.tsv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
