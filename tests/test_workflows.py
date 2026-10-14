#!/usr/bin/env python3
"""Tests for the workflow lint: the failures that cost a day because nothing says what broke.

    python3 tests/test_workflows.py

The reason this test exists is in the tool's own docstring: a duplicated YAML key made
the build workflow unparseable, and GitHub's answer was a failed run in the same
second with **no jobs and no logs**. Two days of dispatches died that way. So the fixtures here are the
two halves of that lesson:

  * a duplicate key in one mapping must be caught, however deeply it is nested, and whatever the block
    scalar bodies around it look like;
  * the same key in two *sibling list items* must NOT be caught - the first version of this check
    flagged every `run:` in these files, which is how a lint becomes something people switch off.
"""

from __future__ import annotations

import json
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
    # Wherever the checkout happens, it is one key: two of them is what made the file unparseable.
    text = (ROOT / ".github" / "workflows" / "build-snapshot.yml").read_text()
    check("the engine checkout is a single repository key", text.count("repository:") <= 1,
          str(text.count("repository:")))
    check("and there is exactly one build workflow: a second one is a second thing to break",
          len(list((ROOT / ".github" / "workflows").glob("*build*.yml"))) == 1,
          str(sorted(p.name for p in (ROOT / ".github" / "workflows").glob("*build*.yml"))))


def test_routes(tmp: pathlib.Path) -> None:
    print("\none version in, one route out")
    sys.path.insert(0, str(ROOT / "tools"))
    import route  # noqa: E402
    import plan as planner  # noqa: E402

    engines = planner.load_engines(include_disabled=False)
    state = {"jvm": {
        "1.12.2": {"java": 8, "lwjgl": "2.9.4", "route": "cheerpj", "type": "release",
                   "source": "document"},
        "1.16.5": {"java": 8, "lwjgl": "3.2.1", "route": "needs-lwjgl3", "type": "release",
                   "source": "document"},
        "1.20.5": {"java": 21, "lwjgl": "3.3.3", "route": "needs-jvm", "type": "release",
                   "source": "document"},
    }, "manifest": {
        "1.21.11": {"type": "release", "released": "2026-01-01T00:00:00+00:00"},
        "b1.7.3": {"type": "old_beta", "released": "2011-07-01T00:00:00+00:00"},
    }, "built": {"26.2": {"url": "https://example.invalid/client-26.2.html", "bytes": 1}}}

    real = route.route_for("1.12.2", engines, state)
    check("a LWJGL 2 client runs the official jar in the page",
          real["route"] == "real-java" and real["engine"] == "cheerpj-unmodified", str(real)[:120])
    check("and its mods load at play time, not at build time",
          "play time" in real["mods"] and real["bakes_mods"] is False, str(real["mods"]))
    shim = route.route_for("1.16.5", engines, state)
    check("a LWJGL 3 client is waiting on the shim", shim["route"] == "needs-shim", shim["why"][:80])
    modern = route.route_for("1.20.5", engines, state)
    check("a Java 21 client can only be compiled", modern["route"] == "aot", modern["why"][:80])
    guessed = route.route_for("1.21.11", engines, state)
    check("a version whose document was not read routes by date, and says so",
          guessed["route"] == "aot" and "guessed from the release date" in guessed["why"],
          str(guessed["why"])[:100])
    check("and the engine for it is the MIT one", guessed["engine"] == "gaius"
          and guessed["engine_license"] == "MIT", f"{guessed['engine']} {guessed['engine_license']}")
    check("an engine that cannot take a mod set says so rather than ignoring one",
          guessed["bakes_mods"] is False and "only when an engine" in guessed["mods"], guessed["mods"])
    built = route.route_for("26.2", engines, state)
    check("a version with a build behind it is playable, and says where the build is",
          built["playable"] and built["built"]["url"].startswith("https://"), str(built["built"]))
    old_beta = route.route_for("b1.7.3", engines, state)
    check("a beta routes as real Java with the pre-shader caveat",
          old_beta["route"] == "real-java", str(old_beta)[:120])

    (tmp / "state.json").write_text(json.dumps(state))
    (tmp / "manifest.json").write_text(json.dumps({"versions": [
        {"id": vid, "type": entry.get("type"), "released": entry.get("released")}
        for vid, entry in state["manifest"].items()]}))
    out = tmp / "routes.json"
    code = route.main(["--export", str(out), "--state", str(tmp / "state.json"),
                       "--manifest", str(tmp / "manifest.json"), "--ports",
                       str(ROOT / "ports.json")])
    check("the export writes the file the launcher reads", code == 0 and out.is_file())
    if out.is_file():
        exported = json.loads(out.read_text())
        check("with a route for every version it was given",
              "1.12.2" in exported["versions"] and "1.21.11" in exported["versions"],
              str(sorted(exported["versions"])[:5]))
        check("and the fields a card shows",
              all(key in exported["versions"]["1.12.2"]
                  for key in ("route", "why", "engine", "engine_license", "mods", "playable")))

    summary = route.span("1.0", "1.21.11", engines, state)
    check("a span counts every route it can see, including the guesses",
          summary["counts"].get("real-java") and summary["counts"].get("aot"), str(summary["counts"]))
    check("and shows examples, so the numbers can be checked",
          all(summary["examples"].values()), str(summary["examples"]))


def test_credits() -> None:
    print("\nthe credits ledger and the engine registry agree")
    ports = json.loads((ROOT / "ports.json").read_text())
    credits = (ROOT / "CREDITS.md").read_text()
    engines = ports["engines"]
    check("there is a credits file", bool(credits.strip()))
    missing_licence = [e["id"] for e in engines if not e.get("license") and not e.get("license_note")]
    check("every engine says what its licence is, or why it has none",
          not missing_licence, str(missing_licence))
    lowered = credits.lower()
    no_credit = [e["id"] for e in engines
                 if e.get("repo") and e["repo"].split("/")[0].lower() not in lowered]
    check("every borrowed engine's owner is named in CREDITS.md",
          not no_credit, str(no_credit))
    unlicensed = [e["id"] for e in engines if not e.get("license")]
    check("and each unlicensed one carries the risk in its own words",
          all("no licence file" in (e.get("license_note") or e.get("blocked_by") or "")
              for e in engines if e["id"] in unlicensed), str(unlicensed))
    check("the credits file says Minecraft is not ours",
          "not an official minecraft product" in credits.lower(),
          "CREDITS.md must say the client is Mojang's and this is not an official product")
    check("and that branding is stripped rather than hidden",
          "--strip-brand" in credits and "branding" in credits)


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        tmp = pathlib.Path(scratch)
        test_scan()
        test_triggers()
        test_lint(tmp)
        test_the_real_workflows()
        test_credits()
        test_routes(tmp)
    print(f"\n{FAILURES} workflow test(s) FAILED" if FAILURES else "\nworkflow tests passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
