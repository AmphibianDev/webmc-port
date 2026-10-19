#!/usr/bin/env python3
"""Backport a Fabric mod to Java 17, including the jars inside it.

    python3 tools/backport_mods.py --input mods/sodium.jar --output out/sodium.jar
    python3 tools/backport_mods.py --input mods-dir --output out-dir        # every jar in a folder

Why this exists, and it is the finding that defines this route: a mod for 1.21.11 is Java 21 bytecode,
exactly like the client - so the same wall that stops the client stops every mod, and the same answer works.
tools/backport17.py lowers one jar (class version 65 -> 61, the three Java 21 types, the new collection and
Math and Thread members, signature stripped); this does it for a mod:

  * a Fabric mod is often a *bundle*: fabric-api is 40-odd jars inside a jar, and every one of them is Java 21
    bytecode, so they are backported and repacked too;
  * the bytecode is only half of it. A mod that declares `depends: {java: ">=21"}` is refused by Fabric Loader
    before a single class is loaded, and after the backport that declaration is no longer true - the classes
    really are version 61 - so the declaration is corrected, and *said so* in the report rather than done
    quietly;
  * the mixin configs a mod carries state the bytecode level of the classes they mix, so a config that says
    JAVA_21 is corrected to JAVA_17 for the same reason the metadata is: after the backport that is what the jar
    contains, and Mixin refuses the *whole config* if the claim does not match the JRE.

What it does not do: make a mod that needs something only Java 21 has work. The backport handles the members
1.21.11 actually calls (measured, see tools/backport17.py); a mod that reaches for something else fails loudly
at the loader or at the first call, which is better than a silent rewrite.
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKPORT = ROOT / "tools" / "backport17.py"
# how the loader decides; the backport makes these true, so they are rewritten to match the bytecode
JAVA_REQUIREMENTS = ("java",)


def java_requirement(metadata: dict) -> str | None:
    depends = metadata.get("depends") or {}
    for name in JAVA_REQUIREMENTS:
        value = depends.get(name)
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value:
            return str(value[0])
    return None


def relax(metadata: dict) -> tuple[dict, str | None]:
    """Say the java requirement in terms of what the jar now contains."""
    depends = metadata.setdefault("depends", {})
    old = depends.get("java")
    if old is None:
        return metadata, None
    # keep the shape the mod used (a string or a list of strings), and only ever lower the floor
    def lower(value: str) -> str:
        return re.sub(r">=\s*2[0-9]", ">=17", re.sub(r">\s*2[0-9]", ">17", value))

    if isinstance(old, list):
        depends["java"] = [lower(str(item)) for item in old]
    else:
        depends["java"] = lower(str(old))
    return metadata, f"java {old} -> {depends['java']}"


def backport_jar(source: pathlib.Path, target: pathlib.Path, depth: int = 0) -> dict:
    """One jar: its classes, and any jars inside it, recursively."""
    report = {"jar": source.name, "nested": [], "java": None, "bytes": 0}
    target.parent.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix="webmc-mod-"))
    try:
        result = subprocess.run([sys.executable, str(BACKPORT), "--input", str(source),
                                 "--output", str(work / "backported.jar")], capture_output=True, text=True)
        if result.returncode != 0 or not (work / "backported.jar").is_file():
            print(f"  the backport refused {source.name}: {(result.stdout + result.stderr).strip()[-300:]}")
            return report
        report["bytes"] = (work / "backported.jar").stat().st_size
        changed = "method calls rewritten" in result.stdout
        for line in result.stdout.splitlines():
            if "method calls rewritten" in line:
                report["calls"] = line.split(":")[-1].strip() or "0"

        # rewrite the metadata, and take the jar inside the jar apart as well
        out = io.BytesIO()
        with zipfile.ZipFile(work / "backported.jar") as archive, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as writer:
            for entry in archive.infolist():
                data = archive.read(entry)
                name = entry.filename
                if name in ("fabric.mod.json", "quilt.mod.json") and depth == 0:
                    try:
                        metadata = json.loads(data)
                        metadata, change = relax(metadata)
                        if change:
                            report["java"] = change
                        data = (json.dumps(metadata, indent=2) + "\n").encode()
                    except (ValueError, TypeError):
                        pass
                elif name.endswith(".mixins.json") or (name.endswith(".json") and b"compatibilityLevel" in data):
                    # A mixin config states the bytecode level of the classes it mixes. After the backport they
                    # are version 61, so JAVA_21 is a claim the jar can no longer make - and Mixin refuses the
                    # whole config rather than the one line: "The requested compatibility level JAVA_21 could not be
                    # set" stopped 49 loaded mods from starting. The level is corrected to 17, which is now true.
                    text = data.decode("utf-8", "replace")
                    lowered = re.sub(r"JAVA_(2[1-9]|3[0-9])", "JAVA_17", text)
                    if lowered != text:
                        report["levels"] = report.get("levels", 0) + 1
                        data = lowered.encode()
                elif name.startswith("META-INF/jars/") and name.endswith(".jar") and depth < 2:
                    inner = work / f"nested-{len(report['nested'])}.jar"
                    inner.write_bytes(data)
                    nested_report = backport_jar(inner, inner.with_suffix(".backported.jar"), depth + 1)
                    report["nested"].append(nested_report)
                    data = inner.with_suffix(".backported.jar").read_bytes()
                writer.writestr(name, data)
        target.write_bytes(out.getvalue())
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, help="a mod jar, or a folder of them")
    parser.add_argument("--output", required=True, help="where the backported jar (or folder) goes")
    args = parser.parse_args(argv)

    source = pathlib.Path(args.input).expanduser()
    target = pathlib.Path(args.output).expanduser()
    jobs = sorted(source.glob("*.jar")) if source.is_dir() else [source]
    if not jobs:
        print(f"no jars in {source}")
        return 2

    for job in jobs:
        out = (target / job.name) if source.is_dir() else target
        report = backport_jar(job, out)
        nested = len([entry for entry in report["nested"]])
        print(f"{job.name}: {out.stat().st_size // 1024 if out.is_file() else 0} KB, "
              f"{nested} nested jar(s) backported, {report.get('levels', 0)} mixin config level(s) lowered"
              + (f", {report['java']}" if report["java"] else ", no java requirement to relax"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
