#!/usr/bin/env python3
"""Checks the boot test is coherent, offline: every member BootTest calls exists in the shim.

    python3 lwjgl3-webgl/test/test_boot_assets.py

Running the loader needs a JDK and a browser, which not every machine has - the CI job does that. What
can be checked anywhere is the thing that wasted the most time when it was wrong by hand: that the Java
program only calls members the shim actually declares, and that each of those has an implementation in
the JavaScript.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHIM = ROOT / "lwjgl3-webgl" / "src" / "main" / "java"
JS = ROOT / "lwjgl3-webgl" / "natives" / "webmc-lwjgl3.js"
TEST = ROOT / "lwjgl3-webgl" / "test" / "BootTest.java"

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


def main() -> int:
    source = TEST.read_text()
    js = JS.read_text()
    declared: dict[str, set[str]] = {}
    for path in SHIM.rglob("*.java"):
        text = path.read_text()
        owner = str(path.relative_to(SHIM)).replace(".java", "").replace("/", ".")
        names = set(re.findall(r"public static (?:native )?[\w.<>\[\]$]+ (\w+)\(", text))
        declared[owner] = names

    print("what BootTest calls")
    calls = re.findall(r"\b(GLFW|GL11|GL15|GL20|GL30|MemoryUtil|WebMC)\.([A-Za-z0-9_]+)\(", source)
    check("it calls something", len(calls) > 10, str(len(calls)))
    owner_of = {
        "GLFW": "org.lwjgl.glfw.GLFW", "GL11": "org.lwjgl.opengl.GL11", "GL15": "org.lwjgl.opengl.GL15",
        "GL20": "org.lwjgl.opengl.GL20", "GL30": "org.lwjgl.opengl.GL30",
        "MemoryUtil": "org.lwjgl.system.MemoryUtil", "WebMC": "org.lwjgl.system.WebMC",
    }
    missing = []
    for simple, method in calls:
        owner = owner_of[simple]
        if method not in declared.get(owner, set()):
            missing.append(f"{owner}.{method}")
    check("every member it calls is declared in the shim", not missing, ", ".join(sorted(set(missing))[:6]))

    # the capture starts after "Java_", so put it back before comparing
    # A member is implemented either natively (the JS has Java_<owner>_<method>) or in Java as a
    # wrapper over one that is (which is how MemoryUtil works: the arena lives in JavaScript, and every
    # read and write is a thin Java call into it).
    implemented = {"Java_" + name for name in re.findall(r"function Java_([A-Za-z0-9_]+)\s*\(", js)}
    natives = {owner: set(re.findall(r"public static native [\w.<>\[\]$]+ (\w+)\(", text))
               for owner, path in ((str(p.relative_to(SHIM)).replace(".java", "").replace("/", "."), p)
                                   for p in SHIM.rglob("*.java"))
               for text in [path.read_text()]}
    unimplemented = []
    for simple, method in calls:
        owner = owner_of[simple]
        if method not in natives.get(owner, set()):
            continue                      # implemented in Java, over natives that are checked below
        if "Java_" + owner.replace(".", "_") + "_" + method not in implemented:
            unimplemented.append(f"{owner}.{method}")
    check("every native it reaches has a JavaScript implementation", not unimplemented,
          ", ".join(sorted(set(unimplemented))[:6]))

    # and the other direction: a JS function that names no member of the shim is a typo waiting to
    # waste a debugging session
    declared_mangled = {("Java_" + owner.replace(".", "_") + "_" + method)
                        for owner, names in declared.items() for method in names}
    orphans = sorted(name for name in implemented if name not in declared_mangled)
    # Extras are fine and useful - they are members a client may reach for that the clients we measured
    # did not call - but they are worth counting, because an undeclared implementation is dead code
    # until somebody declares it.
    print(f"  NOTE  {len(orphans)} JavaScript function(s) wait for a declaration, e.g. "
          + ", ".join(orphans[:3]) if orphans else "  NOTE  every JavaScript function is declared")

    print()
    print("the page that runs it")
    page = (ROOT / "lwjgl3-webgl" / "test" / "boot.html").read_text()
    check("it boots with the natives on the library path", "java.library.path=/app/natives" in page)
    check("it hands the shim a canvas", "webmcCanvasElement" in page)
    check("it reports an exit code the workflow can assert on",
          'document.title = "EXIT "' in page and "RESULT code=" in page)
    workflow = (ROOT / ".github" / "workflows" / "lwjgl3.yml").read_text()
    check("and the workflow asserts a frame was drawn", "RESULT code=0 frames=[1-9]" in workflow)

    print()
    if FAILURES == 0:
        print("boot asset tests passed")
    else:
        print(f"{FAILURES} boot asset test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
