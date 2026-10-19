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

Output is three TSV files: classes, fields and methods, each "from<TAB>to".
"""

from __future__ import annotations

import argparse
import pathlib
import re


def read_official(path: pathlib.Path):
    """Mojang's ProGuard mapping: official on the left, obfuscated on the right, members indented."""
    classes = {}
    members = []          # (obf_owner, obf_name, official_owner, official_name)
    owner_official = None
    with path.open(errors="replace") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            if not line.startswith(" ") and "->" in line:
                left, right = line.split("->")
                obf = right.strip().rstrip(":")
                owner_official = left.strip()
                classes[obf] = owner_official
                continue
            match = re.match(r"^\s+(?:\d+:\d+:)?[\w.$\[\]/<>]+ (\S+)\(.*?\) -> (\S+)", line)
            if match and owner_official:
                members.append(("m", None, match.group(1), owner_official, match.group(2)))
                continue
            match = re.match(r"^\s+[\w.$\[\]/<>]+ (\S+) -> (\S+)", line)
            if match and owner_official:
                members.append(("f", None, match.group(1), owner_official, match.group(2)))
    return classes, members


def read_intermediary(path: pathlib.Path):
    """Fabric's Tiny v2: `official` first, `intermediary` second, members under each class."""
    classes = {}
    member_rows = []      # (kind, obf_class, obf_member_name, intermediary_class, intermediary_member)
    with path.open(errors="replace") as handle:
        header = handle.readline().split()
        columns = header[3:]
        if "official" not in columns or "intermediary" not in columns:
            raise SystemExit(f"unexpected tiny header: {header}")
        official_at, intermediary_at = columns.index("official"), columns.index("intermediary")
        current = None
        for line in handle:
            if line.startswith("c\t"):
                parts = line.rstrip("\n").split("\t")
                names = parts[1:]
                current = (names[official_at], names[intermediary_at])
                classes[current[0]] = current[1]
            elif line.lstrip("\t").startswith(("f\t", "m\t")) and current:
                # member lines are indented with a tab in Tiny v2: "\tf\t<desc>\tofficial\tintermediary"
                parts = line.lstrip("\t").rstrip("\n").split("\t")
                names = parts[2:]
                member_rows.append((parts[0], current[0], names[official_at], current[1], names[intermediary_at]))
    return classes, member_rows


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

    # obfuscated -> official, by class and by (owner, name) for members
    off_by_member = {}
    for kind, _, official_name, official_owner, obf_member in off_members:
        # keyed by the obfuscated member name, valued by the official one, with one letter for the kind -
        # the first version of this stored the obfuscated name and used M/F here against m/f from the tiny
        # file, which composed 10,201 classes and exactly zero members.
        off_by_member[(kind.lower(), official_owner, obf_member)] = official_name

    classes = {}
    for obf, intermediary in int_classes.items():
        official = off_classes.get(obf)
        if official and intermediary.startswith("net/minecraft/"):
            classes[intermediary] = official

    fields = {}
    methods = {}
    for kind, obf_class, obf_member, intermediary_class, intermediary_member in int_members:
        if not intermediary_class.startswith("net/minecraft/"):
            continue
        official_owner = off_classes.get(obf_class)
        if not official_owner:
            continue
        official_member = off_by_member.get((kind, official_owner, obf_member))
        if not official_member:
            continue
        target = fields if kind == "f" else methods
        target[(intermediary_class, intermediary_member)] = official_member

    out = pathlib.Path(args.out)
    if out.suffix != ".tsv":
        # one naming rule, so a caller can pass a prefix and know where the files land: <prefix>.tsv,
        # <prefix>.fields.tsv, <prefix>.methods.tsv. The remapper accepts either form.
        out = out.with_suffix(".tsv")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(f"{was}\t{now}" for was, now in sorted(classes.items())) + "\n")
    out.with_suffix(".fields.tsv").write_text(
        "\n".join(f"{owner}\t{was}\t{now}" for (owner, was), now in sorted(fields.items())) + "\n")
    out.with_suffix(".methods.tsv").write_text(
        "\n".join(f"{owner}\t{was}\t{now}" for (owner, was), now in sorted(methods.items())) + "\n")
    print(f"intermediary -> official: {len(classes)} class(es), {len(fields)} field(s), {len(methods)} method(s)")
    print(f"  wrote {out}, {out.with_suffix('.fields.tsv')}, {out.with_suffix('.methods.tsv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
