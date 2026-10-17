#!/usr/bin/env python3
"""What would an LWJGL 3 layer in JavaScript have to implement?

    python3 tools/lwjgl_surface.py --client work/client/client.jar --lwjgl work/lwjgl --json

The "real Java" route for Minecraft 1.13 to 1.20.4 is blocked by exactly one thing: their
windowing and graphics go through LWJGL 3, and there is no JavaScript implementation of its
native methods (LWJGL 2, which 1.12.2 and older use, has one: cheerpj-natives, plus WebForge's).
This tool measures that gap for a given client instead of guessing at it:

  * which LWJGL packages and classes the client actually references,
  * which `native` methods those classes declare in the LWJGL jars (the JNI surface a JS layer
    must provide), grouped by module,
  * how much of that is already covered by a JavaScript implementation, if one is present.

It reads class files directly (constant pool + method flags), so it needs no JDK and no network;
either the client jar or the LWJGL jars may be missing, and it says what it could not read.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import struct
import sys
import zipfile

UTF8, INTEGER, FLOAT, LONG, DOUBLE = 1, 3, 4, 5, 6
CLASS, STRING = 7, 8
FIELDREF, METHODREF, INTERFACEMETHODREF = 9, 10, 11
NAMEANDTYPE, METHODHANDLE, METHODTYPE, DYNAMIC, INVOKEDYNAMIC = 12, 15, 16, 17, 18
MODULE, PACKAGE = 19, 20
ACC_NATIVE = 0x0100

MODULES = {
    "org/lwjgl/glfw": "glfw (windowing, input, monitors)",
    "org/lwjgl/opengl": "opengl (the GL entry points)",
    "org/lwjgl/openal": "openal (audio)",
    "org/lwjgl/stb": "stb (images and fonts)",
    "org/lwjgl/nanovg": "nanovg (vector UI)",
    "org/lwjgl/egl": "egl (context creation)",
    "org/lwjgl/jemalloc": "jemalloc (memory)",
    "org/lwjgl/lmdb": "lmdb (storage)",
    "org/lwjgl/tinyfd": "tinyfd (file dialogs)",
    "org/lwjgl/system": "system (plumbing every module uses)",
}


def module_of(class_name: str) -> str:
    for prefix, label in MODULES.items():
        if class_name.startswith(prefix):
            return label
    return "other org/lwjgl"


class ClassFile:
    """Only what this needs: the classes it mentions and its methods' flags."""

    def __init__(self, data: bytes):
        self.data = data
        self.utf8: dict[int, str] = {}
        self.classes: dict[int, int] = {}
        self._read_pool()

    def _read_pool(self) -> None:
        data = self.data
        if data[:4] != b"\xca\xfe\xba\xbe":
            raise ValueError("not a class file")
        count = struct.unpack(">H", data[8:10])[0]
        index, cursor = 1, 10
        while index < count:
            tag = data[cursor]
            cursor += 1
            if tag == UTF8:
                length = struct.unpack(">H", data[cursor:cursor + 2])[0]
                self.utf8[index] = data[cursor + 2:cursor + 2 + length].decode("utf-8", "replace")
                cursor += 2 + length
            elif tag in (INTEGER, FLOAT, FIELDREF, METHODREF, INTERFACEMETHODREF, NAMEANDTYPE,
                         DYNAMIC, INVOKEDYNAMIC):
                cursor += 4
            elif tag in (CLASS, STRING, METHODTYPE, MODULE, PACKAGE):
                value = struct.unpack(">H", data[cursor:cursor + 2])[0]
                cursor += 2
                if tag == CLASS:
                    self.classes[index] = value
            elif tag == METHODHANDLE:
                cursor += 3
            elif tag in (LONG, DOUBLE):
                cursor += 8
                index += 1
            else:
                raise ValueError(f"unknown constant pool tag {tag}")
            index += 1
        self.cursor = cursor

    def referenced(self) -> set[str]:
        return {self.utf8.get(self.classes.get(index, 0), "") for index in self.classes} - {""}

    def native_methods(self) -> list[str]:
        """Method names declared `native`: the methods a JS implementation has to provide."""
        data = self.data
        cursor = self.cursor + 6                      # access_flags, this_class, super_class
        interfaces = struct.unpack(">H", data[cursor:cursor + 2])[0]
        cursor += 2 + 2 * interfaces
        found: list[str] = []
        for _ in range(2):                            # fields, then methods
            count = struct.unpack(">H", data[cursor:cursor + 2])[0]
            cursor += 2
            for _ in range(count):
                flags, name_index, _descriptor = struct.unpack(">HHH", data[cursor:cursor + 6])
                name = self.utf8.get(name_index, "")
                cursor += 6
                attributes = struct.unpack(">H", data[cursor:cursor + 2])[0]
                cursor += 2
                for _ in range(attributes):
                    cursor += 2
                    length = struct.unpack(">I", data[cursor:cursor + 4])[0]
                    cursor += 4 + length
                if flags & ACC_NATIVE:
                    found.append(name)
        return found


def scan_jar(path: pathlib.Path, interesting) -> dict:
    """Read every class in a jar and hand each one to the caller's predicate."""
    summary = {"path": str(path), "classes": 0, "matches": 0, "unreadable": 0, "items": {}}
    if not path.is_file():
        summary["missing"] = True
        return summary
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".class"):
                continue
            summary["classes"] += 1
            try:
                parsed = ClassFile(archive.read(name))
            except Exception:  # noqa: BLE001
                summary["unreadable"] += 1
                continue
            for item in interesting(name, parsed):
                summary["matches"] += 1
                summary["items"][item] = summary["items"].get(item, 0) + 1
    return summary


def client_references(client: pathlib.Path) -> dict:
    """Which LWJGL classes a client jar mentions, by module."""
    def interesting(_name, parsed):
        return [c for c in parsed.referenced() if c.startswith("org/lwjgl/")]
    summary = scan_jar(client, interesting)
    by_module = collections.Counter()
    for item, hits in summary["items"].items():
        by_module[module_of(item)] += hits      # references, not distinct classes: a class used
                                                # in twenty places is twenty places to keep working
    summary["modules"] = dict(by_module.most_common())
    return summary


def lwjgl_natives(lwjgl_dir: pathlib.Path, referenced: set[str]) -> dict:
    """The native methods declared by the LWJGL classes the client uses."""
    modules: dict[str, dict] = collections.defaultdict(lambda: {"classes": 0, "methods": [], "files": []})
    jars = sorted(lwjgl_dir.glob("*.jar")) if lwjgl_dir.is_dir() else []
    for jar in jars:
        with zipfile.ZipFile(jar) as archive:
            for name in archive.namelist():
                if not name.endswith(".class"):
                    continue
                class_name = name[:-6]
                if class_name not in referenced:
                    continue
                try:
                    methods = ClassFile(archive.read(name)).native_methods()
                except Exception:  # noqa: BLE001
                    continue
                module = module_of(class_name)
                modules[module]["classes"] += 1
                modules[module]["methods"].extend(methods)
                modules[module]["files"].append(jar.name)
    out = {}
    for module, data in modules.items():
        data["methods"] = sorted(set(data["methods"]))
        data["files"] = sorted(set(data["files"]))
        data["count"] = len(data["methods"])
        out[module] = data
    return {"jars": [j.name for j in jars], "modules": out,
            "total": sum(data["count"] for data in out.values())}


def coverage(js_dir: pathlib.Path | None) -> dict:
    """Which native methods an existing JavaScript layer already implements, if we have one."""
    if not js_dir or not js_dir.is_dir():
        return {"checked": False}
    implemented = set()
    for path in js_dir.rglob("*.js"):
        text = path.read_text(errors="replace")
        for name in __import__("re").findall(r"\bJava_[A-Za-z0-9_]+", text):
            implemented.add(name)
    return {"checked": True, "dir": str(js_dir), "symbols": len(implemented),
            "sample": sorted(implemented)[:5]}


def report(client: dict, natives: dict, js: dict) -> str:
    lines = ["# LWJGL surface for this client", ""]
    if client.get("missing"):
        lines.append(f"- client jar: **missing** ({client['path']})")
    else:
        lines.append(f"- client jar: `{client['path']}` — {client['classes']} classes, "
                     f"{client['matches']} references to `org.lwjgl.*`")
        for module, hits in (client.get("modules") or {}).items():
            lines.append(f"  - {module}: {hits} class reference(s)")
    lines += ["", "## What a JavaScript layer would have to implement", ""]
    if not natives.get("modules"):
        lines.append("_No LWJGL jars were read, so the native surface is not known yet. Point "
                     "`--lwjgl` at the LWJGL 3 jars for this version (Maven, BSD licensed)._")
    else:
        lines.append("| module | classes used | native methods |")
        lines.append("| --- | --- | --- |")
        for module, data in sorted(natives["modules"].items(), key=lambda kv: -kv[1]["count"]):
            lines.append(f"| {module} | {data['classes']} | **{data['count']}** |")
        lines += ["", f"**total: {natives['total']} native methods** across "
                      f"{len(natives['modules'])} modules, from {', '.join(natives['jars']) or 'no jars'}"]
        lines += ["", "The GLFW and OpenGL rows are the bulk and are mechanical: most OpenGL entry "
                      "points map onto WebGL 2 calls, and the GLFW row is window, input and monitor "
                      "plumbing. OpenAL and STB map onto Web Audio and canvas decoding."]
    if js.get("checked"):
        lines += ["", f"## Existing JavaScript layer", "",
                  f"`{js['dir']}` exports {js['symbols']} `Java_*` symbols (for example "
                  f"{', '.join(js['sample'])}). Compare those names against the table above: "
                  "anything missing is the work."]
    else:
        lines += ["", "## Existing JavaScript layer", "",
                  "_None was given. Today: cheerpj-natives implements part of LWJGL 2 (Java 8 era, "
                  "used up to 1.12.2); nothing public implements LWJGL 3._"]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the LWJGL 3 gap for one client")
    parser.add_argument("--client", default=None, help="a client jar (fetched by tools/acquire.py)")
    parser.add_argument("--lwjgl", default=None, help="folder of LWJGL jars for that version")
    parser.add_argument("--js", default=None, help="a JavaScript natives folder to compare against")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", default=None, help="write the report here")
    args = parser.parse_args()

    client = client_references(pathlib.Path(args.client)) if args.client else {"missing": True, "path": ""}
    referenced = set(client.get("items") or {})
    natives = lwjgl_natives(pathlib.Path(args.lwjgl), referenced) if args.lwjgl else {"modules": {}, "total": 0,
                                                                                      "jars": []}
    js = coverage(pathlib.Path(args.js)) if args.js else {"checked": False}

    if args.json:
        print(json.dumps({"client": client, "natives": natives, "javascript": js}, indent=2))
    else:
        text = report(client, natives, js)
        print(text)
        if args.markdown:
            pathlib.Path(args.markdown).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
