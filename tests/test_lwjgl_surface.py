#!/usr/bin/env python3
"""Tests for tools/lwjgl_surface.py — the measurement of the LWJGL 3 gap.

    python3 tests/test_lwjgl_surface.py

Hermetic: it writes its own tiny class files (a client that references LWJGL, LWJGL-ish classes
that declare native methods) so nothing here needs a JDK, Minecraft, or the network.
"""

from __future__ import annotations

import pathlib
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import lwjgl_surface as surface  # noqa: E402

FAILURES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition or not detail else f" — {detail}"))
    if not condition:
        FAILURES += 1


def class_file(this_name: str, referenced: list[str], natives: list[str] = ()) -> bytes:
    """A minimal but valid class file: a constant pool, this class, and one native method per
    name. Written by hand so this test needs no JDK."""
    utf8: list[str] = []

    def utf8_index(text: str) -> int:
        """Pool index of a utf8 entry, adding it if it is new. Index 0 is never a pool entry."""
        if text in utf8:
            return utf8.index(text) + 1          # +1: pool indexes start at 1
        utf8.append(text)
        return len(utf8)                       # same thing, after appending

    this_utf8 = utf8_index(this_name)
    descriptors = [utf8_index("()V")] if natives else []
    native_names = [utf8_index(name) for name in natives]
    ref_utf8 = [utf8_index(name) for name in referenced]
    # If a native method happens to be named like a reference, the indexes above already agree.

    entries: list[bytes] = [b""]               # index 0 is reserved by the format
    # 1. every utf8 entry, in the order the indexes assume
    for text in utf8:
        entries.append(b"\x01" + struct.pack(">H", len(text)) + text.encode())
    this_class = len(entries)
    entries.append(b"\x07" + struct.pack(">H", this_utf8))
    ref_classes = []
    for index in ref_utf8:
        ref_classes.append(len(entries))
        entries.append(b"\x07" + struct.pack(">H", index))
    name_and_types = []
    for name_index in native_names:
        name_and_types.append((name_index, descriptors[0]))

    body = struct.pack(">HHH", 0x0021, this_class, this_class)   # public, super = this (a stub)
    body += struct.pack(">H", 0)                                  # no interfaces
    body += struct.pack(">H", 0)                                  # no fields
    methods = b""
    for name_index, descriptor_index in name_and_types:
        methods += struct.pack(">HHHH", 0x0109, name_index, descriptor_index, 0)  # public static native
    body += struct.pack(">H", len(name_and_types)) + methods
    body += struct.pack(">H", 0)                                  # no class attributes
    header = struct.pack(">IHH", 0xCAFEBABE, 0, 52)               # java 8 class file version
    return header + struct.pack(">H", len(entries)) + b"".join(entries) + body


def build_lwjgl_jar(path: pathlib.Path) -> None:
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("org/lwjgl/glfw/GLFW.class",
                     class_file("org/lwjgl/glfw/GLFW",
                                ["java/lang/Object"],
                                ["glfwInit", "glfwCreateWindow", "glfwPollEvents", "glfwSwapBuffers"]))
        jar.writestr("org/lwjgl/opengl/GL20.class",
                     class_file("org/lwjgl/opengl/GL20",
                                ["java/lang/Object"],
                                ["glCreateShader", "glShaderSource", "glCompileShader", "glUseProgram"]))
        jar.writestr("org/lwjgl/openal/AL10.class",
                     class_file("org/lwjgl/openal/AL10", ["java/lang/Object"], ["alSourcePlay"]))


def build_client_jar(path: pathlib.Path) -> None:
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("net/minecraft/client/Main.class",
                     class_file("net/minecraft/client/Main",
                                ["org/lwjgl/glfw/GLFW", "org/lwjgl/opengl/GL20", "java/lang/Object"]))
        jar.writestr("net/minecraft/client/Render.class",
                     class_file("net/minecraft/client/Render",
                                ["org/lwjgl/opengl/GL20", "org/lwjgl/openal/AL10", "java/lang/Object"]))


def main() -> int:
    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-lwjgl-"))
    client = work / "client.jar"
    lwjgl = work / "lwjgl"
    lwjgl.mkdir(parents=True, exist_ok=True)
    build_client_jar(client)
    build_lwjgl_jar(lwjgl / "lwjgl-3.4.1.jar")

    print("class reading")
    parsed = surface.ClassFile(zipfile.ZipFile(client).read("net/minecraft/client/Main.class"))
    check("a class file's references are read",
          {"org/lwjgl/glfw/GLFW", "org/lwjgl/opengl/GL20"} <= parsed.referenced(), str(sorted(parsed.referenced())))
    natives = surface.ClassFile(zipfile.ZipFile(lwjgl / "lwjgl-3.4.1.jar").read("org/lwjgl/glfw/GLFW.class")).native_methods()
    check("native methods are read", set(natives) == {"glfwInit", "glfwCreateWindow", "glfwPollEvents", "glfwSwapBuffers"},
          str(natives))

    print("\nclient scan")
    client_summary = surface.client_references(client)
    check("every org.lwjgl reference is counted", client_summary["matches"] == 4, str(client_summary["matches"]))
    check("they are grouped by module", "glfw (windowing, input, monitors)" in client_summary["modules"],
          str(client_summary["modules"]))
    check("a class referenced twice weighs twice", client_summary["modules"]["opengl (the GL entry points)"] == 2,
          str(client_summary["modules"]))
    check("and the GL module is bigger than the GLFW one",
          client_summary["modules"]["opengl (the GL entry points)"] > client_summary["modules"]["glfw (windowing, input, monitors)"])
    check("a missing client jar is reported, not crashed on",
          surface.client_references(work / "nope.jar").get("missing") is True)

    print("\nnative surface")
    native_summary = surface.lwjgl_natives(lwjgl, set(client_summary["items"]))
    check("only the classes the client uses are counted", native_summary["total"] == 9, str(native_summary["total"]))
    check("glfw contributes four methods", native_summary["modules"]["glfw (windowing, input, monitors)"]["count"] == 4)
    check("opengl contributes four", native_summary["modules"]["opengl (the GL entry points)"]["count"] == 4)
    check("audio is seen too", native_summary["modules"]["openal (audio)"]["methods"] == ["alSourcePlay"])
    check("the jars it read are named", native_summary["jars"] == ["lwjgl-3.4.1.jar"])

    print("\ncoverage comparison")
    js = work / "natives"
    js.mkdir()
    (js / "lwjgl2.js").write_text("export default { Java_org_lwjgl_opengl_GL11_glClear() {}, "
                                  "Java_org_lwjgl_opengl_GL11_glBegin() {} };\n")
    coverage = surface.coverage(js)
    check("an existing JS layer is counted", coverage["symbols"] == 2, str(coverage))
    check("and none of it covers GLFW 3", not any("glfwInit" in name for name in coverage["sample"]))
    check("no layer is fine too", surface.coverage(None) == {"checked": False})

    print("\nthe report")
    text = surface.report(client_summary, native_summary, coverage)
    check("it states the total", "**total: 9 native methods**" in text, text[-200:])
    check("it names the modules", "glfw (windowing, input, monitors)" in text and "opengl (the GL entry points)" in text)
    check("it says what the GLFW and GL rows are", "mechanical" in text)
    check("it warns when no LWJGL jars were read",
          "No LWJGL jars were read" in surface.report({"missing": True, "path": "x"}, {"modules": {}, "total": 0, "jars": []},
                                                      {"checked": False}))

    print("\nthe command")
    finished = subprocess.run([sys.executable, str(ROOT / "tools" / "lwjgl_surface.py"),
                               "--client", str(client), "--lwjgl", str(lwjgl), "--js", str(js), "--json"],
                              capture_output=True, text=True)
    check("it runs from the command line", finished.returncode == 0, finished.stderr[-200:])
    check("and prints json", '"total": 9' in finished.stdout, finished.stdout[:120])

    print()
    if FAILURES == 0:
        print("lwjgl surface tests passed")
    else:
        print(f"{FAILURES} lwjgl surface test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
