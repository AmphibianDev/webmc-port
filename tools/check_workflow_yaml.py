#!/usr/bin/env python3
"""Refuse to ship a workflow GitHub cannot parse.

    python3 tools/check_workflow_yaml.py

Written after an edit to build-snapshot.yml left a line-continuation inside a quoted echo, which GitHub
rejected outright: the run failed with no log at all, and the failure said nothing about the real cause.
A YAML parse here costs a second and would have caught it.
"""

from __future__ import annotations

import pathlib
import sys

try:
    import yaml
except ImportError:
    print("no pyyaml: install it (pip3 install --user pyyaml) to check the workflows")
    raise SystemExit(0)

broken = 0
for path in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
    try:
        document = yaml.safe_load(path.read_text())
    except Exception as problem:
        print(f"! {path}: {problem}")
        broken += 1
        continue
    jobs = list((document or {}).get("jobs", {}))
    print(f"  {path}: ok, {len(jobs)} job(s)")
raise SystemExit(1 if broken else 0)
