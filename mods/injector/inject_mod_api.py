#!/usr/bin/env python3
"""The injector: splice the JavaScript mod API into an already-built browser client.

    python3 mods/injector/inject_mod_api.py work/builds/1.12.2-eaglerforge/"EaglerForge 1.12.2.html"
    python3 mods/injector/inject_mod_api.py --all            # every build under work/builds/
    python3 mods/injector/inject_mod_api.py --all --dry-run  # what it would do, and how deep it can hook

What it does, in order:

  1. reads the build and asks what it is dealing with: a JavaScript build (game logic in JS, which
     can be hooked by name) or a WebAssembly-GC / minified one (page-tier mods only). It looks for
     the shapes a JS build has: named game classes, an existing mod API, a TeaVM or Eaglercraft
     banner. That verdict is printed per build and written into the patch, so a mod author can see
     what they have before writing code.
  2. splices the API in as early as the page allows (right after <head>, or <body>, or at the very
     front), because a mod API should exist before the client's own code runs.
  3. where the build is a JavaScript build, places source-level hook calls at the anchors it
     recognises and reports how many it placed - the deep tier - so mods can ride the game's tick
     instead of a requestAnimationFrame loop.
  4. writes <name>.modded.html next to the input (or under --out), and never touches the original.

It is idempotent: running it on a patched file replaces the previous patch instead of stacking a
second one. Nothing here needs a JVM, and nothing here can load a `.jar` - see mods/README.md.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
API = HERE / "api.js"
MARKER_BEGIN = "<!-- WebMC mod API (injected) -->"
MARKER_END = "<!-- /WebMC mod API -->"

# Source-level anchors: places a JavaScript build keeps its game loop. Each is a regex and the
# statement to place after it. These are the deep tier; if none match, the build is page-tier only.
ANCHORS = [
    (re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*new\s+net\.minecraft\.client\.Minecraft\b"),
     "loader-hook", "window.WebMCModAPI.emit('load', { deep: true })"),
    (re.compile(r"\bnet\.minecraft\.client\.Minecraft\s*\.\s*prototype\s*\.\s*(?:runTick|tick)\s*=\s*function"),
     "tick-hook", "window.WebMCModAPI.emit('tick', { deep: true })"),
    (re.compile(r"function\s+(?:runTick|tick)\s*\(\s*\)\s*\{"),
     "tick-hook", "window.WebMCModAPI.emit('tick', { deep: true })"),
    (re.compile(r"class\s+Minecraft\b[^{]*\{"),
     "class-hook", "window.WebMCModAPI.emit('load', { deep: true })"),
]

JS_BUILD_MARKERS = (
    "net.minecraft.client",
    "net/minecraft/client",
    "teavm",
    "eaglercraft",
    "Eaglercraft",
)
WASM_MARKERS = (
    "WebAssembly.instantiate",
    "wasm-gc",
    "WASM-GC",
    ".wasm",
)
MOD_API_MARKERS = ("ModAPI", "EaglerForge", "globalThis.ModAPI")


def classify(source: str) -> dict:
    """What kind of build is this, and which tier of mods can it take?

    The distinction that matters: a build whose JavaScript still names its game classes can be
    hooked by name (the deep tier). A build that compiled its game to WebAssembly, or minified the
    names away, cannot: nothing in the page refers to `net.minecraft.*` any more, so there is no
    anchor to place a hook at. That is a fact about the build, and the report says which one it is
    rather than pretending both are equally moddable.
    """
    head = source[:400_000]                       # cheap: the interesting banners are early
    sample = source[:4_000_000]
    found_wasm = [marker for marker in WASM_MARKERS if marker in sample]
    found_js = [marker for marker in JS_BUILD_MARKERS if marker in head]
    existing = [marker for marker in MOD_API_MARKERS if marker in head]
    named_classes = len(re.findall(r"\bnet\.minecraft\.[A-Za-z0-9_.]+", head))
    # The question is not "how many names are visible" but "can a hook actually be placed". Counting
    # names got this wrong: one long reference chain can look like one name, and a build with two
    # names may still have the very method a hook needs.
    anchor_hits = [name for pattern, name, _ in ANCHORS if pattern.search(sample)]
    # minified: very long lines and no readable package names left
    longest_line = max((len(line) for line in sample.splitlines()[:2000]), default=0)
    minified = longest_line > 20_000 and named_classes < 3
    if existing and anchor_hits:
        kind = "build with a mod api and hookable code"
    elif existing:
        kind = "build with a mod api (game code not hookable)"
    elif anchor_hits:
        kind = "javascript build with hookable code"
    elif minified:
        kind = "minified javascript build"
    elif found_wasm:
        kind = "webassembly-gc build"
    else:
        kind = "build of unknown shape"
    return {
        "javascript_markers": found_js[:6],
        "wasm_markers": found_wasm[:4],
        "existing_mod_api": existing[:3],
        "named_game_classes": named_classes,
        "longest_line": longest_line,
        "anchors_found": sorted(set(anchor_hits)),
        "deep": bool(anchor_hits),
        "kind": kind,
    }


def inject_api(source: str, api: str) -> tuple[str, str]:
    """Place the API as early as the page allows. Returns (patched, where)."""
    block = f"\n{MARKER_BEGIN}\n<script>\n{api}\n</script>\n{MARKER_END}\n"
    for tag in ("<head", "<body"):
        match = re.search(re.escape(tag) + r"[^>]*>", source, re.IGNORECASE)
        if match:
            return source[:match.end()] + block + source[match.end():], tag
    return block + source, "document start"


def place_hooks(source: str) -> tuple[str, list[dict]]:
    """Insert the deep-tier hook calls at the anchors this build actually has."""
    placed = []
    for pattern, name, statement in ANCHORS:
        match = pattern.search(source)
        if not match:
            continue
        if statement in source:
            placed.append({"anchor": name, "placed": False, "why": "already there"})
            continue
        insert_at = source.find("{", match.end() - 1)
        if insert_at == -1:
            placed.append({"anchor": name, "placed": False, "why": "no body to hook"})
            continue
        line_end = source.find("\n", insert_at)
        line_end = line_end if line_end != -1 else insert_at
        # emit only if the API exists by then; the guard keeps a broken page harmless
        call = f"\n  try {{ {statement} }} catch (e) {{}}"
        source = source[:line_end] + call + source[line_end:]
        placed.append({"anchor": name, "placed": True, "why": "inside the matched block"})
    return source, placed


def patch(path: pathlib.Path, out_dir: pathlib.Path | None, dry_run: bool) -> dict:
    source = path.read_text(errors="replace")
    verdict = classify(source)
    report = {"file": str(path), **verdict, "hooks": [], "output": None, "state": "planned"}

    # idempotence: drop any previous patch before adding another
    if MARKER_BEGIN in source:
        source = re.sub(re.escape(MARKER_BEGIN) + r"[\s\S]*?" + re.escape(MARKER_END), "", source, count=1)
        report["replaced_previous_patch"] = True

    api = API.read_text()
    source, where = inject_api(source, api)
    report["api_placed_after"] = where

    if verdict["deep"]:
        source, hooks = place_hooks(source)
        report["hooks"] = hooks
        report["hooks_placed"] = sum(1 for hook in hooks if hook["placed"])
    else:
        report["hooks_placed"] = 0

    if dry_run:
        return report
    target_dir = out_dir or path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (path.stem + ".modded.html")
    target.write_text(source)
    report["output"] = str(target)
    report["state"] = "patched"
    return report


def builds_under(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(path for path in root.rglob("*.html") if ".modded." not in path.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Add the JavaScript mod API to a built client")
    parser.add_argument("build", nargs="?", help="one HTML build")
    parser.add_argument("--all", action="store_true", help="every build under work/builds/")
    parser.add_argument("--builds-dir", default=str(ROOT / "work" / "builds"))
    parser.add_argument("--out", default=str(ROOT / "work" / "builds-modded"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out)
    if args.all:
        targets = builds_under(pathlib.Path(args.builds_dir))
    elif args.build:
        targets = [pathlib.Path(args.build)]
    else:
        parser.error("give a build file or --all")
        return 2
    if not targets:
        print(f"no builds under {args.builds_dir} - run tools/fetch_builds.py first")
        return 2

    print(f"{len(targets)} build(s)\n")
    print(f"  {'build':<46} {'what it is':<28} {'deep hooks':<11} existing mod api")
    print(f"  {'-' * 46} {'-' * 28} {'-' * 11} {'-' * 16}")
    patched = deep = 0
    for path in targets:
        per_build_out = out_dir / path.parent.name if path.parent.name != "builds" else out_dir
        report = patch(path, per_build_out, args.dry_run)
        name = path.name if len(path.name) < 44 else path.name[:41] + "..."
        api = ", ".join(report["existing_mod_api"]) or "—"
        print(f"  {name:<46} {report['kind']:<28} "
              f"{('yes (' + str(report['hooks_placed']) + ')' if report['deep'] else 'page tier'):<11} {api}")
        patched += report["state"] == "patched"
        deep += 1 if report["deep"] else 0
    print(f"\n{patched} {'would be patched' if args.dry_run else 'patched'}, "
          f"{deep} with deep hooks, {len(targets) - deep} page-tier only")
    if not args.dry_run:
        print(f"output: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
