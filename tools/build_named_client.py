#!/usr/bin/env python3
"""Build an official-named Minecraft client locally, the same way the engine does.

    python3 tools/build_named_client.py --obf-client <client.jar> --mappings <client.txt> --out <named.jar>

Why this matters more than it sounds: every check in this project that asks "does this reference resolve
against the client?" is only as good as the client it asks. A hand-rolled rename is not good enough - the
first version of it here mapped only the members whose obfuscated name and descriptor both matched, so
`Font` came out with one `width` overload instead of three and a descriptor repair ran against a client that
had never heard of the member it was repairing. Wrong oracle, wrong answers.

So: the real tool, from the same place the engine gets it, with the same flags. It is fetched once into
port/work/tools and cached, exactly like the engine's own remap-client.sh does.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_loader  # noqa: E402

ART_VERSION = "2.0.18"
ART_URL = ("https://maven.neoforged.net/releases/net/neoforged/AutoRenamingTool/"
           f"{ART_VERSION}/AutoRenamingTool-{ART_VERSION}-all.jar")


def java_home() -> pathlib.Path:
    javac = build_loader.find_javac()
    if not javac:
        raise SystemExit("no JDK found")
    return pathlib.Path(javac).parent.parent


def fetch_art(cache: pathlib.Path, log) -> pathlib.Path:
    jar = cache / f"AutoRenamingTool-{ART_VERSION}-all.jar"
    if jar.is_file() and jar.stat().st_size > 100_000:
        return jar
    cache.mkdir(parents=True, exist_ok=True)
    log(f"fetching {ART_URL}")
    try:
        with urllib.request.urlopen(ART_URL, timeout=180) as response:
            jar.write_bytes(response.read())
    except Exception as problem:
        raise SystemExit(f"could not fetch the renaming tool ({problem}); on a network whose resolver "
                         f"answers 0.0.0.0 use: dig +short @1.1.1.1 maven.neoforged.net, then curl --resolve")
    return jar


def build(obf: pathlib.Path, mappings: pathlib.Path, out: pathlib.Path, log=print) -> pathlib.Path:
    home = java_home()
    art = fetch_art(ROOT / "work" / "tools", log)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(home / "bin" / "java"), "-Xmx3g", "-jar", str(art), "--input", str(obf),
         "--output", str(out), "--map", str(mappings), "--reverse", "--strip-sigs", "--ann-fix",
         "--record-fix", "--src-fix", "JAVA"], capture_output=True, text=True)
    if result.returncode != 0:
        log(result.stdout[-1500:])
        log(result.stderr[-1500:])
        raise SystemExit("the client could not be remapped")
    for line in result.stdout.strip().splitlines()[-2:]:
        log("  " + line)
    log(f"  wrote {out} ({out.stat().st_size} bytes)")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--obf-client", required=True)
    parser.add_argument("--mappings", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    build(pathlib.Path(args.obf_client), pathlib.Path(args.mappings), pathlib.Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
