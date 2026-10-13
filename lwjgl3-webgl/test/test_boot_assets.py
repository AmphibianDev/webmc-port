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
    print("signatures, since that is what kept breaking")
    signatures = {}
    for path in SHIM.rglob("*.java"):
        owner = str(path.relative_to(SHIM)).replace(".java", "").replace("/", ".")
        for match in re.finditer(r"public static (?:native )?[\w.<>\[\]$]+ (\w+)\(([^)]*)\)", path.read_text()):
            arity = 0 if not match.group(2).strip() else len(match.group(2).split(","))
            signatures.setdefault((owner, match.group(1)), []).append(arity)
    wrong = []
    for call in re.finditer(r"\b(GLFW|GL11|GL15|GL20|GL30|MemoryUtil)\.([A-Za-z0-9_]+)\(([^;]*?)\)", source):
        simple, method, arguments = call.group(1), call.group(2), call.group(3)
        owner = owner_of[simple]
        # constants and nested calls make exact counting impossible here, so this counts top-level commas
        arity = 0 if not arguments.strip() else len(re.split(r",(?![^(]*\))", arguments))
        known = signatures.get((owner, method))
        if known and arity not in known:
            wrong.append(f"{owner}.{method} called with {arity}, declared {sorted(known)}")
    check("every call passes the number of arguments the shim declares", not wrong, "; ".join(wrong[:4]))

    print()
    print("the page that runs it")
    page = (ROOT / "lwjgl3-webgl" / "test" / "boot.html").read_text()
    # the registrations: CheerpJ 4 takes natives as an option, not through java.library.path
    check("it registers the natives with cheerpjInit", "natives," in page and "import(" in page)
    # a module specifier without ./ is a bare specifier, and browsers refuse it - which is exactly what
    # happened on Pages while CI kept working, because there the path looked absolute
    check("the shim is imported by a resolved URL, not a bare specifier",
          'new URL("natives/webmc-lwjgl3.js", import.meta.url)' in page)
    check("and so is the jar it runs", 'new URL("app.jar", import.meta.url)' in page)
    play = (ROOT / "site" / "loader-play.html").read_text()
    check("there is a page that starts the client in its own tab",
          "requestFullscreen" in play and "cheerpjRunMain" in play)
    check("and the test page offers it", 'href="play.html"' in page and 'target="_blank"' in page)
    workflow = (ROOT / ".github" / "workflows" / "catalog.yml").read_text()
    check("the published loader includes it", "site/loader/play.html" in workflow)
    check("it asks for Java 17 mode", "version: 17" in page)
    check("it hands the shim a canvas", "webmcCanvasElement" in page)
    check("the Java program can report through the shim, not only through a console nobody reads",
          "WebMC.log(" in (ROOT / "lwjgl3-webgl" / "test" / "BootTest.java").read_text()
          and "Java_org_lwjgl_system_WebMC_log" in JS.read_text())
    play = (ROOT / "site" / "loader-play.html").read_text()
    check("and the page it starts from shows those lines", "window.webmc.log()" in play)
    check("it shows them while the run is happening, not only after it ends",
          "setInterval" in play and "printed" in play)
    check("it reports over HTTP, with the stage, so a stuck page says where",
          'fetch("/result"' in page and "at(\"" in page and "setInterval" in page)
    workflow = (ROOT / ".github" / "workflows" / "lwjgl3.yml").read_text()
    check("and the workflow asserts a real result arrived",
          "d.get('code')==0 and d.get('frames',0)>0" in workflow)
    check("it compiles for the Java version the browser JVM runs", "-release 17" in workflow)

    print()
    if FAILURES == 0:
        print("boot asset tests passed")
    else:
        print(f"{FAILURES} boot asset test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
