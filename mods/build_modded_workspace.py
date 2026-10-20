#!/usr/bin/env python3
"""The build-time Fabric loader for a source workspace (the only kind that can exist).

    python3 mods/build_modded_workspace.py --workspace /path/to/Eaglercraft-26.2-Workspace \
        --mods mods/samples --out work/modded --dry-run
    python3 mods/build_modded_workspace.py --workspace ... --mods ... --out ...   # do it

Why it has to be like this: a browser client is compiled ahead of time. There is no class
loader in the tab, so nothing can read a `.jar` at startup and Mixin has no class-load to hook.
"Loading Fabric mods" therefore means, exactly as it does in the real Fabric workflow:

    quit the game -> put the jars in -> start again

with the *start* being a build. This tool is the loader for that build:

  1. scan   - every mod's id, entrypoints, mixin configs, and the Mixins whose target class is
              not in this client (integrations with mods that are not built in). It prunes
              those from the mod jars and from the configs, because a `required: true` config
              that still lists them stops the bake instead of being skipped.
  2. shims  - copy the Fabric API shim layer into the workspace's game source
  3. hooks  - patch the game's own files so the shims actually fire (client tick, screen init,
              keybinds), driven by hooks/apply_hooks.py, which fails loudly if a version bump
              moved an anchor
  4. loader - generate net.fabricmc.eaglerbridge.EaglerFabricLoader with one direct entrypoint
              call per mod. No reflection: the client is ahead-of-time compiled, so reflective
              class loading is not something the compiler can promise.
  5. report - and then print the two commands that build the client, which is the expensive part

This is step 1 through 4 of 5. Compiling (gradle + the wasm toolchain) stays with the engine,
because that is where the version-specific platform layer lives.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import loader as mod_loader  # noqa: E402  (the planner: scan, prune, generate)

GAME_SOURCE = pathlib.Path("game/src/main/java")
GENERATED_LOADER = pathlib.Path("net/fabricmc/eaglerbridge/EaglerFabricLoader.java")


def run_plan(mods: pathlib.Path, game_jar: pathlib.Path | None, out: pathlib.Path,
             workspace: pathlib.Path | None) -> dict:
    argv = [sys.executable, str(HERE / "loader.py"), "--mods", str(mods), "--out", str(out)]
    if game_jar:
        argv += ["--game-jar", str(game_jar)]
    finished = subprocess.run(argv, capture_output=True, text=True)
    plan_path = out / "plan.json"
    if not plan_path.is_file():
        raise SystemExit(f"the planner produced no plan:\n{finished.stdout}\n{finished.stderr}")
    return json.loads(plan_path.read_text())


def copy_shims(workspace: pathlib.Path, dry_run: bool) -> list[str]:
    """The Fabric API shim layer goes into the game source, where the mods' calls will find it."""
    target = workspace / GAME_SOURCE / "net/fabricmc"
    copied = []
    for source in sorted((HERE / "shims").rglob("*.java")):
        relative = source.relative_to(HERE / "shims")
        copied.append(str(relative))
        if not dry_run:
            destination = workspace / GAME_SOURCE / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return copied


def copy_loader_class(workspace: pathlib.Path, plan_out: pathlib.Path, dry_run: bool,
                      plan: dict) -> pathlib.Path | None:
    """The generated loader class has to exist before the hooks reference it."""
    source = plan_out / "src" / GENERATED_LOADER
    if not source.is_file():
        generated = mod_loader.generate_loader(plan, plan_out / "src")
        source = generated
    target = workspace / GAME_SOURCE / GENERATED_LOADER
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return target


def apply_hooks(workspace: pathlib.Path, dry_run: bool) -> tuple[int, str]:
    argv = [sys.executable, str(HERE / "hooks" / "apply_hooks.py"), "--workspace", str(workspace)]
    if dry_run:
        argv.append("--dry-run")
    finished = subprocess.run(argv, capture_output=True, text=True)
    return finished.returncode, finished.stdout + finished.stderr


def workspace_check(workspace: pathlib.Path) -> list[str]:
    """Is this actually a ported workspace? The hooks need the game's source, not a build."""
    problems = []
    if not (workspace / GAME_SOURCE).is_dir():
        problems.append(f"{workspace / GAME_SOURCE} does not exist (is this a source workspace, "
                        "or a built client? a built client cannot take mods)")
    for relative, needle in (("net/minecraft/client/Minecraft.java", "runTick"),
                             ("net/minecraft/client/gui/screens/Screen.java", "protected void init()"),
                             ("net/minecraft/client/KeyMapping.java", "ALL")):
        path = workspace / GAME_SOURCE / relative
        if not path.is_file():
            problems.append(f"{relative} is missing")
        elif needle not in path.read_text(errors="replace"):
            problems.append(f"{relative} has no {needle!r} to hook")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Load Fabric mods into a source workspace, at build time")
    parser.add_argument("--workspace", required=True, help="the ported source tree to patch")
    parser.add_argument("--mods", required=True, help="folder of Fabric jars")
    parser.add_argument("--out", default=str(ROOT / "work" / "modded"), help="where the plan goes")
    parser.add_argument("--game-jar", default=None, help="the client jar, so mixin targets can be checked")
    parser.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    args = parser.parse_args()

    workspace = pathlib.Path(args.workspace).expanduser().resolve()
    mods = pathlib.Path(args.mods).expanduser().resolve()
    out = pathlib.Path(args.out).expanduser().resolve()
    jar = pathlib.Path(args.game_jar).expanduser().resolve() if args.game_jar else None

    print(f"workspace: {workspace}")
    print(f"mods:      {mods}")
    print(f"plan:      {out}")
    print()

    print("== the workspace")
    problems = workspace_check(workspace)
    for problem in problems:
        print(f"  ✗ {problem}")
    if problems:
        print("\nA built client cannot take mods: this step needs the source tree the engine compiles.\n"
              "Point --workspace at a workspace (or at the folder the patcher kit extracted).")
        return 2
    print("  ✓ game source found, with the anchors the hooks need")

    print("\n== the mod set")
    plan = run_plan(mods, jar, out, workspace)
    for entry in plan["mods"]:
        pruned = sum(len(group["mixins"]) for group in entry["mixins_pruned"])
        kept = sum(group["mixins"] for group in entry["mixins_kept"])
        print(f"  {entry['id']} {entry['version']}: {kept} mixins kept, {pruned} pruned"
              f"{', entrypoints: ' + str(len(entry['entrypoints'])) if entry['entrypoints'] else ''}")
    if plan.get("shims_missing"):
        print("  ⛔ a Fabric API class no shim implements:")
        for item in plan["shims_missing"]:
            print(f"     {item}")
        return 3

    print("\n== the shim layer")
    copied = copy_shims(workspace, args.dry_run)
    print(f"  {len(copied)} file(s) {'would be' if args.dry_run else ''} copied into {GAME_SOURCE}/net/fabricmc")

    print("\n== the loader class")
    generated = copy_loader_class(workspace, out, args.dry_run, plan)
    if generated:
        print(f"  {generated.relative_to(workspace)} "
              f"{'would be written' if args.dry_run else 'written'} "
              f"({len(plan['entrypoints'])} entrypoint(s))")

    print("\n== the game-side hooks")
    code, output = apply_hooks(workspace, args.dry_run)
    for line in output.strip().splitlines():
        print(f"  {line}")
    if code != 0:
        print("\nA hook anchor moved, which is exactly what this check is for: the version's")
        print("source is not what the hook table expects, so a mod would silently never fire.")
        return 4

    print("\n== what is left (the expensive part, and the engine's job)")
    engine = None
    ports = json.loads((ROOT / "ports.json").read_text())
    for candidate in ports["engines"]:
        if candidate.get("kind") == "eagler-workspace":
            engine = candidate
            break
    if engine:
        if not engine.get("enabled", True):
            # A disabled engine is not a build option, and saying so here is the point: the workspace
            # lineage that this loader targets is the one whose licence has to be settled first.
            print(f"  ! {engine['id']} is disabled: {engine.get('blocked_by', 'no reason given')}")
        for step in ("compile", "link", "package"):
            command = (engine.get("build") or {}).get(step)
            if command:
                print(f"  {step}: {command}")
    print(f"\n{'dry run: nothing was changed' if args.dry_run else 'done: the workspace now has the mod set in it'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
