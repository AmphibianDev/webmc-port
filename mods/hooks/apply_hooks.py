#!/usr/bin/env python3
"""Wire the shim layer into a ported client workspace (the build-time loader's second step).

Copies modded/shims/** and modded/testmod/** into <workspace>/game/src/main/java, then
inserts the game-side hook calls that make the shims fire:

    Minecraft.tick()        end  -> EaglerFabricLoader.ensureInitialized() + END_CLIENT_TICK
    Screen.init()           end  -> ScreenEvents.AFTER_INIT
    Screen                  field-> adds eaglerGetRenderables() for Screens.getWidgets

Every insertion is marked with EAGLER_FABRIC_HOOK and is skipped if the marker is already
there, so running this twice is safe. If an anchor is missing the script fails loudly -
that is what catches a version bump that moved the code.

    python3 hooks/apply_hooks.py --workspace /path/to/Eaglercraft-26.2-Workspace
    python3 hooks/apply_hooks.py --workspace ... --dry-run
"""

import argparse
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
MODDED = HERE.parent

MARKER = "EAGLER_FABRIC_HOOK"
GAME = pathlib.Path("game/src/main/java")

TICK_HOOK = """      // {marker}: fires the shim events once per client tick
      net.fabricmc.eaglerbridge.EaglerFabricLoader.ensureInitialized();
      net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents.eaglerFireEndTick(this);
""".format(marker=MARKER)

SCREEN_HOOK = """      // {marker}: after a screen has initialised
      net.fabricmc.fabric.api.client.screen.v1.ScreenEvents.eaglerFireAfterInit(
         this.minecraft, this, this.width, this.height);
""".format(marker=MARKER)

SCREEN_ACCESSOR = """
   /** {marker}: accessor for Screens.getWidgets() */
   public java.util.List<Renderable> eaglerGetRenderables() {{
      return this.renderables;
   }}
""".format(marker=MARKER)


def find_body_span(text, signature):
    """(open_brace, close_brace) of the method whose declaration contains `signature`."""
    start = text.find(signature)
    if start < 0:
        return None
    open_brace = text.find("{", start)
    if open_brace < 0:
        return None
    depth, index = 0, open_brace
    in_str = in_char = in_line = in_block = False
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if in_line:
            if char == "\n":
                in_line = False
        elif in_block:
            if char == "*" and nxt == "/":
                in_block = False
                index += 1
        elif in_str:
            if char == "\\":
                index += 1
            elif char == '"':
                in_str = False
        elif in_char:
            if char == "\\":
                index += 1
            elif char == "'":
                in_char = False
        else:
            if char == "/" and nxt == "/":
                in_line = True
                index += 1
            elif char == "/" and nxt == "*":
                in_block = True
                index += 1
            elif char == '"':
                in_str = True
            elif char == "'":
                in_char = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return (open_brace, index)
        index += 1
    return None


def patch(workspace, relative, signature, insertion, dry_run, label):
    path = workspace / GAME / relative
    if not path.exists():
        print(f"  ❌ {relative}: file not found")
        return False
    text = path.read_text()
    if MARKER in text and insertion.strip() in text:
        print(f"  ↩︎  {label}: already applied")
        return True
    span = find_body_span(text, signature)
    if not span:
        print(f"  ❌ {label}: anchor {signature!r} not found in {relative}")
        return False
    _, close = span
    patched = text[:close] + insertion + text[close:]
    if dry_run:
        line = text[:close].count("\n") + 1
        print(f"  ✏️  {label}: would insert {len(insertion.splitlines())} line(s) at {relative}:{line}")
        return True
    path.write_text(patched)
    print(f"  ✅ {label}: inserted at {relative}:{text[:close].count(chr(10)) + 1}")
    return True


def add_accessor(workspace, relative, anchor_line, insertion, dry_run, label):
    path = workspace / GAME / relative
    text = path.read_text()
    if MARKER in text and "eaglerGetRenderables" in text:
        print(f"  ↩︎  {label}: already applied")
        return True
    if anchor_line not in text:
        print(f"  ❌ {label}: anchor line not found in {relative}")
        return False
    if dry_run:
        print(f"  ✏️  {label}: would add eaglerGetRenderables() to {relative}")
        return True
    text = text.replace(anchor_line, anchor_line + insertion, 1)
    path.write_text(text)
    print(f"  ✅ {label}: added eaglerGetRenderables() to {relative}")
    return True


def copy_sources(workspace, dry_run):
    copied = 0
    for folder in ("shims", "testmod"):
        root = MODDED / folder
        if not root.exists():
            continue
        for source in sorted(root.rglob("*.java")):
            target = workspace / GAME / source.relative_to(root)
            if dry_run:
                print(f"  ✏️  copy {source.relative_to(MODDED)} -> {target.relative_to(workspace)}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
    print(f"  ✅ copied {copied} java file(s) into {GAME}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    workspace = pathlib.Path(args.workspace).expanduser().resolve()
    if not (workspace / GAME).is_dir():
        print(f"❌ {workspace} does not look like a ported client workspace (no {GAME})")
        return 2

    print(f"workspace: {workspace}{' (dry run)' if args.dry_run else ''}")
    copy_sources(workspace, args.dry_run)
    ok = True
    ok &= patch(workspace, "net/minecraft/client/Minecraft.java", "public void tick()",
                TICK_HOOK, args.dry_run, "END_CLIENT_TICK")
    ok &= patch(workspace, "net/minecraft/client/gui/screens/Screen.java", "protected void init()",
                SCREEN_HOOK, args.dry_run, "ScreenEvents.AFTER_INIT")
    ok &= add_accessor(workspace, "net/minecraft/client/gui/screens/Screen.java",
                       "private final List<Renderable> renderables = Lists.newArrayList();",
                       SCREEN_ACCESSOR, args.dry_run, "Screens.getWidgets")
    print("hooks applied" if ok else "FAILED - see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
