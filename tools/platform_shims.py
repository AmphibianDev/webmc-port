#!/usr/bin/env python3
"""Make the client and its mod set compile for a browser: point platform APIs the compiler lacks at stubs.

    python3 tools/platform_shims.py --classlib <teavm-classlib.jar> --client <client-named.jar> \
        --mods-dir /tmp/mods --asm <asm jars> --out <dir> [--report <json>]

Why this exists: a browser compiler implements a subset of java.base. The vanilla client happens to stay
inside that subset, but a real mod set does not - Meteor's bundled Discord IPC opens a SocketChannel and
reads a RuntimeMXBean, MinecraftAuth does the same for accounts - and a whole-program compile stops at the
first reference it cannot resolve. That is not a mod being broken: it is the platform being absent.

So the missing classes are given the shape they are used with, in a package of our own (javac will not let
us define java.*), and every reference to them is renamed to match. The code stays; it becomes inert -
which is the honest outcome for a socket in a browser.

The list of what is missing is computed against the compiler's own class library, so this needs no guessing
and no build: `tools/remap/MissingRefs.java` answers it in one pass.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_loader  # noqa: E402 - the JDK finder that already knows about macOS stubs

# Classes whose absence is *expected* to be fixable by stubbing. Everything missing is stubbed, but a
# package listed here is reported as "this is a browser limitation" rather than "this is a surprise".
PLATFORM_PACKAGES = ("java/lang/management/", "java/nio/channels/", "java/net/", "javax/", "jdk/",
                     "java/awt/", "java/time/", "java/security/", "java/nio/file/")

# The packages the compiler has actually named. Both callers use this list: the bake rewrites the mod set
# (which the patch chain merges), and a later step rewrites the patched client (which the engine's own
# patch chain has already been over - renaming inside the client *before* that chain breaks it, because the
# patcher looks for instructions it can no longer find: "Server offline authentication-service patch point
# was not found").
KNOWN = ("java/lang/management/", "java/nio/channels/", "java/net/", "java/sql/", "java/io/",
         "java/lang/reflect/", "javax/management/", "javax/net/", "org/jboss/",
         "org/spongepowered/asm/mixin/", "net/fabricmc/")

TARGET_PACKAGE = "webmc/platform"

PRIMITIVES = {"V": "void", "Z": "boolean", "B": "byte", "C": "char", "S": "short", "I": "int", "J": "long",
              "F": "float", "D": "double"}

# The methods and fields the compiler lacks *on classes it has*, with what to do about each. A stub cannot
# help here: the class exists in the class library, so the only way through is to point the call somewhere
# else. Each entry is (owner, name, descriptor) -> (helper, kind):
#   noop       nothing to do, and that is the truthful implementation
#   throw      a browser cannot do this at all, so it fails loudly where it is called
#   zipStream  expressible with what the class library has
#   emptyArray / emptySet   nothing to report, so nothing is reported
#   constant   a field that only needs a value
METHOD_POLICY = {
    ("java/lang/Thread", "onSpinWait", "()V"):
        ("onSpinWait", "noop"),
    ("java/io/RandomAccessFile", "getChannel", "()Ljava/nio/channels/FileChannel;"):
        ("getChannel", "throw"),
    ("java/net/URLConnection", "getContent", "()Ljava/lang/Object;"):
        ("getContent", "throw"),
    ("java/util/zip/ZipFile", "stream", "()Ljava/util/stream/Stream;"):
        ("zipStream", "zipStream"),
    ("java/lang/reflect/Method", "getParameters", "()[Ljava/lang/reflect/Parameter;"):
        ("getParameters", "emptyArray"),
    ("java/lang/reflect/Field", "accessFlags", "()Ljava/util/Set;"):
        ("accessFlags", "emptySet"),
    # Meteor's event bus resolves its listeners through MethodHandles, which a browser cannot do
    # reflectively. These throw for now so the build can see the rest of the surface; replacing orbit's
    # listener with one that calls the lambda directly is the real fix and is next.
    ("java/lang/invoke/MethodHandles$Lookup", "findStatic",
     "(Ljava/lang/Class;Ljava/lang/String;Ljava/lang/invoke/MethodType;)Ljava/lang/invoke/MethodHandle;"):
        ("noFindStatic", "throw"),
    ("java/lang/invoke/MethodHandles$Lookup", "findVirtual",
     "(Ljava/lang/Class;Ljava/lang/String;Ljava/lang/invoke/MethodType;)Ljava/lang/invoke/MethodHandle;"):
        ("noFindVirtual", "throw"),
    # static: there is no receiver on the stack, so the helper takes only the arguments
    ("java/lang/invoke/MethodType", "methodType",
     "(Ljava/lang/Class;Ljava/lang/Class;)Ljava/lang/invoke/MethodType;"):
        ("noMethodType", "throw", "static"),
    ("java/lang/invoke/MethodHandle", "invoke", "()Ljava/util/function/Consumer;"):
        ("noInvoke", "throw"),
    ("java/lang/invoke/MethodHandle", "invoke", "(Ljava/lang/Object;)Ljava/util/function/Consumer;"):
        ("noInvokeWith", "throw"),
}

FIELD_POLICY = {
    ("java/awt/Color", "LIGHT_GRAY", "Ljava/awt/Color;"):
        ("LIGHT_GRAY", "constant"),
}


def java_source_type(descriptor: str, names: dict) -> str:
    """A descriptor as Java source, after the renames: arrays first, then primitives, then objects.

    The first attempt stripped characters off the ends of the descriptor, which turned an array of a stub
    type into `[Lwebmc.platform.Parameter` - javac said no, twenty-seven times.
    """
    arrays = 0
    while descriptor.startswith("["):
        arrays += 1
        descriptor = descriptor[1:]
    if descriptor == "V":
        base = "void"
    elif descriptor in PRIMITIVES:
        base = PRIMITIVES[descriptor]
    elif descriptor.startswith("L") and descriptor.endswith(";"):
        internal = descriptor[1:-1]
        # a type we are stubbing keeps its binary name (webmc.platform.Outer$Inner); a type that lives
        # elsewhere - java.lang.invoke.MethodHandles$Lookup, say - is written the source way, because javac
        # will not resolve a '$' in a qualified name against a real module
        if internal in names:
            base = names[internal].replace("/", ".")
        else:
            base = internal.replace("$", ".").replace("/", ".")
    else:
        base = "java.lang.Object"
    return base + "[]" * arrays


def kind_of(key) -> tuple:
    return METHOD_POLICY[key]


def rename_descriptor(descriptor: str, names: dict) -> str:
    """Apply the class renames to a descriptor, so a helper's signature matches the rewritten call site."""
    for was, now in names.items():
        descriptor = descriptor.replace("L" + was + ";", "L" + now + ";")
    return descriptor


def helper_source(names: dict, methods: list, fields: list) -> str:
    """The Java for every helper the policy asked for, in the package the stubs live in."""
    body = []
    for key, entry in sorted(METHOD_POLICY.items()):
        helper, kind = entry[0], entry[1]
        owner, name, descriptor = key
        if (owner, name, descriptor) not in {(m[0], m[1], m[2]) for m in methods}:
            continue
        is_static = len(kind_of(key)) > 2 and kind_of(key)[2] == "static"
        returns = descriptor[descriptor.index(")") + 1:]
        arguments = descriptor[descriptor.index("(") + 1:descriptor.index(")")]
        parameters = [] if is_static else [f"{java_source_type('L' + owner + ';', names)} receiver"]
        for index, piece in enumerate(_argument_descriptors(arguments)):
            parameters.append(f"{java_source_type(piece, names)} argument{index}")
        result_type = java_source_type(returns, names)
        signature = f"public static {result_type} {helper}({', '.join(parameters)})"
        if kind == "noop":
            body.append(f"    {signature} {{ }}")
        elif kind == "throw":
            body.append(f"    {signature} {{ throw new UnsupportedOperationException("
                        f"\"not available in a browser: {owner.replace('/', '.')}.{name}\"); }}")
        elif kind == "zipStream":
            body.append(f"""    {signature} {{
        java.util.List<java.util.zip.ZipEntry> entries = new java.util.ArrayList<>();
        java.util.Enumeration<? extends java.util.zip.ZipEntry> enumeration = receiver.entries();
        while (enumeration.hasMoreElements()) {{
            entries.add(enumeration.nextElement());
        }}
        return entries.stream();
    }}""")
        elif kind == "emptyArray":
            body.append(f"    {signature} {{ return new {result_type[:-2]}[0]; }}")
        elif kind == "emptySet":
            body.append(f"    {signature} {{ return java.util.Collections.emptySet(); }}")
    for key, (helper, kind) in sorted(FIELD_POLICY.items()):
        owner, name, descriptor = key
        if (owner, name, descriptor) not in {(f[0], f[1], f[2]) for f in fields}:
            continue
        body.append(f"    public static final {java_source_type(descriptor, names)} {helper} = null;")
    if not body:
        return ""
    return ("package webmc.platform;\n\n// GENERATED by tools/platform_shims.py: the helpers that stand in for\n"
            "// platform methods the browser compiler does not have. See METHOD_POLICY there for why each\n"
            "// one is a no-op, a real implementation, or a loud failure.\n"
            "public final class Support {\n\n" + "\n\n".join(body) + "\n}\n")


def _argument_descriptors(arguments: str) -> list:
    pieces, current, index = [], "", 0
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


def write_rewrite_tables(work: pathlib.Path, names: dict, methods: list, fields: list) -> pathlib.Path:
    calls, statics = [], []
    for key, entry in sorted(METHOD_POLICY.items()):
        helper, kind = entry[0], entry[1]
        owner, name, descriptor = key
        if (owner, name, descriptor) not in {(m[0], m[1], m[2]) for m in methods}:
            continue
        new_owner = names.get(owner, owner)
        new_descriptor = rename_descriptor(descriptor, names)
        entry = METHOD_POLICY[key]
        is_static = len(entry) > 2 and entry[2] == "static"
        receiver = "" if is_static else "L" + new_owner + ";"
        helper_descriptor = "(" + receiver + new_descriptor[1:]
        calls.append("\t".join([new_owner, name, new_descriptor, f"{TARGET_PACKAGE}/Support", helper,
                                 helper_descriptor, "static" if is_static else "virtual"]))
    for key, (helper, kind) in sorted(FIELD_POLICY.items()):
        owner, name, descriptor = key
        if (owner, name, descriptor) not in {(f[0], f[1], f[2]) for f in fields}:
            continue
        new_descriptor = rename_descriptor(descriptor, names)
        statics.append("\t".join([names.get(owner, owner), name, new_descriptor,
                                   f"{TARGET_PACKAGE}/Support", helper, new_descriptor]))
    path = work / "calls.tsv"
    path.write_text("".join(line + "\n" for line in calls))
    path.with_suffix(".fields.tsv").write_text("".join(line + "\n" for line in statics))
    return path




def find_classlib(hint: pathlib.Path, log) -> pathlib.Path | None:
    """The compiler's class library: from a directory that might hold a maven cache, or from Maven Central."""
    if hint.is_dir():
        for candidate in (hint.glob("org/teavm/teavm-classlib/*/teavm-classlib-*.jar"),
                          hint.glob("**/teavm-classlib-*.jar")):
            for path in sorted(candidate):
                return path
    cached = pathlib.Path("/tmp/teavm/teavm-classlib-0.15.0.jar")
    if cached.is_file() and cached.stat().st_size > 1_000_000:
        return cached
    cached.parent.mkdir(parents=True, exist_ok=True)
    url = ("https://repo1.maven.org/maven2/org/teavm/teavm-classlib/0.15.0/"
           "teavm-classlib-0.15.0.jar")
    log(f"  fetching the compiler's class library: {url}")
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=180) as response:
            cached.write_bytes(response.read())
        return cached
    except Exception as problem:
        log(f"  ! could not fetch it ({problem})")
        return None


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


def compile_tools(javac: str, asm: str, work: pathlib.Path, log) -> pathlib.Path:
    classes = work / "tools"
    classes.mkdir(parents=True, exist_ok=True)
    sources = ["tools/remap/MissingRefs.java", "tools/remap/ModRemapper.java", "tools/ScanRefs.java"]
    result = run([javac, "-nowarn", "-cp", asm, "-d", str(classes)] + [str(ROOT / source) for source in sources])
    if result.returncode != 0:
        log(result.stderr[-1500:])
        raise SystemExit("could not compile the platform tools")
    return classes


def missing_classes(java: str, tools: pathlib.Path, asm: str, classlib: pathlib.Path, scan: list,
                    log) -> tuple:
    result = run([java, "-cp", f"{tools}{os.pathsep}{asm}", "MissingRefs", "--classlib", str(classlib),
                  "--scan", *[str(path) for path in scan]])
    if result.returncode != 0:
        log(result.stderr[-1500:])
        raise SystemExit("could not compute the missing references")
    classes, methods, fields = [], [], []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "class" and len(parts) > 1 and parts[1].strip():
            classes.append(parts[1].strip())
        elif parts[0] == "method" and len(parts) > 1:
            # the scanner prints "owner name descriptor" as one field: splitting it here is what the policy
            # table keys on, and leaving it as a string meant every entry silently matched nothing
            piece = parts[1].split(" ")
            if len(piece) == 3:
                methods.append(tuple(piece))
        elif parts[0] == "field" and len(parts) > 1:
            piece = parts[1].split(" ")
            if len(piece) == 3:
                fields.append(tuple(piece))
    summary = [line for line in result.stdout.splitlines() if line.startswith("# checked")]
    log("  " + (summary[-1] if summary else "no summary"))
    return classes, methods, fields


def target_names(missing: list) -> dict:
    """One target name per missing class, in our own package, with collisions resolved."""
    names = {}
    used = {}
    for name in missing:
        package, _, simple = name.rpartition("/")
        candidate = simple
        if candidate in used:
            # two packages each with a Foo: keep them apart by package, still under our own namespace
            candidate = package.replace("/", "_") + "_" + simple
        used[candidate] = name
        names[name] = f"{TARGET_PACKAGE}/{candidate}"
    return names


def write_table(work: pathlib.Path, names: dict) -> pathlib.Path:
    prefix = work / "platform-rename.tsv"
    prefix.write_text("".join(f"{was}\t{now}\n" for was, now in sorted(names.items())))
    prefix.with_suffix(".fields.tsv").write_text("")
    prefix.with_suffix(".methods.tsv").write_text("")
    return prefix


def rename(java: str, tools: pathlib.Path, asm: str, table: pathlib.Path, jar: pathlib.Path,
           work: pathlib.Path, log) -> bool:
    """Rewrite references to the missing classes in one jar, in place."""
    output = work / "renamed" / jar.name
    output.parent.mkdir(parents=True, exist_ok=True)
    result = run([java, "-cp", f"{tools}{os.pathsep}{asm}", "ModRemapper", str(jar), str(output),
                  str(table)])
    if result.returncode != 0 or not output.is_file():
        log(f"  ! {jar.name}: could not be renamed ({result.stderr.strip()[:140]})")
        return False
    summary = [line for line in result.stdout.splitlines() if line.startswith("remapped ")]
    if summary:
        log("  " + summary[-1].replace(str(output), jar.name))
    shutil.copyfile(output, jar)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--classlib", required=True, help="the compiler's class library jar")
    parser.add_argument("--client", default=None, help="the client jar to rewrite in place")
    parser.add_argument("--mods-dir", default=None, help="where the mod jars are, and where the stubs land")
    parser.add_argument("--asm", required=True, help="classpath of ASM jars")
    parser.add_argument("--out", required=True, help="directory for the generated stub jar")
    parser.add_argument("--known", action="store_true",
                        help="use the shared set of packages the compiler reported (see KNOWN)")
    parser.add_argument("--only", action="append", default=[],
                        help="restrict stubbing to this class or package (a trailing / means package). The "
                             "detector is deliberately wider than the compiler: it reads the class library "
                             "jar, which does not include everything the compiler can resolve (a class "
                             "provided by a substitution looks missing). Renaming one of those inside the "
                             "client changes its API surface - the engine's own patch sources are compiled "
                             "against it - so what gets renamed is the set the compiler actually reported.")
    parser.add_argument("--rewrite-mods", action="store_true",
                        help="also rename references inside the mod jars. The bake has already done that "
                             "for the mod set it ships; doing it a second time from here means rewriting "
                             "jars the bake produced (the optional-dependency stubs among them) and, with a "
                             "different rule set, is how those stubs disappeared from the classpath.")
    parser.add_argument("--report", default=None, help="write a JSON report of what was stubbed")
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)

    log = print
    javac = build_loader.find_javac()
    if not javac:
        log("no javac: cannot compile the platform tools")
        return 2
    java = str(pathlib.Path(javac).parent / "java")
    classlib = pathlib.Path(args.classlib)
    if not classlib.is_file():
        classlib = find_classlib(classlib, log)
    if classlib is None or not classlib.is_file():
        log(f"! the class library is missing: {args.classlib}")
        return 2

    scan = []
    if args.client:
        scan.append(pathlib.Path(args.client))
    if args.mods_dir:
        scan.extend(sorted(pathlib.Path(args.mods_dir).glob("*.jar")))
    if not scan:
        log("nothing to scan: pass --client or --mods-dir")
        return 2

    with tempfile.TemporaryDirectory(prefix="webmc-platform-") as tmp:
        work = pathlib.Path(tmp)
        tools = compile_tools(javac, args.asm, work, log)

        # 1. detect, stub, rename - repeated, because generating stubs can introduce references that were
        #    not there before (a stub whose method returns a type nothing had mentioned yet)
        all_names = {}
        for pass_number in range(1, 7):
            missing, methods, fields = missing_classes(java, tools, args.asm, classlib, scan, log)
            if not missing:
                log(f"  pass {pass_number}: nothing missing")
                break
            if pass_number > 1:
                log(f"  pass {pass_number}: {len(missing)} more class(es): {', '.join(missing[:4])}")
            wanted_set = list(args.only) + (list(KNOWN) if args.known else [])
            if wanted_set:
                before = len(missing)
                missing = [name for name in missing
                           if any(name == wanted or (wanted.endswith("/") and name.startswith(wanted))
                                  or name.startswith(wanted + "$") for wanted in wanted_set)]
                log(f"  {len(missing)} of {before} missing class(es) are in the requested set")
                if not missing:
                    # the detector is wider than the compiler on purpose: what is left is out of scope for
                    # this run, and the loop has to stop here rather than chase it forever
                    break
            by_package = {}
            for name in missing:
                by_package.setdefault(name.rpartition("/")[0], []).append(name)
            log(f"  {len(missing)} class(es) the compiler does not have, from {len(by_package)} package(s):")
            for package, group in sorted(by_package.items(), key=lambda item: -len(item[1]))[:8]:
                log(f"    {len(group):3d}  {package}")

            # 2. shapes for them, generated from how they are used, emitted under our own package
            names = {**all_names, **target_names(missing)}
            all_names = names
            stubs = pathlib.Path(args.out) / "webmc-platform-stubs.jar"
            stubs.parent.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, str(ROOT / "tools" / "build_optional_stubs.py"),
                       "--asm", args.asm, "--out", str(stubs),
                       "--client", str(args.client or scan[0])]
            for path in scan:
                command += ["--scan", str(path)]
            for source in missing:
                command += ["--class", source]
            for source, target in names.items():
                command += ["--move", f"{source}:{target}"]
            helpers = helper_source(names, methods, fields)
            if helpers:
                extra = work / "Support.java"
                extra.write_text(helpers)
                command += ["--extra-source", str(extra)]
            result = run(command)
            if result.returncode != 0 or not stubs.is_file():
                log(result.stdout[-2000:])
                log(result.stderr[-2000:])
                log("! the platform stubs could not be built")
                return 1
            for line in result.stdout.strip().splitlines()[-3:]:
                log("  " + line)

            # 3. rename every reference to them, in the client and in the mods, and point the calls the
            #    compiler cannot make at the helpers
            table = write_table(work, names)
            rewrites = write_rewrite_tables(work, names, methods, fields)
            os.environ["WEBMC_CALL_REWRITES"] = str(rewrites)
            renamed = 0
            targets = [jar for jar in scan
                       if args.rewrite_mods or (args.client and str(jar) == args.client)] or scan
            for jar in targets:
                if rename(java, tools, args.asm, table, jar, work, log):
                    renamed += 1
            log(f"  renamed references in {renamed} jar(s)")

            if args.mods_dir:
                shutil.copyfile(stubs, pathlib.Path(args.mods_dir) / stubs.name)
                log(f"  stub jar shipped as {stubs.name}")

        if not all_names:
            log("  nothing platform-shaped is missing: no stubs needed")
            return 0

        names_path = ROOT / "tools" / "platform-shims.json"
        names_path.write_text(json.dumps({"stubbed": names, "methods": methods, "fields": fields},
                                         indent=1, sort_keys=True) + "\n")
        if args.report:
            shutil.copyfile(names_path, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
