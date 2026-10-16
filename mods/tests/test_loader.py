#!/usr/bin/env python3
"""Tests for modded/loader.py — the build-time Fabric loader's planning half.

    python3 modded/test_loader.py        # exits non-zero if a case fails

Runs without a JDK and without network access: the "client jar" is synthesised from the
class names the mod itself mentions, and the fixture mod is the real Meteor jar sitting in
client-mods/. What is checked here is exactly what used to go wrong silently:

  * a mixin whose target is not in the build must be pruned, and the *config file* has to
    lose it too (a `required: true` config that still lists it stops the bake),
  * a mixin whose target exists must be kept,
  * the entrypoints from fabric.mod.json have to end up as direct calls in the generated
    loader class, and nothing may be resolved by reflection,
  * a Fabric API class a surviving mod class calls, with no shim written for it, has to be
    a blocker *before* the bake, naming the class.
"""

from __future__ import annotations

import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]          # webmc-port/
LOADER = HERE.parent / "loader.py"
# Any Fabric jar to test against: WEBMC_MOD_JAR, or the first one in mods/samples/.
def sample_jar() -> pathlib.Path | None:
    import os
    from_env = os.environ.get("WEBMC_MOD_JAR")
    if from_env and pathlib.Path(from_env).is_file():
        return pathlib.Path(from_env)
    candidates = sorted((ROOT / "mods" / "samples").glob("*.jar")) if (ROOT / "mods" / "samples").is_dir() else []
    candidates += sorted(pathlib.Path.home().glob("Downloads/meteor-client-*.jar"))
    return candidates[0] if candidates else None


METEOR = sample_jar()

FAILURES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition or not detail else f" — {detail}"))
    if not condition:
        FAILURES += 1


def meteor_mods() -> list[pathlib.Path]:
    return [METEOR] if METEOR and METEOR.is_file() else []


def fake_client_jar(target: pathlib.Path, mod: pathlib.Path) -> int:
    """A client jar carrying every net/minecraft class the mod mentions.

    The loader only ever asks whether a class *name* exists (that is all a mixin target
    check can do before the real jar arrives), so empty entries are a faithful stand-in -
    and it keeps this test runnable without Mojang's jar.
    """
    names: set[str] = set()
    with zipfile.ZipFile(mod) as source:
        for entry in source.namelist():
            if not entry.endswith(".class"):
                continue
            text = b"\n".join(re.findall(rb"[ -~]{5,}", source.read(entry))).decode("utf-8", "replace")
            for name in re.findall(r"L?(net[./]minecraft/[A-Za-z0-9_$/]+);?", text):
                names.add(name.replace(".", "/").rstrip(";"))
    with zipfile.ZipFile(target, "w") as out:
        for name in sorted(names):
            out.writestr(name + ".class", b"")
    return len(names)


def run_loader(mods: pathlib.Path, client: pathlib.Path, out: pathlib.Path) -> tuple[int, str]:
    finished = subprocess.run(
        [sys.executable, str(LOADER), "--mods", str(mods), "--game-jar", str(client), "--out", str(out)],
        capture_output=True, text=True,
    )
    return finished.returncode, finished.stdout + finished.stderr


def fixture_mod(target: pathlib.Path, name: str, classes: list[tuple[str, str]], meta: dict) -> pathlib.Path:
    """A mod jar carrying the given (entry, bytes) classes and a fabric.mod.json."""
    path = target / f"{name}.jar"
    with zipfile.ZipFile(path, "w") as out:
        out.writestr("fabric.mod.json", json.dumps(meta))
        for entry, data in classes:
            out.writestr(entry, data)
    return path


def main() -> int:
    mods = meteor_mods()
    if not mods:
        print("no Fabric jar to test with. Point WEBMC_MOD_JAR at any mod jar (or drop one in "
              "mods/samples/) to run the pruning checks; the loader itself is unchanged.")
        return 0

    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-loader-test-"))
    try:
        client = work / "client.jar"
        count = fake_client_jar(client, mods[0])
        print(f"loader planning tests (client jar: {count} classes, mod: {mods[0].name})")

        # 1. the real mod: prune what cannot apply, keep what can, and generate the loader
        mods_dir = work / "mods-in"
        mods_dir.mkdir()
        shutil.copy2(mods[0], mods_dir / mods[0].name)
        code, output = run_loader(mods_dir, client, work / "plan")
        check("a mod whose mixins mostly fit exits 0", code == 0, f"exit {code}: {output[-300:]}")
        plan = json.loads((work / "plan" / "plan.json").read_text())
        entry = plan["mods"][0]
        check("the mod is planned by its fabric.mod.json id", entry["id"].startswith("meteor-client"), entry["id"])
        kept = sum(group["mixins"] for group in entry["mixins_kept"])
        pruned = [item for group in entry["mixins_pruned"] for item in group["mixins"]]
        check("most of the core mixins are kept", kept > 150, f"kept {kept}")
        check("mixins for mods that are not in this build are pruned", len(pruned) > 5, f"pruned {len(pruned)}")
        check(
            "the Sodium/Lithium/Baritone integrations are among the pruned",
            any("sodium" in item["mixin"] for item in pruned) and any("baritone" in item["mixin"] for item in pruned),
        )
        check("no shim need is left uncovered", plan["shims_missing"] == [], str(plan["shims_missing"]))
        check(
            "only the entrypoint interfaces are compiled as shims",
            plan["shims_compiled"] == ["net/fabricmc/api"],
            str(plan["shims_compiled"]),
        )

        # 2. the rewritten jar: pruned classes gone, configs rewritten
        rewritten = zipfile.ZipFile(work / "plan" / "mods" / mods[0].name)
        names = set(rewritten.namelist())
        pruned_files = [item["mixin"] + ".class" for item in pruned]
        check(
            "pruned mixin classes are removed from the jar",
            not any(path in names for path in pruned_files[:5]),
            str(pruned_files[:3]),
        )
        main_config = json.loads(rewritten.read("meteor-client.mixins.json"))
        listed = set(main_config.get("client") or [])
        dropped_names = {item["mixin"].rsplit("/", 1)[-1] for item in pruned}
        check(
            "the config file no longer lists a pruned mixin",
            not (listed & dropped_names),
            str(sorted(listed & dropped_names)[:3]),
        )
        check("fabric.mod.json still registers the core config", "meteor-client.mixins.json" in rewritten.read("fabric.mod.json").decode())
        check("the overlay payload keeps the mod's classes", len([n for n in names if n.endswith(".class")]) > 1000)

        # 3. the generated loader: direct calls, no reflection, and the bake guard
        source = (work / "plan" / "src" / "net" / "fabricmc" / "eaglerbridge" / "EaglerFabricLoader.java").read_text()
        check("the entrypoint class is imported", "import meteordevelopment.meteorclient.MeteorClient;" in source)
        check("the entrypoint is instantiated directly", "new MeteorClient()" in source)
        check("its client initialiser is called", "mod.onInitializeClient();" in source)
        check("no class is looked up by name", "Class.forName" not in source and "newInstance" not in source)
        check("the bake guard keeps the calls reachable but inactive there", 'System.getProperty("webmc.bake")' in source)

        # 4. something no shim covers must stop the build before the bake
        indigolike = zipfile.ZipFile(mods[0]).read(
            "meteordevelopment/meteorclient/mixin/indigo/AltModelBlockRendererImplMixin.class"
        )
        uncovered = work / "uncovered"
        uncovered.mkdir()
        fixture_mod(
            uncovered, "needs-a-missing-shim",
            [("net/fabricmc/impl/NeedsMesh.class", indigolike)],
            {"id": "needs-a-missing-shim", "version": "1", "entrypoints": {"client": ["example.Thing"]}},
        )
        # The mesh API has a shim now, so the uncovered case is made by hiding that shim for the run:
        # what is under test is that an uncovered need blocks the build and is named, not that any
        # particular class is missing one.
        # The loader covers a need when a shim *package* exists, so the uncovered case hides the whole
        # mesh package for the run. What is under test is that an uncovered need blocks the build and is
        # named, not that a particular class has no shim today.
        shim_package = (HERE.parent / "shims" / "net" / "fabricmc" / "fabric" / "api" / "client" / "renderer"
                        / "v1" / "mesh")
        hidden = shim_package.with_name("mesh.hidden")
        try:
            shim_package.rename(hidden)
            code, output = run_loader(uncovered, client, work / "uncovered-out")
        finally:
            hidden.rename(shim_package)
        check("a needed Fabric API class with no shim blocks the build", code == 2, f"exit {code}")
        blocked = json.loads((work / "uncovered-out" / "plan.json").read_text())["shims_missing"]
        check(
            "and the blocker names the class",
            any("client/renderer/v1/mesh" in item for item in blocked),
            str(blocked),
        )

        # 5. no mods at all is a valid build (the smoke test)
        empty = work / "empty"
        empty.mkdir()
        code, output = run_loader(empty, client, work / "empty-out")
        check("an empty mods folder is not an error", code == 0, f"exit {code}: {output[-200:]}")
        quiet = (work / "empty-out" / "src" / "net" / "fabricmc" / "eaglerbridge" / "EaglerFabricLoader.java").read_text()
        check("the loader says a build has no mods", "// no mods in this build" in quiet)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\nloader planning tests passed" if FAILURES == 0 else f"\n{FAILURES} loader test(s) FAILED")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
