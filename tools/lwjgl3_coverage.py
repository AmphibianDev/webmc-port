#!/usr/bin/env python3
"""How much of the LWJGL 3 surface 1.21.11 needs is actually answered.

    python3 tools/lwjgl3_gaps.py --surface S --shim lwjgl3-webgl --out /tmp/need.json
    python3 tools/lwjgl3_coverage.py --need /tmp/need.json

A member counts as answered when this shim has a JavaScript native for it *or* a Java body (a struct accessor, a
capability field, a callback), because both are implementations. Counting only the JavaScript ones made the
number look ten points worse than it was.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TYPE = r"[\w\.\$\[\]<>,\s]*?"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--need", default="/tmp/need.json")
    parser.add_argument("--shim", default=str(ROOT / "lwjgl3-webgl"))
    args = parser.parse_args(argv)
    need = json.loads(pathlib.Path(args.need).read_text())
    shim = pathlib.Path(args.shim)
    js = (shim / "natives" / "webmc-lwjgl3.js").read_text()
    natives = set(re.findall(r"^function (Java_\w+)", js, re.M))
    left = {}
    for owner, members in need.items():
        path = shim / "src" / "main" / "java" / (owner.replace(".", "/") + ".java")
        text = path.read_text() if path.is_file() else ""
        for member in members:
            jni = "Java_" + owner.replace(".", "_").replace("$", "_") + "_" + member
            if jni in natives:
                continue
            # a member is answered when this file, the file of a class it extends in this shim, or a class
            # declared inside it has a body for it. Struct accessors are inherited from Struct/CustomBuffer and
            # capability fields live in nested classes, and both are implementations.
            candidates = [text]
            for base in ("Struct", "CustomBuffer", "Callback", "Pointer"):
                if "extends " + base in text or "extends " + base + "<" in text:
                    for path in shim.rglob(base + ".java"):
                        candidates.append(path.read_text())
            body = None
            for candidate in candidates:
                body = re.search(r"(?:public|protected)\s+(?:static\s+)?(?:final\s+)?" + TYPE + r"\b"
                                 + re.escape(member) + r"\s*[\(;=]", candidate) or body
            if body:
                line_end = text.find("\n", body.start())
                if "UnsupportedOperationException" not in text[body.start():line_end if line_end > 0 else len(text)]:
                    continue
            left.setdefault(owner, []).append(member)
    total = sum(len(v) for v in left.values())
    demanded = sum(len(v) for v in need.values())
    print(f"{demanded - total} of {demanded} demanded member(s) answered · {total} left")
    for owner, members in sorted(left.items()):
        print(f"  {owner}: {', '.join(sorted(members))}")
    return 0 if not left else 1


if __name__ == "__main__":
    raise SystemExit(main())
