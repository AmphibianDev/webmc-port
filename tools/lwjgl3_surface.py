#!/usr/bin/env python3
"""What exactly does a 1.13+ client demand of LWJGL 3?

    python3 tools/lwjgl3_surface.py --version 1.20.4 --out work/surface-1.20.4.json
    python3 tools/lwjgl3_surface.py --jars work/client/1.13.2.jar,work/client/1.20.4.jar --compare

The LWJGL 3 API is enormous (thousands of generated bindings). What has to be reimplemented, however,
is not the API: it is the subset the client actually calls, plus whatever the JVM resolves while
loading. This reads that subset out of the client jar itself, by walking each class file's constant
pool for references to org/lwjgl - no JDK, no javap, no guessing - and prints it as the work list.

Two numbers matter: how many classes are touched, and how many distinct methods/fields. Those are the
spec of the shim, and they shrink the problem from "implement LWJGL" to "implement this list".
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import struct
import sys
import urllib.request
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import versions as version_tools  # noqa: E402

# The groups the shim has to provide, in the order a client hits them at boot.
GROUPS = {
    "system": ("org/lwjgl/system/", "memory, function pointers, JNI plumbing"),
    "glfw": ("org/lwjgl/glfw/", "window, input, monitor, cursor"),
    "opengl": ("org/lwjgl/opengl/", "rendering"),
    "openal": ("org/lwjgl/openal/", "sound"),
    "stb": ("org/lwjgl/stb/", "image decoding, font packing"),
    "nanovg": ("org/lwjgl/nanovg/", "loading screens in newer versions"),
    "other": ("org/lwjgl/", "everything else"),
}


def group_of(internal: str) -> str:
    for name, (prefix, _) in GROUPS.items():
        if name != "other" and internal.startswith(prefix):
            return name
    return "other"


def read_constant_pool(data: bytes) -> list:
    """The constant pool of one class file: enough for references, nothing more."""
    if len(data) < 10 or data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("not a class file")
    count = struct.unpack_from(">H", data, 8)[0]
    offset, pool = 10, [None] * count
    index = 1
    while index < count:
        tag = data[offset]
        offset += 1
        if tag == 1:  # Utf8
            length = struct.unpack_from(">H", data, offset)[0]
            offset += 2
            pool[index] = data[offset:offset + length].decode("utf-8", "replace")
            offset += length
        elif tag in (3, 4):  # Integer, Float
            offset += 4
        elif tag in (5, 6):  # Long, Double take two slots
            offset += 8
            index += 1
        elif tag in (7, 8, 16, 19, 20):  # Class, String, MethodType, Module, Package
            pool[index] = struct.unpack_from(">H", data, offset)[0]
            offset += 2
        elif tag in (9, 10, 11, 17, 18):  # refs: class + NameAndType
            # the tag is kept, not just the pair: without it a field reference and a method reference are
            # indistinguishable, and this file labelled every one of them a method (a field read arrived as
            # a call to `GL_ARB_buffer_storageZ`, Z being the field descriptor). A shim generated from that
            # list declares methods where the client reads fields, and the client dies with NoSuchFieldError.
            pool[index] = (tag, *struct.unpack_from(">HH", data, offset))
            offset += 4
        elif tag == 12:  # NameAndType
            pool[index] = struct.unpack_from(">HH", data, offset)
            offset += 4
        elif tag == 15:  # MethodHandle
            offset += 3
        else:
            raise ValueError(f"unknown constant pool tag {tag}")
        index += 1
    return pool


def utf8(pool: list, index: int) -> str:
    value = pool[index]
    return value if isinstance(value, str) else ""


def class_name(pool: list, index: int) -> str:
    entry = pool[index]
    if isinstance(entry, int):
        return utf8(pool, entry)
    return ""


def references(data: bytes) -> set[str]:
    """Every org/lwjgl method and field this class file refers to."""
    pool = read_constant_pool(data)
    found: set[str] = set()
    for entry in pool:
        if not isinstance(entry, tuple) or len(entry) != 3:
            continue
        tag, class_index, name_and_type = entry
        owner = class_name(pool, class_index)
        if not owner.startswith("org/lwjgl"):
            continue
        name_type = pool[name_and_type] if name_and_type < len(pool) else None
        if not isinstance(name_type, tuple):
            continue
        name, descriptor = utf8(pool, name_type[0]), utf8(pool, name_type[1])
        if name:
            # 9 Fieldref and 17 Dynamic are reads of a field; 10 Methodref, 11 InterfaceMethodref and
            # 18 InvokeDynamic call one.
            #
            # A field is written `name:descriptor`, a method `name(descriptor)`: for a method the descriptor
            # starts with a bracket so the two never ran together, but for a field they did - `GL_ARB_debug_
            # output` and its `Z` became the member "GL_ARB_debug_outputZ", which is not a name that exists
            # anywhere, and a skeleton built from it invented a static method for what is really a field.
            if tag in (9, 17):
                found.add(f"F:{owner.replace('/', '.')}#{name}:{descriptor}")
            else:
                found.add(f"M:{owner.replace('.', '.').replace('/', '.')}#{name}{descriptor}")
    return found


def scan(jar: pathlib.Path) -> dict:
    """The LWJGL 3 surface of one client jar."""
    methods: dict[str, set[str]] = collections.defaultdict(set)
    classes: set[str] = set()
    errors = 0
    with zipfile.ZipFile(jar) as archive:
        for name in archive.namelist():
            if not name.endswith(".class"):
                continue
            try:
                for reference in references(archive.read(name)):
                    owner = reference.split("#", 1)[0].split(":", 1)[-1].split(":", 1)[-1]
                    classes.add(owner)
                    methods[group_of(owner.replace(".", "/"))].add(reference)
            except Exception:  # noqa: BLE001 - one odd class file must not stop the scan
                errors += 1
    return {
        "jar": jar.name,
        "classes": sorted(classes),
        "references": {group: sorted(items) for group, items in sorted(methods.items())},
        "counts": {group: len(items) for group, items in sorted(methods.items())},
        "class_count": len(classes),
        "reference_count": sum(len(items) for items in methods.values()),
        "unreadable_classes": errors,
    }


def download_client(version_id: str, target: pathlib.Path) -> pathlib.Path:
    """The official client jar, straight from Mojang, cached in work/."""
    if target.is_file() and target.stat().st_size > 1_000_000:
        return target
    manifest = version_tools.load_manifest(version_tools.MANIFEST_URL)
    entry = version_tools.resolve(manifest, version_id)
    request = urllib.request.Request(entry["url"], headers={"User-Agent": "webmc-port/1.0 (+surface)"})
    with urllib.request.urlopen(request, timeout=120) as response:
        document = json.loads(response.read().decode("utf-8"))
    url = (document.get("downloads") or {}).get("client", {}).get("url")
    if not url:
        raise SystemExit(f"{version_id}: no client jar in Mojang's metadata")
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "webmc-port/1.0 (+surface)"})
    with urllib.request.urlopen(request, timeout=600) as response, target.open("wb") as handle:
        while chunk := response.read(1 << 20):
            handle.write(chunk)
    return target


def report(result: dict, detail: int = 12) -> str:
    lines = [f"{result['jar']}: {result['class_count']} LWJGL classes, "
             f"{result['reference_count']} distinct members"]
    for group, count in result["counts"].items():
        if not count:
            continue
        what = GROUPS.get(group, ("", ""))[1]
        lines.append(f"  {group:<9} {count:>5}  ({what})")
        for reference in result["references"][group][: int(count > 0) * 0 and 0]:
            lines.append(f"      {reference}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.20.4", help="a version whose client to scan")
    parser.add_argument("--jars", help="comma separated jars to scan instead")
    parser.add_argument("--work", default=str(ROOT / "work"))
    parser.add_argument("--out", help="write the full list here")
    parser.add_argument("--compare", action="store_true", help="print the group counts side by side")
    parser.add_argument("--detail", type=int, default=0, help="how many members to print per group")
    args = parser.parse_args(argv)

    if args.jars:
        jars = [pathlib.Path(p) for p in args.jars.split(",")]
    else:
        jars = [download_client(args.version, pathlib.Path(args.work) / "client" / f"{args.version}.jar")]

    results = []
    for jar in jars:
        result = scan(jar)
        results.append(result)
        print(report(result, args.detail))
        print()
        if args.detail:
            for group, items in result["references"].items():
                if not items:
                    continue
                print(f"  {group}: {len(items)} members, first {args.detail}:")
                for reference in items[: args.detail]:
                    print(f"      {reference}")
            print()

    if args.compare and len(results) > 1 and not args.detail:
        groups = sorted({g for r in results for g in r["counts"]})
        print(f"  {'group':<9} " + " ".join(f"{pathlib.Path(r['jar']).stem:>10}" for r in results))
        for group in groups:
            row = " ".join(f"{r['counts'].get(group, 0):>10}" for r in results)
            print(f"  {group:<9} {row}")

    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=1) + "\n")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
