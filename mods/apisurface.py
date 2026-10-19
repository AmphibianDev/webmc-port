#!/usr/bin/env python3
"""Work-list generator for the build-time Fabric shim layer.

portscan.py says whether a mod is portable. This says *what to implement*: the exact
Fabric API members the mod calls (owner#name : descriptor), the loader API it uses, and
which game classes it touches — which is also the list of hook points the ported client
has to keep alive.

    python3 apisurface.py mod.jar                     # human report
    python3 apisurface.py --json shims.json mod.jar    # machine-readable shim manifest
    python3 apisurface.py a.jar b.jar                  # ... plus the shared union of both
"""

import io
import json
import struct
import sys
import urllib.request
import zipfile
from collections import Counter, defaultdict

UTF8, INTEGER, FLOAT, LONG, DOUBLE = 1, 3, 4, 5, 6
CLASS, STRING, FIELDREF, METHODREF, INTERFACEMETHODREF = 7, 8, 9, 10, 11
NAMEANDTYPE, METHODHANDLE, METHODTYPE, DYNAMIC, INVOKEDYNAMIC = 12, 15, 16, 17, 18
MODULE, PACKAGE = 19, 20


def parse_refs(data):
    """Return (owner, name, descriptor) for every class/method/field the bytecode references."""
    if data[:4] != b"\xca\xfe\xba\xbe":
        return []
    count = struct.unpack(">H", data[8:10])[0]
    utf8, class_idx, nat, refs = {}, {}, {}, []
    i, index = 10, 1
    while index < count:
        tag = data[i]
        i += 1
        if tag == UTF8:
            length = struct.unpack(">H", data[i:i + 2])[0]
            i += 2
            utf8[index] = data[i:i + length].decode("utf-8", "replace")
            i += length
        elif tag in (INTEGER, FLOAT, FIELDREF, METHODREF, INTERFACEMETHODREF, NAMEANDTYPE,
                     DYNAMIC, INVOKEDYNAMIC):
            a, b = struct.unpack(">HH", data[i:i + 4])
            i += 4
            if tag in (FIELDREF, METHODREF, INTERFACEMETHODREF):
                refs.append((tag, a, b))
            elif tag == NAMEANDTYPE:
                nat[index] = (a, b)
        elif tag in (CLASS, STRING, METHODTYPE, MODULE, PACKAGE):
            idx = struct.unpack(">H", data[i:i + 2])[0]
            i += 2
            if tag == CLASS:
                class_idx[index] = idx
        elif tag == METHODHANDLE:
            i += 3
        elif tag in (LONG, DOUBLE):
            i += 8
            index += 1
        index += 1

    out = []
    for kind, cls, nt in refs:
        owner = utf8.get(class_idx.get(cls, 0), "?")
        name_idx, desc_idx = nat.get(nt, (0, 0))
        out.append((kind, owner, utf8.get(name_idx, "?"), utf8.get(desc_idx, "?")))
    return out


def scan(jar_bytes):
    z = zipfile.ZipFile(io.BytesIO(jar_bytes))
    meta = json.loads(z.read("fabric.mod.json")) if "fabric.mod.json" in z.namelist() else {}
    fabric, loader, game, other = defaultdict(set), defaultdict(set), Counter(), Counter()
    kinds = {}

    for name in z.namelist():
        if not name.endswith(".class"):
            continue
        try:
            refs = parse_refs(z.read(name))
        except Exception:
            continue
        for kind, owner, member, desc in refs:
            kinds[(owner, member, desc)] = {FIELDREF: "field", METHODREF: "method",
                                            INTERFACEMETHODREF: "interface method"}[kind]
            if owner.startswith(("net/minecraft/", "com/mojang/")):
                game[owner] += 1
            elif owner.startswith("net/fabricmc/fabric/api/"):
                module = owner.split("/api/")[-1].rsplit("/", 1)[0]
                fabric[module].add((owner, member, desc))
            elif owner.startswith("net/fabricmc/api/"):
                loader[owner].add((owner, member, desc))
            elif not owner.startswith(("java/", "javax/", "sun/", "jdk/")):
                other[owner] += 1

    return {"meta": meta, "fabric": fabric, "loader": loader, "game": game,
            "other": other, "kinds": kinds}


def render(result, show_game=15):
    meta = result["meta"]
    print(f"\n=== {meta.get('id', '?')} {meta.get('version', '')}"
          f"   (environment: {meta.get('environment', '?')})")
    entrypoints = meta.get("entrypoints") or {}
    if entrypoints:
        print(f"    entrypoints: {', '.join(entrypoints.keys())}")

    total = 0
    print("\n  Fabric API to shim:")
    for module in sorted(result["fabric"]):
        members = sorted(result["fabric"][module])
        total += len(members)
        print(f"    {module}  ({len(members)} member(s))")
        for owner, member, desc in members:
            kind = result["kinds"].get((owner, member, desc), "?")
            short = owner.split("/")[-1]
            print(f"       {short}.{member}  {desc}   [{kind}]")
    if not result["fabric"]:
        print("    (none)")

    print("\n  Loader API used:")
    for owner in sorted(result["loader"]):
        print(f"    {owner.split('/')[-1]}: {len(result['loader'][owner])} member(s)")

    if result["game"]:
        print(f"\n  Game classes touched (top {show_game} of {len(result['game'])}) - each needs a live hook:")
        for owner, hits in result["game"].most_common(show_game):
            print(f"    {hits:4}  {owner}")

    if result["other"]:
        print("\n  Other libraries:")
        for owner, hits in result["other"].most_common(8):
            print(f"    {hits:4}  {owner}")
    print(f"\n  shim members required: {total}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    json_path = None
    if "--json" in sys.argv:
        json_path = sys.argv[sys.argv.index("--json") + 1]
        args = [a for a in args if a != json_path]

    results = {}
    for arg in args:
        data = urllib.request.urlopen(arg, timeout=120).read() if arg.startswith("http") else open(arg, "rb").read()
        result = scan(data)
        results[result["meta"].get("id") or arg] = result
        render(result)

    if len(results) > 1:
        union = defaultdict(set)
        for result in results.values():
            for module, members in result["fabric"].items():
                union[module] |= members
        print("\n=== shared union across all mods (write the shim once)")
        for module in sorted(union):
            print(f"    {module}: {len(union[module])} member(s)")
        print(f"    TOTAL: {sum(len(v) for v in union.values())} member(s)")
        print(f"    game classes: {len(set().union(*[set(r['game']) for r in results.values()]))}")

    if json_path:
        mods = {}
        for mid, r in results.items():
            fabric_out = {}
            for module in sorted(r["fabric"]):
                fabric_out[module] = [
                    {"owner": owner, "member": member, "descriptor": desc,
                     "kind": r["kinds"].get((owner, member, desc), "?")}
                    for owner, member, desc in sorted(r["fabric"][module])
                ]
            mods[mid] = {
                "version": r["meta"].get("version"),
                "environment": r["meta"].get("environment"),
                "fabric_api": fabric_out,
                "loader_api": sorted({owner for members in r["loader"].values()
                                      for owner, _, _ in members}),
                "game_classes": [{"class": klass, "refs": hits}
                                 for klass, hits in r["game"].most_common()],
            }
        json.dump({"mods": mods}, open(json_path, "w"), indent=2)
        print(f"\nwrote {json_path}")
