#!/usr/bin/env python3
"""Lint the workflows: the failures that cost a day and look like nothing.

    python3 tools/workflow_lint.py                 # every workflow in .github/workflows
    python3 tools/workflow_lint.py --file x.yml --json

Why this exists: on 2026-10-11 `.github/workflows/build-client.yml` grew a second `repository:` key in
the same `with:` block - a merge accident - and GitHub answered every push by creating a failed run
**with no jobs**, in the same second, named after the file path. Nothing in the build was broken; the
file simply could not be parsed. Two days of dispatches died that way and the logs could not say why,
because there were no logs.

So this checks the four things that actually bit, plus the project's own rules:

  * **duplicate keys in one mapping** - invalid YAML that GitHub rejects wholesale;
  * **no `timeout-minutes`** on a job - a hung build holds a runner for six hours;
  * **a long build that runs on `push`** - a build must be a decision, not a side effect of a commit;
  * **anything from the streaming stack** (x11vnc, noVNC, Xvfb, ngrok, ffmpeg streaming) - that is what
    got an earlier account suspended, and `COMPLIANCE.md` says no workflow may mention it.

Written on Python's standard library alone, because CI has no PyYAML and a lint that cannot run where it
matters is decoration. PyYAML is used as a cross-check when it happens to be installed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
WORKFLOWS = ROOT / ".github" / "workflows"

# The arms of the streaming stack this repository must never touch again (COMPLIANCE.md).
FORBIDDEN = (
    "x11vnc", "novnc", "noVNC", "Xvfb", "xvfb-run", "xpra", "kasmvnc", "x11docker",
    "ngrok", "localhost.run", "serveo", "cloudflared tunnel", "tmate", "tailscale serve",
    "-f vnc", "rtsp://", "websocketd",
)

BLOCK_SCALAR = re.compile(r"^[|>][+-]?\d*$")
KEY = re.compile(r"^([A-Za-z_][\w.@/-]*):(?:\s*(.*))?$")


class Context:
    """One block mapping being read: the keys it has already seen, and its indentation."""

    __slots__ = ("indent", "keys", "line")

    def __init__(self, indent: int, line: int):
        self.indent = indent
        self.keys: dict[str, int] = {}
        self.line = line


def scan(text: str) -> list[str]:
    """Duplicate keys in the same mapping, as human-readable strings.

    This is a deliberately small YAML reader: block mappings, block sequences, and the block scalars
    that carry every `run:` body. Anything else (flow mappings, anchors) is not used by these files, and
    is ignored rather than guessed at.
    """
    duplicates: list[str] = []
    stack: list[Context] = [Context(-1, 0)]
    scalar_indent: int | None = None

    for number, raw in enumerate(text.splitlines(), 1):
        if scalar_indent is not None:
            # inside a `run: |` body: everything more indented than the key belongs to the scalar
            if not raw.strip() or (len(raw) - len(raw.lstrip())) > scalar_indent:
                continue
            scalar_indent = None

        line = strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        content = line.strip()

        if content.startswith("- ") or content == "-":
            while len(stack) > 1 and stack[-1].indent >= indent:
                stack.pop()
            inner = content[2:].strip()
            context = Context(indent + 2, number)
            stack.append(context)
            match = KEY.match(inner)
            if match and not BLOCK_SCALAR.match((match.group(2) or "").strip()):
                context.keys[match.group(1)] = number
            elif match:
                scalar_indent = indent + 2
            continue

        while len(stack) > 1 and stack[-1].indent > indent:
            stack.pop()
        if stack[-1].indent != indent:
            stack.append(Context(indent, number))
        context = stack[-1]

        match = KEY.match(content)
        if not match:
            continue
        key, value = match.group(1), (match.group(2) or "").strip()
        if key in context.keys:
            duplicates.append(f"line {number}: {key!r} is already defined at line {context.keys[key]}")
        context.keys[key] = number
        if BLOCK_SCALAR.match(value):
            scalar_indent = indent
    return duplicates


def strip_comment(line: str) -> str:
    """Remove a trailing comment, but never inside quotes - `run: echo "a # b"` keeps its hash."""
    out, quote = [], None
    for index, char in enumerate(line):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            continue
        if char == "#" and index and line[index - 1] in " \t":
            break
        out.append(char)
    return "".join(out).rstrip()


def triggers(text: str) -> set[str]:
    """The event names under `on:` - a small reader, because the block is not a mapping."""
    names: set[str] = set()
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if re.match(r"^on:\s*$", line) or re.match(r"^on:\s*\[", line):
            if "[" in line:
                names.update(part.strip().strip("\"'") for part in line.split("[", 1)[1].rstrip("]").split(","))
                return names
            base = len(line) - len(line.lstrip())
            event_indent = None
            for follow in lines[number + 1:]:
                if not follow.strip() or follow.strip().startswith("#"):
                    continue
                indent = len(follow) - len(follow.lstrip())
                if indent <= base:
                    break
                if event_indent is None:
                    event_indent = indent
                # only the event names: `branches:` lives one level deeper and is not a trigger
                if indent != event_indent:
                    continue
                match = re.match(r"^\s*([\w-]+):", follow)
                if match:
                    names.add(match.group(1))
        elif re.match(r"^on:\s+[\w-]+", line):
            names.add(line.split(":", 1)[1].strip())
    return names


def push_scope(text: str) -> str:
    """What a `push:` trigger is restricted to: 'tags', 'branches', 'both' or 'unrestricted'.

    The distinction is the whole point: a tag is a deliberate act by a person, so a 55-minute build may
    start from one; a branch push is a side effect of committing, and a long job must not start that way.
    """
    if re.search(r"^on:\s*\[", text, re.M) and "push" in text.split("on:", 1)[1].split("\n", 1)[0]:
        return "unrestricted"      # an inline list cannot say what the push was restricted to
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if not re.match(r"^on:\s*$", line):
            continue
        base = len(line) - len(line.lstrip())
        inside_push = False
        push_indent = None
        scope = set()
        for follow in lines[number + 1:]:
            if not follow.strip() or follow.strip().startswith("#"):
                continue
            indent = len(follow) - len(follow.lstrip())
            if indent <= base:
                break
            if re.match(r"^\s*push:\s*(\[.*\])?\s*$", follow):
                inside_push = True
                push_indent = indent
                inline = follow.split(":", 1)[1].strip()
                if inline.startswith("["):
                    body = inline.strip("[]")
                    for token in body.split(","):
                        token = token.strip().strip("\"'")
                        if token.startswith("tags"):
                            scope.add("tags")
                        if token.startswith("branches"):
                            scope.add("branches")
                continue
            if inside_push and indent > push_indent:
                key = re.match(r"^\s*([\w-]+):", follow)
                if key and key.group(1) in ("branches", "branches-ignore", "tags", "tags-ignore"):
                    scope.add("tags" if key.group(1).startswith("tags") else "branches")
            elif inside_push and indent <= push_indent:
                inside_push = False
        if not scope:
            return "unrestricted"
        return "both" if len(scope) > 1 else scope.pop()
    return "unrestricted"


def lint(path: pathlib.Path, log=print) -> list[str]:
    """Every problem found in one workflow file."""
    text = path.read_text()
    problems: list[str] = []

    for duplicate in scan(text):
        problems.append(f"duplicate key: {duplicate} (GitHub refuses the whole file)")

    events = triggers(text)
    is_build = "build" in path.name
    if is_build and "schedule" in events:
        problems.append("a build workflow runs on a schedule: long jobs must be dispatched by hand, "
                        "so nothing starts one by surprise")
    if is_build and "push" in events:
        scope = push_scope(text)
        if scope in ("branches", "both", "unrestricted"):
            problems.append(f"a build workflow runs on a push to {scope}: only a tag is deliberate "
                            "enough to start a long job, so restrict it with `on: push: tags:`")
    if not events:
        problems.append("no `on:` triggers could be read: GitHub would never run this file")

    for name, block in jobs(text).items():
        if "timeout-minutes" not in block:
            problems.append(f"job {name!r} has no timeout-minutes: a hung build holds a runner for six hours")

    for needle in FORBIDDEN:
        if needle.lower() in text.lower():
            problems.append(f"mentions {needle!r}, which is the streaming stack COMPLIANCE.md forbids")

    if "permissions:" not in text:
        problems.append("no `permissions:` block: the default token is broader than any job here needs")

    return problems


def jobs(text: str) -> dict[str, str]:
    """Job name -> the text of its block, from the `jobs:` mapping."""
    lines = text.splitlines()
    found: dict[str, str] = {}
    start = None
    for number, line in enumerate(lines):
        if re.match(r"^jobs:\s*$", line):
            start = number + 1
            break
    if start is None:
        return found
    base = None
    current = None
    body: list[str] = []
    for line in lines[start:]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if base is None:
            base = indent
        if indent < base:
            break
        match = re.match(r"^\s{2}([\w-]+):\s*$", line) if indent == base else None
        if match:
            if current:
                found[current] = "\n".join(body)
            current, body = match.group(1), []
        elif current:
            body.append(line)
    if current:
        found[current] = "\n".join(body)
    return found


def cross_check(path: pathlib.Path) -> str | None:
    """If PyYAML is here, make sure it agrees that this file has duplicate keys (or does not)."""
    try:
        import yaml  # noqa: PLC0415 - optional, only when installed
    except ImportError:
        return None
    class Strict(yaml.SafeLoader):
        pass

    def no_duplicates(loader, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.YAMLError(f"duplicate key {key!r}")
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep)

    Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, no_duplicates)
    try:
        yaml.load(path.read_text(), Loader=Strict)
        return "ok"
    except yaml.YAMLError as error:
        return f"PyYAML disagrees: {str(error).splitlines()[0]}"
    except Exception as error:  # noqa: BLE001 - a lint must not crash on a weird file
        return f"PyYAML could not read it: {error}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", action="append", default=None,
                        help="lint one file (default: every workflow)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    paths = [pathlib.Path(p) for p in args.file] if args.file else sorted(WORKFLOWS.glob("*.yml"))
    report = {}
    failures = 0
    for path in paths:
        if not path.is_file():
            print(f"no such workflow: {path}", file=sys.stderr)
            return 2
        problems = lint(path, log=lambda *_: None)
        duplicate_check = cross_check(path)
        if duplicate_check and duplicate_check != "ok" and not any("duplicate key" in p for p in problems):
            problems.append(duplicate_check)
        report[str(path)] = problems
        failures += len(problems)
        if not args.json:
            print(f"{path.name}")
            for problem in problems:
                print(f"  ! {problem}")
            if not problems:
                print("  ok")
    if args.json:
        print(json.dumps(report, indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
