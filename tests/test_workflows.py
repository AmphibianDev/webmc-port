#!/usr/bin/env python3
"""Tests for the workflow lint: the failures that cost a day because nothing says what broke.

    python3 tests/test_workflows.py

The reason this test exists is in the tool's own docstring: a duplicated YAML key made
`.github/workflows/build-client.yml` unparseable, and GitHub's answer was a failed run in the same
second with **no jobs and no logs**. Two days of dispatches died that way. So the fixtures here are the
two halves of that lesson:

  * a duplicate key in one mapping must be caught, however deeply it is nested, and whatever the block
    scalar bodies around it look like;
  * the same key in two *sibling list items* must NOT be caught - the first version of this check
    flagged every `run:` in these files, which is how a lint becomes something people switch off.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import workflow_lint  # noqa: E402

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f" — {detail}"))
    if not ok:
        FAILURES += 1


DUPLICATE = """\
name: Example
on:
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v7
        with:
          repository: owner/other
          repository: owner/preferred
          path: engine
"""

FINE = """\
name: Example
on:
  workflow_dispatch:

permissions:
  contents: write

jobs:
  one:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: First
        run: |
          echo "a key: value inside a block scalar"
          echo "# not a comment inside the scalar"
          with: this is text, not yaml
      - name: Second
        run: echo "same key, different list item"
        with:
          path: engine
      - name: Third
        run: echo "and again"
        with:
          path: dist
  two:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - run: echo "run: is not a duplicate across jobs either"
"""


def test_scan() -> None:
    print("\nthe duplicate-key reader")
    duplicates = workflow_lint.scan(DUPLICATE)
    check("a duplicate key in one mapping is caught", len(duplicates) == 1 and "repository" in duplicates[0],
          str(duplicates))
    check("and it names the line it first saw", "at line" in duplicates[0], str(duplicates))
    check("a file with the same key in sibling list items is not flagged", workflow_lint.scan(FINE) == [],
          str(workflow_lint.scan(FINE)))
    check("a block scalar body is not read as YAML",
          workflow_lint.scan('run: |\n  key: 1\n  key: 2\n') == [])
    check("a hash inside quotes is not a comment",
          workflow_lint.strip_comment('run: echo "a # b"') == 'run: echo "a # b"')
    check("a hash after a space is a comment",
          workflow_lint.strip_comment('run: echo hi  # comment').strip() == "run: echo hi")


def test_triggers() -> None:
    print("\nreading the triggers")
    check("a mapping form of on: is read",
          workflow_lint.triggers("on:\n  push:\n    branches: [main]\n  workflow_dispatch:\n") ==
          {"push", "workflow_dispatch"})
    check("a bare form is read", workflow_lint.triggers("on: [push, tags]\n") == {"push", "tags"})
    check("a single event is read", workflow_lint.triggers("on: push\n") == {"push"})


def test_lint(tmp: pathlib.Path) -> None:
    print("\nthe rules")
    duplicate = tmp / "dup.yml"
    duplicate.write_text(DUPLICATE)
    problems = workflow_lint.lint(duplicate)
    check("a duplicate key is an error", any("duplicate key" in p for p in problems), str(problems))
    fine = tmp / "fine.yml"
    fine.write_text(FINE)
    check("a clean workflow has nothing to report", workflow_lint.lint(fine) == [],
          str(workflow_lint.lint(fine)))

    pushed = tmp / "push-build.yml"
    pushed.write_text(FINE.replace("on:\n  workflow_dispatch:\n", "on:\n  push:\n"))
    problems = workflow_lint.lint(pushed)
    check("a build that runs on any push is an error",
          any("only a tag is deliberate" in p for p in problems), str(problems))

    branch_push = tmp / "branch-build.yml"
    branch_push.write_text(FINE.replace("on:\n  workflow_dispatch:\n",
                                       "on:\n  push:\n    branches: [main]\n"))
    check("a build on a branch push is refused too",
          any("only a tag is deliberate" in p for p in workflow_lint.lint(branch_push)),
          str(workflow_lint.lint(branch_push)))

    tag_push = tmp / "tag-build.yml"
    tag_push.write_text(FINE.replace("on:\n  workflow_dispatch:\n",
                                    "on:\n  push:\n    tags: [\"build-*\"]\n"))
    check("a build on a tag is allowed: pushing a tag is a deliberate act",
          workflow_lint.lint(tag_push) == [], str(workflow_lint.lint(tag_push)))
    check("and the tag scope is read rather than guessed",
          workflow_lint.push_scope("on:\n  push:\n    tags: ['build-*']\n") == "tags"
          and workflow_lint.push_scope("on:\n  push:\n    branches: [main]\n") == "branches"
          and workflow_lint.push_scope("on:\n  push:\n") == "unrestricted",
          str(workflow_lint.push_scope("on:\n  push:\n")))

    no_timeout = tmp / "no-timeout.yml"
    no_timeout.write_text(FINE.replace("    timeout-minutes: 10\n", ""))
    problems = workflow_lint.lint(no_timeout)
    check("a job with no timeout is an error", any("no timeout-minutes" in p for p in problems), str(problems))

    streaming = tmp / "stream.yml"
    streaming.write_text(FINE + "\n# start x11vnc for the session\n")
    check("the streaming stack is refused",
          any("streaming stack" in p for p in workflow_lint.lint(streaming)))

    naked = tmp / "naked.yml"
    naked.write_text(FINE.replace("permissions:\n  contents: write\n\n", ""))
    check("a workflow with no permissions block is an error",
          any("permissions" in p for p in workflow_lint.lint(naked)))

    if workflow_lint.cross_check(duplicate) is not None:
        check("PyYAML agrees with the reader", workflow_lint.cross_check(duplicate) != "ok",
              str(workflow_lint.cross_check(duplicate)))
    else:
        print("  SKIP  PyYAML is not installed here (the reader is what runs in CI)")


def test_the_real_workflows() -> None:
    print("\nthis repository's own workflows")
    paths = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    check("there are workflows to check", len(paths) >= 3, str(len(paths)))
    for path in paths:
        problems = workflow_lint.lint(path)
        check(f"{path.name} is valid", problems == [], "; ".join(problems))
    # The one that broke, specifically: an engine checkout that can be overridden must be one key.
    client = (ROOT / ".github" / "workflows" / "build-client.yml").read_text()
    check("the engine checkout is a single repository key",
          client.count("repository:") == 1, str(client.count("repository:")))


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        test_scan()
        test_triggers()
        test_lint(pathlib.Path(scratch))
        test_the_real_workflows()
    print(f"\n{FAILURES} workflow test(s) FAILED" if FAILURES else "\nworkflow tests passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
