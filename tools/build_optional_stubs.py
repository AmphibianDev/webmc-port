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


def moved(name: str, moves: list) -> str:
    """`java/lang/management/ManagementFactory` -> `webmc/platform/ManagementFactory` under a move rule."""
    for rule in moves:
        source, _, target = rule.partition(":")
        source = source.rstrip("/")
        target = target.rstrip("/")
        if not source:
            continue
        # a rule names a class, and applies to it, to its inner classes, and to anything under it if it
        # names a package: an exact match was missing at first, so every stub was still emitted in java.*
        # and javac refused the lot ("package exists in another module: java.base")
        if name == source:
            return target
        if name.startswith(source + "$"):
            return target + name[len(source):]
        if name.startswith(source + "/"):
            return target + "/" + name[len(source) + 1:]
    return name


def java_type(descriptor: str, moves=(), generated=None) -> str:
    """A descriptor, as a Java source type. Generics are dropped and nested classes use a dot."""
    arrays = 0
    while descriptor.startswith("["):
        arrays += 1
        descriptor = descriptor[1:]
    if descriptor in PRIMITIVES:
        name = PRIMITIVES[descriptor]
    elif descriptor.startswith("L") and descriptor.endswith(";"):
        original = descriptor[1:-1]
        internal = moved(original, list(moves))
        # A stub we generate is one flat class whose binary name keeps the '$' (webmc.platform.Outer$Inner),
        # so it is referenced that way. A type that lives somewhere else - the compiler's own library, the
        # JDK - is referenced the source way (java.nio.file.WatchEvent.Kind): javac will not resolve a '$'
        # in a qualified name against a real module, and there nesting is real.
        if generated is not None and original not in generated:
            internal = internal.replace("$", ".")
        # the '$' stays: inner stubs are flat classes whose binary name is Outer$Inner, so the source
        # spelling is webmc.platform.Outer$Inner - turning it into Outer.Inner points at a class that does
        # not exist ("cannot find symbol: class Kind, location: class WatchEvent")
        name = internal.replace("/", ".")
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
        # the same rule the scanner uses: a class name matches itself and its inner classes, a name ending
        # in '/' is a package. A bare string prefix over-collects (Parameter matched ParameterizedType)
        return any(name == prefix or (prefix.endswith("/") and name.startswith(prefix))
                   or name.startswith(prefix + "$") for prefix in self.prefixes)

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


def sources_for(shapes: Shapes, seed_methods: dict, moves=()) -> dict:
    moves = list(moves)
    generated = set(shapes.types)
    """One Java file per *outer* type, with inner types nested inside it.

    Nesting matters: `baritone.api.Settings$Setting` has to be a class inside `Settings`, because the mods
    refer to it as `baritone.api.Settings.Setting`. Emitting it as a top-level `Setting`, or as a file named
    `class Settings.Setting`, does not compile - both were tried.
    """
    kinds = {}
    for name in shapes.types:
        kinds[name] = "interface" if (shapes.types[name]["interface"] or name in shapes.implemented) else "class"
    kinds.setdefault("__shell__", "class")

    def members(name: str) -> list:
        if name not in shapes.types:
            # an inner class can be missing while its outer is provided by the class library (Thread's
            # nested types, for instance): the outer is then only a shell for nesting, with no members of
            # its own, and references to the real outer are left alone
            return []
        shape = shapes.types[name]
        lines = [f"    public {java_type(descriptor, moves, generated)} {field};"
                 for field, descriptor in sorted(shape["fields"])]
        for method_name, descriptor in sorted(shape["methods"]):
            if method_name in OBJECT_METHODS:
                continue
            returns = descriptor[descriptor.index(")") + 1:]
            arguments = descriptor[descriptor.index("(") + 1:descriptor.index(")")]
            parameters = [f"{java_type(piece, moves, generated)} argument{index}"
                          for index, piece in enumerate(_arguments(arguments))]
            call = f"{method_name}({', '.join(parameters)})"
            if kinds[name] == "interface":
                lines.append(f"    {java_type(returns, moves, generated)} {call};")
            elif returns == "V":
                lines.append(f"    public void {call} {{}}")
            else:
                lines.append(f"    public {java_type(returns, moves, generated)} {call}"
                             f" {{ return {default_value(returns)}; }}")
        if kinds[name] == "class":
            # the flat name, '$' and all: a constructor named State inside a class named Thread$State is a
            # method declaration to javac ("invalid method declaration; return type required")
            simple = moved(name, moves).rsplit("/", 1)[-1]
            for descriptor in sorted(shape["constructors"]) or ["()V"]:
                arguments = descriptor[descriptor.index("(") + 1:descriptor.index(")")]
                parameters = [f"{java_type(piece, moves, generated)} argument{index}"
                              for index, piece in enumerate(_arguments(arguments))]
                lines.append(f"    public {simple}({', '.join(parameters)}) {{}}")
        return lines

    def declaration_flat(name: str) -> str:
        simple = moved(name, moves).rsplit("/", 1)[-1]
        kind = kinds.get(name, "class")
        body = members(name)
        return f"public {kind} {simple} {{\n" + "\n".join(body) + "\n}"

    def declaration(name: str) -> str:
        # the *emitted* name, not the original one: a moved stub whose file is javax_naming_directory_Attribute
        # but whose class is still called Attribute does not compile (and javac's "cannot find symbol" then
        # points at a file that is right there, which cost a while to see)
        simple = moved(name, moves).rsplit("/", 1)[-1].split("$")[-1]
        children = sorted(other for other in shapes.types
                          if other.startswith(name + "$") and "$" not in other[len(name) + 1:])
        body = members(name) + [""] * (1 if children else 0) + [declaration(child) for child in children]
        kind = kinds.get(name, "class")
        text = f"public {kind} {simple} {{\n" + "\n".join(body) + "\n}"
        return text if not children else text[:-1] + "}\n"

    # One file per stub, flat - including inner classes, whose binary name keeps the '$'
    # (webmc/platform/Thread$State is a legal top-level class name). Nesting them would mean emitting a
    # moved *outer* for every missing inner class, and when the outer is a class the class library already
    # provides (java.lang.Thread) that shell shadows the real thing; leaving it un-moved emits sources in
    # java.* which javac refuses outright ("package exists in another module: java.base").
    files = {}
    for name in sorted(shapes.types):
        moved_name = moved(name, moves)
        package, _, _simple = moved_name.rpartition("/")
        files[moved_name] = f"package {package.replace('/', '.')};\n\n" + declaration_flat(name) + "\n"
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
    parser.add_argument("--class", action="append", default=[], dest="classes",
                        help="an exact class to stub, in internal form (java/nio/channels/SocketChannel)")
    parser.add_argument("--move", action="append", default=[],
                        help="from:to package prefixes, applied to every stub generated. A class in java.* "
                             "cannot be compiled as itself (javac refuses to define java.* sources), so the "
                             "shims live in webmc.platform and the references are renamed to match.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--asm", default=None, help="classpath of ASM jars, to compile the scanner")
    parser.add_argument("--client", default=None,
                        help="the client jar: the stubs name Minecraft types (BlockPos, BlockState), so the "
                             "compiler needs the client on its classpath")
    parser.add_argument("--client-dir", default=None,
                        help="directory to find the client in: the largest .jar in it, which is what it is")
    parser.add_argument("--extra-source", action="append", default=[],
                        help="a handwritten source to compile with the stubs: it may refer to the stub types, "
                             "which is what lets a helper stand in for a platform method whose signature "
                             "names a class the compiler does not have")
    parser.add_argument("--source-out", default=None, help="keep the generated sources here, to read them")
    args = parser.parse_args(argv)
    if not args.prefix and not args.classes:
        print("no --prefix or --class given: nothing to stub")
        return 0

    # The scan runs in Java, because that is where class files can be read: tools/ScanRefs.java prints one
    # line per reference and this collects them. It is compiled here, against the ASM jars the build already
    # stages for the mod remap, so nothing new has to be fetched.
    shapes = Shapes(args.prefix + [name.rsplit("/", 1)[0] + "/" for name in args.classes]
                    if False else args.prefix + args.classes)
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
                               ",".join(args.prefix + args.classes)] + args.scan, capture_output=True, text=True)
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

    # every generated source is emitted under its moved name, and the members' types are rewritten too, so
    # the stubs refer to each other rather than to classes javac will not let us define
    original = sources_for
    def sources_moved(shapes_arg, seed):
        return original(shapes_arg, seed, args.move)
    # A stub can name a type that was never collected: a member's return type that only appears inside
    # another missing class's signature, or an inner class of a missing outer. Every such type needs a
    # stub too, or javac fails on a name that has no source - which is exactly what "cannot find symbol:
    # class javax_naming_directory_Attribute" and "package webmc.platform.Thread does not exist" were.
    def referenced(types) -> set:
        names = set()
        for shape in types.values():
            descriptors = [descriptor for _name, descriptor in shape["fields"] | shape["methods"]]
            descriptors += list(shape["constructors"])
            for descriptor in descriptors:
                for piece in re.findall(r"L([^;]+);", descriptor):
                    names.add(piece)
        return names

    for _round in range(4):
        added = 0
        for name in sorted(referenced(shapes.types)):
            if name not in shapes.types and moved(name, list(args.move)) != name:
                shapes.types[name] = {"interface": False, "methods": set(), "fields": set(),
                                      "constructors": set()}
                added += 1
        if not added:
            break
    files = sources_moved(shapes, {})
    out = pathlib.Path(args.out)
    with tempfile.TemporaryDirectory(prefix="webmc-stubs-") as tmp:
        root = pathlib.Path(tmp) / "src"
        for name, text in files.items():
            path = root / (name + ".java")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for extra in args.extra_source:
            source = pathlib.Path(extra)
            target = root / source.name          # it declares its own package, so it lands at the root
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text())
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
