#!/usr/bin/env python3
"""Tests for the build-time Fabric loader (mods/build_modded_workspace.py).

    python3 mods/tests/test_workspace_loader.py

Hermetic: it builds a *synthetic* workspace (the game source the hooks look for, with the
anchors in place), a synthetic mod jar with a real Mixin class inside, and then checks that the
loader copies the shims, generates its loader class, patches the hooks, and refuses a workspace
or a mod set it cannot handle.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
MODS = HERE.parent
ROOT = MODS.parent
sys.path.insert(0, str(MODS))
sys.path.insert(0, str(MODS / "tests"))

FAILURES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition or not detail else f" — {detail}"))
    if not condition:
        FAILURES += 1


def class_file(this_name: str, referenced: list[str] = (), annotations: str = "") -> bytes:
    """A minimal class file. `annotations` is raw utf8 the pool must carry, so a Mixin's
    @Mixin(targets = "...") can be present for the scanner to read."""
    utf8: list[str] = []

    def index_of(text: str) -> int:
        if text in utf8:
            return utf8.index(text) + 1
        utf8.append(text)
        return len(utf8)

    index_of(this_name)
    for name in referenced:
        index_of(name)
    for text in ("Lorg/spongepowered/asm/mixin/Mixin;", "targets", "@@@", "Lorg/spongepowered/asm/mixin/injection/Inject;",
                 "method", "runTick", "at", "Lorg/spongepowered/asm/mixin/injection/At;", "value", "HEAD"):
        index_of(text)
    if annotations:
        index_of(annotations)

    entries: list[bytes] = [b""]
    for text in utf8:
        entries.append(b"\x01" + struct.pack(">H", len(text)) + text.encode())
    this_class = len(entries)
    entries.append(b"\x07" + struct.pack(">H", index_of(this_name)))
    body = struct.pack(">HHH", 0x0021, this_class, this_class)
    body += struct.pack(">H", 0)
    body += struct.pack(">H", 0)
    body += struct.pack(">H", 0)
    body += struct.pack(">H", 0)
    # one class attribute: the mixin annotation blob, so the scanner sees a target
    payload = annotations or this_name
    name_index = index_of("RuntimeInvisibleAnnotations") if "RuntimeInvisibleAnnotations" in utf8 else None
    if name_index is None:
        utf8.append("RuntimeInvisibleAnnotations")
        entries = [b""] + [b"\x01" + struct.pack(">H", len(t)) + t.encode() for t in utf8]
        this_class = len(entries)
        entries.append(b"\x07" + struct.pack(">H", index_of(this_name)))
        body = struct.pack(">HHH", 0x0021, this_class, this_class) + struct.pack(">H", 0) * 4
        name_index = len(utf8)
    blob = payload.encode()
    body += struct.pack(">H", 1) + struct.pack(">HH", name_index, len(blob)) + blob
    header = struct.pack(">IHH", 0xCAFEBABE, 0, 52)
    return header + struct.pack(">H", len(entries)) + b"".join(entries) + body


def build_workspace(path: pathlib.Path) -> None:
    """The three files the hook table anchors on, with their anchors present."""
    files = {
        "net/minecraft/client/Minecraft.java":
            "package net.minecraft.client;\npublic class Minecraft {\n"
            "  private void runTick(boolean bl) {\n    int x = 1;\n  }\n}\n",
        "net/minecraft/client/gui/screens/Screen.java":
            "package net.minecraft.client.gui.screens;\n"
            "import java.util.List;\nimport net.minecraft.client.gui.components.Renderable;\n"
            "import java.util.ArrayList;\n"
            "public class Screen {\n  private final List<Renderable> renderables = new ArrayList<>();\n"
            "  protected void init() {\n    int y = 2;\n  }\n}\n",
        "net/minecraft/client/KeyMapping.java":
            "package net.minecraft.client;\nimport java.util.Map;\npublic class KeyMapping {\n"
            "  public static final Map<String, KeyMapping> ALL = null;\n}\n",
    }
    for relative, text in files.items():
        target = path / "game" / "src" / "main" / "java" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def build_mod_jar(path: pathlib.Path, name: str, version: str, with_mixin: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("fabric.mod.json", json.dumps({
            "id": name, "version": version, "name": name,
            "entrypoints": {"client": [f"example.{name}.Client"]},
            "mixins": [f"{name}.mixins.json"] if with_mixin else [],
            "depends": {"minecraft": "26.2", "fabricloader": ">=0.15"},
        }))
        if with_mixin:
            jar.writestr(f"{name}.mixins.json", json.dumps({
                "required": True, "package": f"example.{name}.mixin",
                "client": ["HelloMixin"], "injectors": {"defaultRequire": 1},
            }))
            # the annotation payload carries the target, which is how the scanner reads targets
            jar.writestr(f"example/{name}/mixin/HelloMixin.class",
                         class_file(f"example/{name}/mixin/HelloMixin",
                                    ["net/minecraft/client/Minecraft"],
                                    annotations="Lorg/spongepowered/asm/mixin/Mixin;targets"
                                                "net/minecraft/client/Minecraft"))
        jar.writestr(f"example/{name}/Client.class", class_file(f"example/{name}/Client"))


def run(*args: str) -> tuple[int, str]:
    finished = subprocess.run([sys.executable, str(MODS / "build_modded_workspace.py"), *args],
                              capture_output=True, text=True)
    return finished.returncode, finished.stdout + finished.stderr


def main() -> int:
    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-workspace-"))
    try:
        workspace = work / "Eaglercraft-Workspace"
        workspace.mkdir()
        build_workspace(workspace)
        mods = work / "mods"
        build_mod_jar(mods / "hello-mod.jar", "hello", "1.0")

        print("a real workspace, a real mod jar")
        code, output = run("--workspace", str(workspace), "--mods", str(mods),
                           "--out", str(work / "plan"), "--dry-run")
        check("the loader runs", code == 0, output[-300:])
        check("it finds the game source and its anchors", "anchors the hooks need" in output)
        check("it reports the mod set", "hello 1.0" in output, output[:400])
        check("it counts the mixins it keeps", "mixins kept" in output)
        check("it copies the shim layer (dry run: says so)", "would be" in output or "copied" in output)
        check("it prints the build commands the engine owns", "compile:" in output and "package:" in output)

        print("\nthe real run")
        code, output = run("--workspace", str(workspace), "--mods", str(mods), "--out", str(work / "plan"))
        check("it succeeds", code == 0, output[-300:])
        shims = list((workspace / "game/src/main/java/net/fabricmc").rglob("*.java"))
        check("the Fabric API shims are in the workspace", len(shims) >= 5, f"{len(shims)} files")
        loader_class = workspace / "game/src/main/java/net/fabricmc/eaglerbridge/EaglerFabricLoader.java"
        check("the loader class was written", loader_class.is_file())
        if loader_class.is_file():
            text = loader_class.read_text()
            check("and it calls this mod's entrypoint directly",
                  "example.hello.Client" in text and "onInitializeClient" in text, text[-200:])
            check("with no reflection", "Class.forName" not in text)
        minecraft = (workspace / "game/src/main/java/net/minecraft/client/Minecraft.java").read_text()
        screen = (workspace / "game/src/main/java/net/minecraft/client/gui/screens/Screen.java").read_text()
        check("the tick hook is in the game code", "EAGLER_FABRIC_HOOK" in minecraft
              and "ensureInitialized" in minecraft, minecraft[-200:])
        check("the screen hook is too", "EAGLER_FABRIC_HOOK" in screen)
        check("the hooks are only inserted once", minecraft.count("ensureInitialized") == 1,
              str(minecraft.count("ensureInitialized")))

        print("\nrunning it again")
        code, output = run("--workspace", str(workspace), "--mods", str(mods), "--out", str(work / "plan"))
        minecraft_again = (workspace / "game/src/main/java/net/minecraft/client/Minecraft.java").read_text()
        check("a second run is safe (hooks are marked)", code == 0 and minecraft_again.count("ensureInitialized") == 1,
              str(minecraft_again.count("ensureInitialized")))

        print("\nthings it must refuse")
        built_client = work / "built-client"
        built_client.mkdir()
        (built_client / "index.html").write_text("<html>a compiled client</html>")
        code, output = run("--workspace", str(built_client), "--mods", str(mods), "--out", str(work / "p2"))
        check("a built client is refused, with the reason", code == 2 and "built client" in output.lower(),
              output[-200:])

        moved = work / "moved-anchors"
        moved.mkdir()
        build_workspace(moved)
        minecraft_path = moved / "game/src/main/java/net/minecraft/client/Minecraft.java"
        minecraft_path.write_text("package net.minecraft.client;\npublic class Minecraft { void tick() {} }\n")
        code, output = run("--workspace", str(moved), "--mods", str(mods), "--out", str(work / "p3"))
        check("a workspace whose anchor moved is refused", code == 2, output[-200:])
        check("and the missing anchor is named", "runTick" in output)

        empty_mods = work / "no-mods"
        empty_mods.mkdir()
        code, output = run("--workspace", str(workspace), "--mods", str(empty_mods), "--out", str(work / "p4"))
        check("an empty mod set is not an error", code == 0, output[-200:])
        empty_plan = json.loads((work / "p4" / "plan.json").read_text())
        generated = (work / "p4" / "src" / "net/fabricmc/eaglerbridge/EaglerFabricLoader.java")
        check("and the generated loader says a build has no mods",
              empty_plan["mods"] == [] and generated.is_file()
              and "no mods in this build" in generated.read_text(), "")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILURES == 0:
        print("workspace loader tests passed")
    else:
        print(f"{FAILURES} workspace loader test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
