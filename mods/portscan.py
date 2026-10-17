#!/usr/bin/env python3
"""Portability scanner for Fabric mod jars, for the browser (Eaglercraft-style) builds.

A runtime Fabric loader cannot exist in a browser client: TeaVM-compiled clients have no
JVM, no class loader and no class-load step for Mixin to hook. What CAN exist is a
build-time loader: the mod's classes are compiled into the client with TeaVM and the
Fabric API modules it calls are shimmed on top of the ported client.

This tool answers, before anyone spends 40 minutes on a rebuild: can THIS mod be ported
that way, and what does it need?

    python3 portscan.py somemod.jar
    python3 portscan.py https://example.com/somemod.jar

Output: a human report, plus the same data as JSON with --json.
"""

import io
import json
import re
import struct
import sys
import urllib.request
import zipfile
from collections import Counter

# Mixin annotations that rewrite game code. Each one has to be applied statically
# (at build time) instead of at class load time, which is where the real work is.
INJECTORS = {
    "Inject", "Redirect", "ModifyArg", "ModifyArgs", "ModifyVariable", "ModifyConstant",
    "ModifyExpressionValue", "WrapOperation", "WrapWithCondition", "Overwrite", "Surrogate",
    "ModifyReturnValue", "ModifyReceiver", "WrapMethod",
}
BENIGN_MIXINS = {"Mixin", "Shadow", "Unique", "Final", "Mutable", "Accessor", "Invoker", "At", "Slice", "Debug"}


def class_strings(data):
    """Every UTF-8 constant in a class file - enough to see what the bytecode refers to."""
    if data[:4] != b"\xca\xfe\xba\xbe":
        return []
    count = struct.unpack(">H", data[8:10])[0]
    i, index, out = 10, 1, []
    while index < count:
        tag = data[i]
        i += 1
        if tag == 1:
            length = struct.unpack(">H", data[i:i + 2])[0]
            i += 2
            out.append(data[i:i + length].decode("utf-8", "replace"))
            i += length
        elif tag in (7, 8, 16, 19, 20):
            i += 2
        elif tag == 15:
            i += 3
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            i += 4
        elif tag in (5, 6):
            i += 8
            index += 1
        index += 1
    return out


def java_major(data):
    return struct.unpack(">H", data[6:8])[0] if data[:4] == b"\xca\xfe\xba\xbe" else 0


def scan(jar_bytes):
    z = zipfile.ZipFile(io.BytesIO(jar_bytes))
    names = z.namelist()
    classes = [n for n in names if n.endswith(".class")]
    meta = json.loads(z.read("fabric.mod.json")) if "fabric.mod.json" in names else {}

    official, intermediary, injectors, benign, fabric_api = set(), set(), Counter(), Counter(), set()
    kotlin = asm_runtime = 0
    majors = Counter()

    for name in classes:
        try:
            data = z.read(name)
            majors[java_major(data)] += 1
            strings = class_strings(data)
        except Exception:
            continue
        for s in strings:
            if s.startswith(("net/minecraft/", "com/mojang/")):
                official.add(s)
            if re.search(r"(^|/)class_\d+", s):
                intermediary.add(s)
            if s.startswith("org/spongepowered/asm/mixin") or (s.startswith("Lorg/spongepowered/asm/mixin") and s.endswith(";")):
                annot = s.lstrip("L").rstrip(";").split("/")[-1]
                if annot in INJECTORS:
                    injectors[annot] += 1
                elif annot in BENIGN_MIXINS:
                    benign[annot] += 1
            if s.startswith("net/fabricmc/fabric/api"):
                fabric_api.add(s.split("$")[0])
            if s.startswith(("kotlin/", "kotlinx/")):
                kotlin += 1
            if s.startswith("org/objectweb/asm"):
                asm_runtime += 1

    natives = [n for n in names if n.endswith((".so", ".dll", ".dylib", ".jnilib"))]
    nested = [n for n in names if n.endswith(".jar")]

    mixin_configs = list(meta.get("mixins") or [])
    if isinstance(mixin_configs, dict):
        mixin_configs = list(mixin_configs.values())
    mixin_classes = 0
    for config in mixin_configs:
        raw = None
        for candidate in (config, "mixins." + config, config if config.endswith(".json") else config + ".json"):
            if candidate in names:
                raw = z.read(candidate)
                break
        if raw:
            try:
                cfg = json.loads(raw)
                package = (cfg.get("package") or "").replace(".", "/")
                mixin_classes += sum(
                    len(cfg.get(key) or [])
                    for key in ("mixins", "client", "server")
                )
                if package and not cfg.get("mixins") and not cfg.get("client"):
                    mixin_classes += 1
            except Exception:
                pass

    injector_total = sum(injectors.values())
    depends = dict(meta.get("depends") or {})
    entrypoints = {k: (len(v) if isinstance(v, list) else 1) for k, v in (meta.get("entrypoints") or {}).items()}

    # ---- verdict -------------------------------------------------------------
    blockers, work = [], []
    if natives:
        blockers.append(f"ships native libraries ({', '.join(natives[:3])}) - a browser tab cannot load them")
    if asm_runtime:
        blockers.append("bundles/uses ASM to rewrite class bytecode at runtime")
    if majors:
        highest = max(majors)
        java_version = highest - 44
        if java_version > 25:
            blockers.append(f"compiled for Java {java_version}; nothing browser-side compiles past Java 25 today")
    if not official and not intermediary:
        blockers.append("references no game classes at all - not a Minecraft mod")

    if injector_total:
        work.append(f"{injector_total} Mixin injection(s) to apply offline: {', '.join(f'@{k}x{v}' for k, v in injectors.most_common(5))}")
    if mixin_classes:
        work.append(f"{mixin_classes} mixin class(es) declared in {len(mixin_configs)} config(s)")
    if fabric_api:
        modules = sorted({a.split("/api/")[-1].rsplit("/", 1)[0] for a in fabric_api})
        work.append(f"{len(fabric_api)} Fabric API type(s) to shim, modules: {', '.join(modules)}")
    for dep in depends:
        if dep not in ("minecraft", "fabricloader", "java"):
            work.append(f"needs another mod: {dep}")

    if blockers:
        verdict = "impossible"
    elif injector_total > 25:
        verdict = "hard"          # mostly renderer/perf mods that rewrite hot game code
    elif injector_total or work:
        verdict = "portable, needs shims"
    else:
        verdict = "portable"

    return {
        "id": meta.get("id", "?"), "version": meta.get("version", "?"),
        "environment": meta.get("environment", "?"),
        "classes": len(classes),
        "class_kb": sum(z.getinfo(c).file_size for c in classes) // 1024,
        "java": max(majors) - 44 if majors else None,
        "unobfuscated_names": len(intermediary) == 0,
        "official_refs": len(official), "intermediary_refs": len(intermediary),
        "injectors": dict(injectors), "mixin_classes": mixin_classes,
        "fabric_api": sorted(fabric_api), "depends": depends, "entrypoints": entrypoints,
        "kotlin_refs": kotlin, "natives": natives, "nested_jars": nested,
        "blockers": blockers, "work": work, "verdict": verdict,
    }


def report(r):
    icon = {"portable": "✅", "portable, needs shims": "🟡", "hard": "🟠", "impossible": "⛔"}[r["verdict"]]
    print(f"\n{icon} {r['id']} {r['version']}  ({r['environment']}) - {r['verdict'].upper()}")
    print(f"   {r['classes']} classes / {r['class_kb']} KB, Java {r['java']}"
          f", names: {'official (no remap needed)' if r['unobfuscated_names'] else 'intermediary (needs remap)'}")
    print(f"   game refs: {r['official_refs']} official, {r['intermediary_refs']} intermediary"
          f" | mixins: {r['mixin_classes']} class(es), injectors: {sum(r['injectors'].values())}")
    if r["fabric_api"]:
        print(f"   fabric api types: {len(r['fabric_api'])}")
    if r["kotlin_refs"]:
        print(f"   Kotlin mod ({r['kotlin_refs']} kotlin refs) - needs the Kotlin language adapter")
    for item in r["work"]:
        print(f"   work: {item}")
    for item in r["blockers"]:
        print(f"   BLOCKER: {item}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    results = []
    for arg in args:
        data = urllib.request.urlopen(arg, timeout=120).read() if arg.startswith("http") else open(arg, "rb").read()
        results.append(scan(data))
    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            report(r)
