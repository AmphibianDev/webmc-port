#!/usr/bin/env python3
"""Compile the mixin annotation stubs into a jar a build can put on the client's classpath.

A mod that carries mixins has code that constructs `CallbackInfo`, and the client it is merged into has to have
that class to be compiled at all. The annotations themselves are `CLASS` retention, so they are read out of the
mod's class file by the applier and never needed again - but the callback classes are real code at play time, so
they are built here and shipped into the client with the mod.

This is deliberately the same shape as tools/build_loader.py: javac and jar, no build system, no network.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHIMS = ROOT / "mods" / "mixin-shims"
DEFAULT_OUT = ROOT / "mods" / "jars" / "webmc-mixin-shims.jar"

sys.path.insert(0, str(ROOT / "tools"))
import build_loader  # noqa: E402 - the JDK finder that already knows about macOS stubs


def build(out: pathlib.Path, log=print) -> dict:
    sources = sorted(SHIMS.rglob("*.java"))
    if not sources:
        raise SystemExit(f"no mixin stub sources under {SHIMS}")
    javac = build_loader.find_javac()
    if not javac:
        raise SystemExit("no working javac: the stubs have to be compiled, and a version string is not a compiler")

    with tempfile.TemporaryDirectory(prefix="webmc-mixin-shims-") as tmp:
        classes = pathlib.Path(tmp) / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [javac, "-nowarn", "-source", "8", "-target", "8", "-d", str(classes)]
            + [str(source) for source in sources],
            capture_output=True, text=True,
        )
        if compile_result.returncode != 0:
            print(compile_result.stdout + compile_result.stderr, file=sys.stderr)
            raise SystemExit("the mixin stubs do not compile")

        out.parent.mkdir(parents=True, exist_ok=True)
        written = []
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            # deterministic: same sources in, same bytes out, so a rebuild is not a new artifact
            for path in sorted(classes.rglob("*.class")):
                name = path.relative_to(classes).as_posix()
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
                written.append(name)

    log(f"  wrote {out} ({len(written)} class(es))")
    for name in written:
        log(f"    {name}")
    return {"jar": str(out), "classes": written, "sources": [str(source) for source in sources]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build(args.out)
    if args.json:
        import json
        print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
