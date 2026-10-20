#!/usr/bin/env python3
"""The WebMC build-time Fabric loader.

An ahead-of-time browser client has no class loader, so "loading mods" happens when
the client is *built*. This is that loader's front end: point it at the mods and the
client jar, and it produces everything the build needs, refusing - with the reason -
the mods that cannot work.

    1. reads every jar in client-mods/ (fabric.mod.json: id, version, entrypoints,
       mixin configs) and reports what each mod needs;
    2. prunes the mixins that cannot apply here - integration mixins whose target mod
       (Sodium, Lithium, Indigo, ViaFabricPlus, Baritone ...) is not in this build -
       and rewrites those jars without them, because a `required: true` config with a
       missing target stops the bake instead of being skipped;
    3. generates net.fabricmc.eaglerbridge.EaglerFabricLoader: one entrypoint call per
       mod, which is exactly what Fabric's loader does at startup minus the jar scan;
    4. lays out what to compile and where each class goes:

        <out>/mods/            pruned mod jars (the bake reads this folder)
        <out>/src/             generated java (the loader class)
        <out>/overlay-mods/    mod classes that must go into the client jar
        <out>/plan.json        machine-readable result
        <out>/REPORT.md        the human report, also printed

Exit code is 2 when something cannot be loaded at all - a mod with no classes, or a Fabric
API class the surviving mod code calls that no shim implements - so CI stops before the
bake and the ~50 minute compile instead of after them.

    python3 modded/loader.py --mods client-mods --game-jar client-original.jar --out build/loader
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import re
import shutil
import struct
import sys
import zipfile

# --------------------------------------------------------------------------- #
# class files: just enough of the format to read @Mixin and the constant pool
# --------------------------------------------------------------------------- #

UTF8, INTEGER, FLOAT, LONG, DOUBLE = 1, 3, 4, 5, 6
CLASS, STRING = 7, 8
FIELDREF, METHODREF, INTERFACEMETHODREF = 9, 10, 11
NAMEANDTYPE, METHODHANDLE, METHODTYPE, DYNAMIC, INVOKEDYNAMIC = 12, 15, 16, 17, 18
MODULE, PACKAGE = 19, 20

MIXIN_ANNOTATION = "Lorg/spongepowered/asm/mixin/Mixin;"


class ClassFile:
    """Constant pool + the class-level annotations of one .class file."""

    def __init__(self, data: bytes):
        self.data = data
        self.utf8: dict[int, str] = {}
        self.classes: dict[int, int] = {}
        self.strings: dict[int, int] = {}
        self._read_pool()

    # -- constant pool ------------------------------------------------------
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
                elif tag == STRING:
                    self.strings[index] = value
            elif tag == METHODHANDLE:
                cursor += 3
            elif tag in (LONG, DOUBLE):
                cursor += 8
                index += 1
            else:
                raise ValueError(f"unknown constant pool tag {tag}")
            index += 1
        self.cursor = cursor

    def class_name(self, index: int) -> str:
        return self.utf8.get(self.classes.get(index, 0), "")

    def referenced_classes(self) -> set[str]:
        """Every class name this class file mentions."""
        return {self.class_name(index) for index in self.classes}

    def const_string(self, index: int) -> str:
        return self.utf8.get(self.strings.get(index, 0), "")

    # -- class attributes ---------------------------------------------------
    def _skip_member(self, cursor: int) -> int:
        cursor += 6  # access, name, descriptor
        attributes = struct.unpack(">H", self.data[cursor:cursor + 2])[0]
        cursor += 2
        for _ in range(attributes):
            cursor += 2
            length = struct.unpack(">I", self.data[cursor:cursor + 4])[0]
            cursor += 4 + length
        return cursor

    def attributes(self) -> dict[str, bytes]:
        cursor = self.cursor
        cursor += 6  # access_flags, this_class, super_class
        interfaces = struct.unpack(">H", self.data[cursor:cursor + 2])[0]
        cursor += 2 + 2 * interfaces
        for _ in range(2):  # fields, then methods
            count = struct.unpack(">H", self.data[cursor:cursor + 2])[0]
            cursor += 2
            for _ in range(count):
                cursor = self._skip_member(cursor)
        found: dict[str, bytes] = {}
        count = struct.unpack(">H", self.data[cursor:cursor + 2])[0]
        cursor += 2
        for _ in range(count):
            name_index = struct.unpack(">H", self.data[cursor:cursor + 2])[0]
            length = struct.unpack(">I", self.data[cursor + 2:cursor + 6])[0]
            blob = self.data[cursor + 6:cursor + 6 + length]
            found[self.utf8.get(name_index, "")] = blob
            cursor += 6 + length
        return found

    def mixin_targets(self) -> list[str]:
        """The classes this mixin declares with @Mixin(value = X.class, targets = "y")."""
        blob = self.attributes().get("RuntimeInvisibleAnnotations") or \
            self.attributes().get("RuntimeVisibleAnnotations")
        if not blob:
            return []
        try:
            return self._targets_from_annotations(blob)
        except (struct.error, IndexError, ValueError):
            return []

    def _targets_from_annotations(self, blob: bytes) -> list[str]:
        cursor = 2  # annotation count
        count = struct.unpack(">H", blob[:2])[0]
        targets: list[str] = []
        for _ in range(count):
            type_index = struct.unpack(">H", blob[cursor:cursor + 2])[0]
            pairs = struct.unpack(">H", blob[cursor + 2:cursor + 4])[0]
            cursor += 4
            is_mixin = self.utf8.get(type_index) == MIXIN_ANNOTATION
            for _ in range(pairs):
                name = self.utf8.get(struct.unpack(">H", blob[cursor:cursor + 2])[0], "")
                cursor += 2
                values, cursor = self._element_value(blob, cursor)
                if is_mixin and name in ("value", "targets"):
                    targets.extend(values)
        return [t for t in targets if t]


    def _element_value(self, blob: bytes, cursor: int) -> tuple[list[str], int]:
        """Returns (class names / strings it carries, new cursor)."""
        tag = chr(blob[cursor])
        cursor += 1
        if tag == "c":  # class literal: "Lcom/example/Thing;" or "[I"
            index = struct.unpack(">H", blob[cursor:cursor + 2])[0]
            cursor += 2
            return [self.utf8.get(index, "").strip("L;").replace(".", "/")], cursor
        if tag == "s":  # string literal: how targets = "com.example.Thing" arrives
            index = struct.unpack(">H", blob[cursor:cursor + 2])[0]
            cursor += 2
            return [self.utf8.get(index, "").replace(".", "/")], cursor
        if tag == "e":  # enum: type + name
            cursor += 4
            return [], cursor
        if tag == "[":
            numbers = struct.unpack(">H", blob[cursor:cursor + 2])[0]
            cursor += 2
            found: list[str] = []
            for _ in range(numbers):
                values, cursor = self._element_value(blob, cursor)
                found.extend(values)
            return found, cursor
        if tag == "@":  # nested annotation: skip it, we only read the mixin's own
            numbers = struct.unpack(">H", blob[cursor:cursor + 2])[0]
            cursor += 2
            for _ in range(numbers):
                cursor += 2
                _, cursor = self._element_value(blob, cursor)
            return [], cursor
        widths = {"B": 1, "C": 1, "D": 2, "F": 1, "I": 1, "J": 2, "S": 1, "Z": 1}
        return [], cursor + widths.get(tag, 1) * 2


# --------------------------------------------------------------------------- #
# jars
# --------------------------------------------------------------------------- #

class Jar:
    def __init__(self, path: pathlib.Path, data: bytes | None = None):
        self.path = path
        self.data = data if data is not None else path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(self.data)) as archive:
            self.names = [n for n in archive.namelist() if n.endswith(".class")]
            self.meta = archive.read("fabric.mod.json") if "fabric.mod.json" in archive.namelist() else None

    @property
    def classes(self) -> set[str]:
        """Class names without the .class suffix, which is how mixin targets read."""
        return {name[:-6] for name in self.names}

    def read(self, name: str) -> bytes:
        with zipfile.ZipFile(io.BytesIO(self.data)) as archive:
            return archive.read(name)

    def without(self, drop: set[str], replacements: dict[str, bytes] | None = None) -> bytes:
        """A copy of the jar without some entries, with others replaced."""
        replacements = replacements or {}
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.data)) as source, \
                zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                if info.filename in drop:
                    continue
                if info.filename in replacements:
                    target.writestr(info, replacements[info.filename])
                    continue
                target.writestr(info, source.read(info.filename))
        return buffer.getvalue()


def mod_metadata(jar: Jar) -> dict:
    if jar.meta is None:
        return {}
    try:
        return json.loads(jar.meta)
    except json.JSONDecodeError:
        return {}


def mixin_configs(meta: dict) -> list[str]:
    """fabric.mod.json may list mixin configs as strings or as {config: ...} objects."""
    raw = meta.get("mixins") or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for entry in raw:
        name = entry.get("config") if isinstance(entry, dict) else entry
        if isinstance(name, str):
            out.append(name)
    return out


def config_mixins(jar: Jar, config_name: str) -> list[str]:
    """The mixin class names (package + name) a config asks Mixin to apply."""
    try:
        config = json.loads(jar.read(config_name))
    except (KeyError, json.JSONDecodeError):
        return []
    return [full_mixin_name(config, name) for name in config_mixin_entries(config)]


def config_mixin_entries(config: dict) -> list[str]:
    entries = []
    for key in ("mixins", "client", "server"):
        entries.extend(config.get(key) or [])
    return entries


def full_mixin_name(config: dict, name: str) -> str:
    """A config lists either "Thing" (relative to its package) or "a.b.Thing"."""
    if "." in name:
        return name.replace(".", "/")
    package = config.get("package", "").replace(".", "/")
    return f"{package}/{name}" if package else name


def rewrite_config(jar: Jar, config_name: str, dropped: set[str]) -> bytes:
    """The same config without the mixins that cannot apply to this client."""
    config = json.loads(jar.read(config_name))
    for key in ("mixins", "client", "server"):
        if key in config:
            config[key] = [name for name in config[key]
                           if full_mixin_name(config, name) not in dropped]
    return json.dumps(config, indent=2).encode()


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #

SHIM_ROOT = pathlib.Path(__file__).parent / "shims"
# Entrypoint interfaces the generated loader itself needs, whatever the mods use.
ALWAYS_SHIMS = ("net/fabricmc/api",)
# Covered by modded/loader-src/stubs instead of modded/shims.
STUB_PACKAGES = ("net/fabricmc/loader",)

# A shim that calls a member which has to be *injected into the game first* cannot be
# compiled against a plain client jar. On this route the mixin applies the real mods and
# the overlay adds classes, so there is no source patch to hang such a member on yet.
NEEDS_SOURCE_HOOK = {
    "net/fabricmc/fabric/api/client/screen/v1/Screens":
        "Screens.getWidgets() calls Screen.eaglerGetRenderables(), which the source-hook "
        "route injects and the overlay route does not have yet",
}


def resolve_shims(needed: set[str]) -> tuple[list[str], list[str]]:
    """Which shim packages must be compiled, and which needs are not covered."""
    packages = set(ALWAYS_SHIMS)
    missing = []
    for class_name in sorted(needed):
        if class_name.startswith(STUB_PACKAGES):
            continue  # supplied by modded/loader-src/stubs
        if class_name in NEEDS_SOURCE_HOOK:
            missing.append(f"{class_name}: {NEEDS_SOURCE_HOOK[class_name]}")
            continue
        package = str(pathlib.PurePosixPath(class_name).parent)
        if (SHIM_ROOT / package).is_dir():
            packages.add(package)
        else:
            missing.append(f"{class_name}: no shim written for this Fabric API class")
    return sorted(packages), missing


def referenced_fabric_classes(jars: list, kept: dict) -> set[str]:
    """The net.fabricmc classes the classes that survive pruning still reference."""
    found: set[str] = set()
    for path, jar in jars:
        for class_name in kept.get(path.name, set()):
            try:
                referenced = ClassFile(jar.read(class_name + ".class")).referenced_classes()
            except Exception:  # noqa: BLE001
                continue
            found |= {name for name in referenced if name.startswith("net/fabricmc/")}
    return found


def plan_mods(mod_paths: list[pathlib.Path], game_classes: set[str]) -> dict:
    jars = [(path, Jar(path)) for path in mod_paths]
    every_class = set(game_classes)
    for _path, jar in jars:
        every_class |= jar.classes

    mods = []
    for path, jar in jars:
        meta = mod_metadata(jar)
        mod_id = meta.get("id") or path.stem
        entrypoints = meta.get("entrypoints") or {}
        entry = {
            "file": path.name,
            "id": mod_id,
            "version": meta.get("version"),
            "name": (meta.get("name") or mod_id),
            "depends": meta.get("depends") or {},
            "entrypoints": {kind: entrypoints[kind] for kind in ("main", "client", "modmenu")
                           if kind in entrypoints},
            "mixins_kept": [],
            "mixins_pruned": [],
            "classes": len(jar.classes),
        }

        drop: set[str] = set()
        replacements: dict[str, bytes] = {}
        for config_name in mixin_configs(meta):
            mixin_classes = config_mixins(jar, config_name)
            if not mixin_classes:
                entry["mixins_kept"].append({"config": config_name, "mixins": 0,
                                             "note": "config not readable"})
                continue
            kept, pruned = [], []
            for mixin in mixin_classes:
                entry_path = mixin + ".class"
                if entry_path not in jar.names:
                    pruned.append({"mixin": mixin, "why": "class is not in the jar"})
                    drop.add(entry_path)
                    continue
                try:
                    targets = ClassFile(jar.read(entry_path)).mixin_targets()
                except Exception as error:  # noqa: BLE001
                    targets = []
                    kept.append(mixin)
                    entry.setdefault("warnings", []).append(f"{mixin}: {error}")
                if not targets:
                    kept.append(mixin)
                    continue
                resolvable = [t for t in targets if t in every_class]
                if resolvable:
                    kept.append(mixin)
                else:
                    pruned.append({"mixin": mixin, "why": "target not in this build: " +
                                   ", ".join(sorted(targets)[:3])})
                    drop.add(entry_path)
            entry["mixins_kept"].append({"config": config_name, "mixins": len(kept)})
            if pruned:
                entry["mixins_pruned"].append({"config": config_name, "mixins": pruned})
                # The config file itself has to lose them too: it is often required: true,
                # and a required config that still lists a mixin whose target is absent
                # stops the bake even though we already know it cannot apply.
                replacements[config_name] = rewrite_config(
                    jar, config_name, {item["mixin"] for item in pruned})

        if drop:
            kept_names = [n for n in mixin_configs(meta)
                          if any(m + ".class" not in drop for m in config_mixins(jar, n))]
            new_meta = dict(meta)
            if len(kept_names) != len(mixin_configs(meta)):
                new_meta["mixins"] = kept_names
                if not kept_names:
                    new_meta.pop("mixins")
            replacements["fabric.mod.json"] = json.dumps(new_meta, indent=2).encode()
        entry["payload"] = jar.without(drop, replacements)
        entry["kept_classes"] = {name for name in jar.classes
                                 if name + ".class" not in drop}
        entry["_jar"] = jar
        entry["_path"] = path
        entry["_drop"] = drop
        mods.append(entry)

    entrypoints = []
    for entry in mods:
        for kind in ("main", "client"):
            for name in entry["entrypoints"].get(kind, []):
                if isinstance(name, str):
                    entrypoints.append({"mod": entry["id"], "kind": kind, "class": name})
    return {"mods": mods, "entrypoints": entrypoints}


LOADER_TEMPLATE = '''package net.fabricmc.eaglerbridge;

import java.util.ArrayList;
import java.util.List;
{imports}
/**
 * Generated by modded/loader.py - do not edit.
 *
 * The loader. A browser client has no class loader and no jar scanner: the mods are
 * already compiled in, so all that is left is running their entrypoints, which is
 * what Fabric's loader does at startup too. No reflection, because the client is
 * ahead-of-time compiled: every mod is wired with a direct "new".
 */
public final class EaglerFabricLoader {{
    private static boolean initialized;
    private static final List<String> loaded = new ArrayList<>();

    private EaglerFabricLoader() {{
    }}

    /** Mod ids this client was built with - what FabricLoader.isModLoaded answers from. */
    public static boolean isLoaded(String id) {{
        return IDS.contains(id);
    }}

    public static List<String> loadedMods() {{
        return loaded;
    }}

    public static synchronized void ensureInitialized() {{
        if (initialized) {{
            return;
        }}
        initialized = true;
        bootstrap();
    }}

    /** Kept in its own method so the entrypoint calls stay reachable to the compiler. */
    private static void bootstrap() {{
        // During the build the mods are already being run by a real Fabric loader for
        // the class capture, so this is a no-op there and the real thing in the browser.
        if (System.getProperty("webmc.bake") != null) {{
            return;
        }}
{blocks}
        System.out.println("[EaglerFabric] mods initialised: " + loaded);
    }}

    private static void run(String id, Runnable entrypoint) {{
        try {{
            entrypoint.run();
            loaded.add(id);
        }} catch (Throwable error) {{
            System.out.println("[EaglerFabric] " + id + " failed to initialise: " + error);
            error.printStackTrace();
        }}
    }}

    private static final List<String> IDS = java.util.Arrays.asList({ids});
}}
'''


def generate_loader(plan: dict, src_dir: pathlib.Path) -> pathlib.Path:
    imports, blocks = set(), []
    for entry in plan["entrypoints"]:
        class_name = entry["class"]
        imports.add(f"import {class_name};")
        package, _, simple = class_name.rpartition(".")
        initializer = "onInitializeClient" if entry["kind"] == "client" else "onInitialize"
        interface = "ClientModInitializer" if entry["kind"] == "client" else "ModInitializer"
        imports.add(f"import net.fabricmc.api.{interface};")
        blocks.append(f'        run("{entry["mod"]}", () -> {{\n'
                      f'            {interface} mod = new {simple}();\n'
                      f'            mod.{initializer}();\n'
                      f'        }});')
    ids = ", ".join(f'"{entry["id"]}"' for entry in plan["mods"])
    source = LOADER_TEMPLATE.format(
        imports="\n".join(sorted(imports)) + ("\n" if imports else ""),
        blocks="\n".join(blocks) if blocks else "        // no mods in this build",
        ids=ids,
    )
    target = src_dir / "net" / "fabricmc" / "eaglerbridge" / "EaglerFabricLoader.java"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    return target


def report(plan: dict, game_classes: int) -> str:
    lines = ["# Build-time Fabric loader plan", "",
             f"client classes: {game_classes} | mods: {len(plan['mods'])} | "
             f"entrypoints: {len(plan['entrypoints'])}", "",
             "| mod | version | classes | entrypoints | mixin configs kept | mixins pruned |",
             "| --- | --- | --- | --- | --- | --- |"]
    for entry in plan["mods"]:
        entry_names = ", ".join(f"`{e['class'].split('.')[-1]}`" for e in plan["entrypoints"]
                                if e["mod"] == entry["id"]) or "—"
        pruned = sum(len(group["mixins"]) for group in entry["mixins_pruned"])
        lines.append(f"| **{entry['id']}** | {entry['version']} | {entry['classes']} | "
                     f"{entry_names} | {len(entry['mixins_kept'])} | {pruned} |")
    lines += ["", "## What each mod asks for", ""]
    for entry in plan["mods"]:
        lines.append(f"### {entry['name']} ({entry['id']} {entry['version']})")
        depends = entry["depends"]
        if depends:
            lines.append("depends: " + ", ".join(f"{k} {v}" for k, v in depends.items()))
        for group in entry["mixins_kept"]:
            lines.append(f"- mixin config `{group['config']}`: {group['mixins']} applied")
        for group in entry["mixins_pruned"]:
            lines.append(f"- ⚠️ mixin config `{group['config']}`: "
                         f"{len(group['mixins'])} dropped (integration with a mod that is "
                         f"not in this build)")
            for item in group["mixins"][:4]:
                lines.append(f"  - `{item['mixin'].split('/')[-1]}` — {item['why']}")
        for warning in entry.get("warnings", []):
            lines.append(f"- ⚠️ {warning}")
        lines.append("")
    lines += ["## Shims to compile (what the surviving mod classes call)", ""]
    lines.append("- packages: " + (", ".join(f"`{p}`" for p in plan["shims_compiled"]) or "none"))
    if plan["shims_missing"]:
        lines.append("- ⛔ not covered:")
        lines += [f"  - {item}" for item in plan["shims_missing"]]
    lines += ["", "## Loader entrypoints (what runs when the client starts)", ""]
    if not plan["entrypoints"]:
        lines.append("- none — this build has no code mods")
    for entry in plan["entrypoints"]:
        lines.append(f"- `{entry['class']}` ({entry['kind']}) from {entry['mod']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build-time Fabric loader front end")
    parser.add_argument("--mods", default="client-mods", help="folder of Fabric jars")
    parser.add_argument("--game-jar", default=None, help="the client jar the mods must fit")
    parser.add_argument("--out", default="build/loader", help="where the plan and payloads go")
    args = parser.parse_args()

    out = pathlib.Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    (out / "mods").mkdir(parents=True)
    (out / "overlay-mods").mkdir(parents=True)

    game_classes: set[str] = set()
    if args.game_jar and pathlib.Path(args.game_jar).is_file():
        game_classes = Jar(pathlib.Path(args.game_jar)).classes
        print(f"client jar: {len(game_classes)} classes")
    else:
        print("! no --game-jar: mixin targets cannot be checked against the client")

    paths = sorted(pathlib.Path(args.mods).glob("*.jar"))
    if not paths:
        # Not fatal: the bake falls back to Fabric API as a test mod, and the loader
        # then has nothing to start. Empty client-mods/ is a smoke test, not an error.
        print(f"! no jars in {args.mods} - building a client with no code mods")

    plan = plan_mods(paths, game_classes)

    needed = referenced_fabric_classes([(e["_path"], e["_jar"]) for e in plan["mods"]],
                                       {e["file"]: e["kept_classes"] for e in plan["mods"]})
    packages, missing = resolve_shims(needed)
    plan["shims_compiled"] = packages
    plan["shims_missing"] = missing


    # 1. the pruned jars the bake reads
    for entry in plan["mods"]:
        (out / "mods" / entry["file"]).write_bytes(entry["payload"])

    # 2. the mod classes that belong in the client jar
    for entry in plan["mods"]:
        with zipfile.ZipFile(io.BytesIO(entry["_jar"].data)) as archive:
            for name in archive.namelist():
                if name.endswith(".class") and name not in entry["_drop"]:
                    target = out / "overlay-mods" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))

    # 3. the shim sources the surviving mod classes need, ready to compile
    for package in plan["shims_compiled"]:
        for java in sorted((SHIM_ROOT / package).glob("*.java")):
            target = out / "shim-src" / package / java.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(java, target)

    # 4. the loader class
    source = generate_loader(plan, out / "src")

    for entry in plan["mods"]:
        entry.pop("_jar", None)
        entry.pop("kept_classes", None)
        entry.pop("_path", None)
        entry.pop("_drop", None)
        entry.pop("payload", None)
    plan["game_classes"] = len(game_classes)
    (out / "plan.json").write_text(json.dumps(plan, indent=2))
    text = report(plan, len(game_classes))
    (out / "REPORT.md").write_text(text)
    print(text)
    print(f"wrote {out}/mods, {out}/overlay-mods, {out}/src ({source.name}), {out}/plan.json")

    blockers, warnings = list(plan["shims_missing"]), []
    for entry in plan["mods"]:
        if not entry["classes"]:
            blockers.append(f"{entry['id']}: no classes in the jar")
        needed = entry["depends"].get("fabric-api")
        if needed:
            warnings.append(f"{entry['id']} depends on fabric-api ({needed}): only the "
                            "shimmed Fabric API modules will exist")
        if not entry["entrypoints"]:
            warnings.append(f"{entry['id']} declares no main/client entrypoint")
    for warning in warnings:
        print(f"! {warning}")
    if blockers:
        print("BLOCKED: " + "; ".join(blockers))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
