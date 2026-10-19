#!/usr/bin/env python3
"""Build a jar of stubs for the optional dependencies a mod set expects but the client does not carry.

    python3 tools/build_optional_stubs.py --scan /tmp/mods --prefix baritone --out /tmp/mods/webmc-stubs.jar

A mod may treat another mod as optional and still be compiled against its API: Meteor Client ships a
Baritone integration, so its classes name `baritone.api.IBaritone` and friends, which are simply not there
unless Baritone is installed. Fabric Loader tolerates that at play time (the class is only loaded if
something touches it), but a browser client is *compiled whole*, and the compiler stops at the first
reference it cannot resolve - which is how a build ends with "Class baritone.api.IBaritone was not found".

So the shapes are generated here: every type, method and field the mod set references under the named
packages, read out of the mods' own bytecode, compiled into a jar and put on the classpath. The stubs
answer with the defaults and are never asked to do anything real, because the mod that would drive them
is not installed.

Nothing is committed: this runs in the build, against whatever jars that build has.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_loader  # noqa: E402 - the JDK finder that already knows about macOS stubs

# methods that already exist on Object: a stub interface that declares them does not compile ("getClass()
# in IBaritoneProcess cannot override getClass() in Object - overridden method is final")
OBJECT_METHODS = {"getClass", "hashCode", "equals", "toString", "clone", "finalize", "wait", "notify",
                  "notifyAll", "registerNatives"}

PRIMITIVES = {"V": "void", "Z": "boolean", "B": "byte", "C": "char", "S": "short", "I": "int", "J": "long",
              "F": "float", "D": "double"}


def java_type(descriptor: str) -> str:
    """A descriptor, as a Java source type. Generics are dropped and nested classes use a dot."""
    arrays = 0
    while descriptor.startswith("["):
        arrays += 1
        descriptor = descriptor[1:]
    if descriptor in PRIMITIVES:
        name = PRIMITIVES[descriptor]
    elif descriptor.startswith("L") and descriptor.endswith(";"):
        name = descriptor[1:-1].replace("$", ".").replace("/", ".")
    else:
        name = "java.lang.Object"
    return name + "[]" * arrays


def default_value(descriptor: str) -> str:
    if descriptor.startswith("["):
        return "null"
    return {"V": "", "Z": "false", "B": "(byte) 0", "C": "(char) 0", "S": "(short) 0", "I": "0",
            "J": "0L", "F": "0f", "D": "0d"}.get(descriptor, "null")


class Shapes:
    """What the mod set wants from the optional packages."""

    def __init__(self, prefixes):
        self.prefixes = prefixes
        self.types = {}          # internal name -> {"interface": bool, "methods": set, "fields": set,
                                 #                  "constructors": set, "implemented_by": set}
        self.implemented = set()  # types some mod class inherits from, which must be interfaces

    def wanted(self, name: str) -> bool:
        return any(name.startswith(prefix) for prefix in self.prefixes)

    def note_type(self, name: str, is_interface: bool = False) -> dict:
        if name not in self.types:
            self.types[name] = {"interface": is_interface, "methods": set(), "fields": set(),
                                "constructors": set()}
        if is_interface:
            self.types[name]["interface"] = True
        return self.types[name]

    def note_method(self, owner: str, name: str, descriptor: str) -> None:
        entry = self.note_type(owner)
        if name == "<clinit>":
            return
        if name == "<init>":
            # a constructor is not a method: emitting it as one is a syntax error, which is how the first
            # run of this failed with "'(' expected" seventeen times
            entry["constructors"].add(descriptor)
            for parameter in re.findall(r"L([^;]+);", descriptor):
                if self.wanted(parameter):
                    self.note_type(parameter)
            return
        entry["methods"].add((name, descriptor))
        for parameter in re.findall(r"L([^;]+);", descriptor):
            if self.wanted(parameter):
                self.note_type(parameter)

    def note_field(self, owner: str, name: str, descriptor: str) -> None:
        self.note_type(owner)["fields"].add((name, descriptor))
        for parameter in re.findall(r"L([^;]+);", descriptor):
            if self.wanted(parameter):
                self.note_type(parameter)

    def note_descriptor(self, descriptor: str) -> None:
        for name in re.findall(r"L([^;]+);", descriptor):
            if self.wanted(name):
                self.note_type(name)


def sources_for(shapes: Shapes, seed_methods: dict) -> dict:
    """One Java file per *outer* type, with inner types nested inside it.

    Nesting matters: `baritone.api.Settings$Setting` has to be a class inside `Settings`, because the mods
    refer to it as `baritone.api.Settings.Setting`. Emitting it as a top-level `Setting`, or as a file named
    `class Settings.Setting`, does not compile - both were tried.
    """
    kinds = {}
    for name in shapes.types:
        kinds[name] = "interface" if (shapes.types[name]["interface"] or name in shapes.implemented) else "class"

    def members(name: str) -> list:
        shape = shapes.types[name]
        lines = [f"    public {java_type(descriptor)} {field};" for field, descriptor in sorted(shape["fields"])]
        for method_name, descriptor in sorted(shape["methods"]):
            if method_name in OBJECT_METHODS:
                continue
            returns = descriptor[descriptor.index(")") + 1:]
            arguments = descriptor[descriptor.index("(") + 1:descriptor.index(")")]
            parameters = [f"{java_type(piece)} argument{index}"
                          for index, piece in enumerate(_arguments(arguments))]
            call = f"{method_name}({', '.join(parameters)})"
            if kinds[name] == "interface":
                lines.append(f"    {java_type(returns)} {call};")
            elif returns == "V":
                lines.append(f"    public void {call} {{}}")
            else:
                lines.append(f"    public {java_type(returns)} {call} {{ return {default_value(returns)}; }}")
        if kinds[name] == "class":
            simple = name.rsplit("/", 1)[-1].split("$")[-1]
            for descriptor in sorted(shape["constructors"]) or ["()V"]:
                arguments = descriptor[descriptor.index("(") + 1:descriptor.index(")")]
                parameters = [f"{java_type(piece)} argument{index}"
                              for index, piece in enumerate(_arguments(arguments))]
                lines.append(f"    public {simple}({', '.join(parameters)}) {{}}")
        return lines

    def declaration(name: str) -> str:
        simple = name.rsplit("/", 1)[-1].split("$")[-1]
        children = sorted(other for other in shapes.types
                          if other.startswith(name + "$") and "$" not in other[len(name) + 1:])
        body = members(name) + [""] * (1 if children else 0) + [declaration(child) for child in children]
        text = f"public {kinds[name]} {simple} {{\n" + "\n".join(body) + "\n}"
        return text if not children else text[:-1] + "}\n"

    files = {}
    for outer in sorted({name.split("$")[0] for name in shapes.types}):
        package, _, _simple = outer.rpartition("/")
        files[outer] = f"package {package.replace('/', '.')};\n\n" + declaration(outer) + "\n"
    return files


def _arguments(arguments: str):
    """Split a method descriptor's argument list into one descriptor per argument."""
    pieces, current = [], ""
    index = 0
    while index < len(arguments):
        character = arguments[index]
        if character == "[":
            current += character
            index += 1
            continue
        if character == "L":
            end = arguments.index(";", index)
            pieces.append(current + arguments[index:end + 1])
            current = ""
            index = end + 1
            continue
        pieces.append(current + character)
        current = ""
        index += 1
    return pieces


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scan", action="append", default=[], help="a jar (or directory of jars) to read")
    parser.add_argument("--prefix", action="append", default=[], help="a package prefix that must be stubbed")
    parser.add_argument("--out", required=True)
    parser.add_argument("--asm", default=None, help="classpath of ASM jars, to compile the scanner")
    parser.add_argument("--client", default=None,
                        help="the client jar: the stubs name Minecraft types (BlockPos, BlockState), so the "
                             "compiler needs the client on its classpath")
    parser.add_argument("--client-dir", default=None,
                        help="directory to find the client in: the largest .jar in it, which is what it is")
    parser.add_argument("--source-out", default=None, help="keep the generated sources here, to read them")
    args = parser.parse_args(argv)
    if not args.prefix:
        print("no --prefix given: nothing to stub")
        return 0

    # The scan runs in Java, because that is where class files can be read: tools/ScanRefs.java prints one
    # line per reference and this collects them. It is compiled here, against the ASM jars the build already
    # stages for the mod remap, so nothing new has to be fetched.
    shapes = Shapes(args.prefix)
    javac = build_loader.find_javac()
    if not javac:
        print("no javac: the scanner has to be compiled")
        return 1
    # the java beside the javac that was found, so a JDK installed anywhere works
    java = str(pathlib.Path(javac).parent / "java")
    with tempfile.TemporaryDirectory(prefix="webmc-scanrefs-") as tmp:
        compiled = pathlib.Path(tmp) / "classes"
        compiled.mkdir()
        result = subprocess.run([javac, "-nowarn", "-cp", args.asm or "", "-d", str(compiled),
                                 str(ROOT / "tools" / "ScanRefs.java")], capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr[-2000:])
            return 1
        scan = subprocess.run([java, "-cp", str(compiled) + (":" + args.asm if args.asm else ""), "ScanRefs",
                               ",".join(args.prefix)] + args.scan, capture_output=True, text=True)
    if scan.returncode != 0:
        print(scan.stderr[-2000:])
        return 1
    for line in scan.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and parts[0] == "type":
            shapes.note_type(parts[1], parts[3] == "interface")
            if parts[3] == "interface":
                shapes.implemented.add(parts[1])
        elif len(parts) == 4 and parts[0] == "implements":
            shapes.note_type(parts[1], True)
            shapes.implemented.add(parts[1])
        elif len(parts) == 4 and parts[0] == "method":
            shapes.note_method(parts[1], parts[2], parts[3])
        elif len(parts) == 4 and parts[0] == "field":
            shapes.note_field(parts[1], parts[2], parts[3])
        elif len(parts) == 2 and parts[0] == "descriptor":
            shapes.note_descriptor(parts[1])
    if not shapes.types:
        print("nothing referenced from " + ", ".join(args.prefix) + ": no stubs needed")
        return 0

    files = sources_for(shapes, {})
    out = pathlib.Path(args.out)
    with tempfile.TemporaryDirectory(prefix="webmc-stubs-") as tmp:
        root = pathlib.Path(tmp) / "src"
        for name, text in files.items():
            path = root / (name + ".java")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        if args.source_out:
            directory = pathlib.Path(args.source_out)
            directory.mkdir(parents=True, exist_ok=True)
            for name, text in files.items():
                path = directory / (name + ".java")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        classes = pathlib.Path(tmp) / "classes"
        classes.mkdir()
        javac = build_loader.find_javac()
        client = args.client
        if not client and args.client_dir:
            candidates = sorted(pathlib.Path(args.client_dir).glob("*.jar"), key=lambda path: -path.stat().st_size)
            client = str(candidates[0]) if candidates else None
        classpath = [path for path in (client, args.asm) if path]
        result = subprocess.run([javac, "-nowarn"] + (["-cp", ":".join(classpath)] if classpath else [])
                                + ["-d", str(classes)]
                                + [str(path) for path in sorted(root.rglob("*.java"))],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr[-3000:])
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as jar:
            for path in sorted(classes.rglob("*.class")):
                jar.write(path, str(path.relative_to(classes)))
    print(f"stubbed {len(shapes.types)} type(s) from " + ", ".join(args.prefix) + f" -> {out}")
    for name in sorted(shapes.types)[:12]:
        print("  " + name + (" (interface)" if shapes.types[name]["interface"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
