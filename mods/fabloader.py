#!/usr/bin/env python3
"""Fabric mod loader front-end for the browser (build-time loader).

On a browser client there is no runtime to load a .jar into, so a "Fabric loader" has to
run at build time. This is that loader's front-end: point it at a version workspace and a
folder of Fabric mods, and it produces everything the build needs - and refuses the mods
that cannot work, with the reason.

    python3 fabloader.py --workspace /path/to/Eaglercraft-26.2-Workspace \
                        --mods mods/ --out plan/

Outputs:
    plan/build-plan.json    machine-readable: per mod verdict + the union shim manifest
    plan/build-plan.md      what a human reads before burning a 40-minute build
    plan/shims.json         the Fabric API members the shim layer must implement

Exit code is non-zero if any mod is impossible or a required hook site is missing from the
workspace, so CI can stop before the expensive part.
"""

import json
import os
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import apisurface  # noqa: E402
import portscan  # noqa: E402

# Where each Fabric API member has to hook into the ported game. Checked against the real
# workspace, so a version bump that moves the code shows up as "hook missing" instead of a
# mystery build failure.
HOOKS = {
    "net/fabricmc/fabric/api/client/event/lifecycle/v1/ClientTickEvents": {
        "hook": "ClientTickEvents.END_CLIENT_TICK",
        "file": "game/src/main/java/net/minecraft/client/Minecraft.java",
        "anchor": "runTick",
    },
    "net/fabricmc/fabric/api/client/screen/v1/ScreenEvents": {
        "hook": "ScreenEvents.AFTER_INIT",
        "file": "game/src/main/java/net/minecraft/client/gui/screens/Screen.java",
        "anchor": "protected void init()",
    },
    "net/fabricmc/fabric/api/client/screen/v1/Screens": {
        "hook": "Screens.getWidgets",
        "file": "game/src/main/java/net/minecraft/client/gui/screens/Screen.java",
        "anchor": "renderables",
    },
    "net/fabricmc/fabric/api/client/keymapping/v1/KeyMappingHelper": {
        "hook": "KeyMappingHelper.registerKeyMapping",
        "file": "game/src/main/java/net/minecraft/client/KeyMapping.java",
        "anchor": "ALL",
    },
    "net/fabricmc/fabric/api/client/command/v2/ClientCommandRegistrationCallback": {
        "hook": "ClientCommandRegistrationCallback.EVENT",
        "file": "game/src/main/java/net/minecraft/client/multiplayer/ClientPacketListener.java",
        "anchor": "CommandDispatcher",
    },
    "net/fabricmc/fabric/api/event/Event": {
        "hook": "Event.register",
        "file": None,  # lives in our own shim module, no game hook
        "anchor": None,
    },
    "net/fabricmc/fabric/api/client/command/v2/ClientCommands": {
        "hook": "ClientCommands.literal",
        "file": None,  # pure helper over brigadier's LiteralArgumentBuilder
        "anchor": None,
    },
}


def check_hook(workspace, module_info):
    """Is the hook site still there? Returns (status, detail)."""
    if not module_info["file"]:
        return "shim-only", "no game hook needed"
    path = pathlib.Path(workspace) / module_info["file"]
    if not path.exists():
        return "MISSING", f"{module_info['file']} does not exist"
    try:
        text = path.read_text(errors="replace")
    except Exception as error:
        return "MISSING", str(error)
    if module_info["anchor"] not in text:
        return "MISSING", f"anchor {module_info['anchor']!r} not found in {module_info['file']}"
    return "ok", f"{module_info['file']} has {module_info['anchor']!r}"


def load_mods(sources):
    """sources: paths to .jar files or http(s) urls."""
    out = []
    for source in sources:
        data = (urllib.request.urlopen(source, timeout=180).read()
                if source.startswith("http") else open(source, "rb").read())
        name = os.path.basename(source.rstrip("/")) or source
        out.append((name, data))
    return out


def main():
    argv = sys.argv[1:]
    workspace = argv[argv.index("--workspace") + 1] if "--workspace" in argv else None
    out_dir = pathlib.Path(argv[argv.index("--out") + 1] if "--out" in argv else "plan")
    if "--mods" in argv:
        mods_arg = argv[argv.index("--mods") + 1]
        if mods_arg.startswith("http"):
            sources = [mods_arg]
        else:
            sources = [str(p) for p in sorted(pathlib.Path(mods_arg).glob("*.jar"))]
    else:
        sources = [a for a in argv if a.endswith(".jar") or a.startswith("http")]

    mods = load_mods(sources)
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = {"workspace": workspace, "mods": [], "shim_union": {}, "hooks": {}}
    union = {}
    blockers = 0
    for name, data in mods:
        verdict = portscan.scan(data)
        surface = apisurface.scan(data)
        mod_id = verdict["id"] or name
        plan["mods"].append({"file": name, **verdict,
                             "fabric_api": {m: [{"owner": o, "member": n, "descriptor": d}
                                                for o, n, d in sorted(v)]
                                            for m, v in surface["fabric"].items()}})
        if verdict["verdict"] == "impossible":
            blockers += 1
        for module, members in surface["fabric"].items():
            union.setdefault(module, set()).update(members)

    for module in sorted(union):
        members = [{"owner": o, "member": n, "descriptor": d} for o, n, d in sorted(union[module])]
        plan["shim_union"][module] = members
        entries = []
        for owner in sorted({o for o, _, _ in union[module]}):
            info = HOOKS.get(owner)
            if info:
                status, detail = check_hook(workspace, info)
            else:
                status, detail = "unmapped", "no game hook known - shim may not need one"
                info = {"hook": "?"}
            entries.append({"class": owner.split("/")[-1], "hook": info["hook"],
                            "status": status, "detail": detail})
        plan["hooks"][module] = entries

    total_members = sum(len(v) for v in plan["shim_union"].values())
    missing_hooks = [f"{module}:{entry['class']}" for module, entries in plan["hooks"].items()
                     for entry in entries if entry["status"] == "MISSING"]
    json.dump(plan, open(out_dir / "build-plan.json", "w"), indent=2)
    json.dump({"version": plan.get("version", "26.2"),
               "fabric_api": {m: v for m, v in plan["shim_union"].items()}},
              open(out_dir / "shims.json", "w"), indent=2)

    # ---- human report -------------------------------------------------------
    lines = ["# Build plan", ""]
    lines.append(f"workspace: `{workspace}`" )
    lines.append("")
    lines.append("| mod | classes | names | injections | verdict |")
    lines.append("| --- | --- | --- | --- | --- |")
    for mod in plan["mods"]:
        names = "official" if mod["unobfuscated_names"] else "intermediary (remap)"
        injections = sum(mod["injectors"].values())
        icon = {"portable": "✅", "portable, needs shims": "🟡", "hard": "🟠",
                "impossible": "⛔"}[mod["verdict"]]
        lines.append(f"| {mod['id']} {mod['version']} | {mod['classes']} | {names} |"
                     f" {injections} | {icon} {mod['verdict']} |")
    lines += ["", f"## Shims to write ({total_members} members, {len(union)} modules)", ""]
    for module, members in plan["shim_union"].items():
        lines.append(f"### {module}")
        for entry in plan["hooks"][module]:
            mark = {"ok": "✅", "shim-only": "🧩", "unmapped": "❔"}.get(entry["status"], "❌")
            lines.append(f"- hook {mark} **{entry['hook']}** — {entry['detail']}")
        for member in members:
            lines.append(f"  - `{member['owner'].split('/')[-1]}.{member['member']}` `{member['descriptor']}`")
        lines.append("")
    lines += ["## Blockers", ""]
    any_blocker = False
    for mod in plan["mods"]:
        for item in mod["blockers"]:
            lines.append(f"- **{mod['id']}**: {item}")
            any_blocker = True
    if not any_blocker:
        lines.append("- none")
    (out_dir / "build-plan.md").write_text("\n".join(lines) + "\n")

    print("\n".join(lines))
    print(f"\nwrote {out_dir}/build-plan.json, {out_dir}/shims.json, {out_dir}/build-plan.md")
    print(f"mods: {len(plan['mods'])} | shim members: {total_members} | "
          f"impossible: {blockers} | hooks missing: {len(missing_hooks)}")
    sys.exit(1 if (blockers or missing_hooks) else 0)


if __name__ == "__main__":
    main()
