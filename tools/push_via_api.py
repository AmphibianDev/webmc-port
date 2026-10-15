#!/usr/bin/env python3
"""Push local commits through the GitHub API, for when `git push` is refused.

`git push` and the REST API are not the same door. A flagged or limited account can be refused with "Your account
is suspended" on the git transport while the same token still writes files through the API - which is a strange
sentence to write, and was measured rather than assumed before this tool existed. When that happens, the work does
not have to stop: a commit is a tree, a parent and a message, and all three can be sent over HTTPS.

What it does, per local commit that the remote does not have (oldest first):

    POST /git/trees    with base_tree = the parent's tree, and only the paths this commit changed
    POST /git/commits  with the message, the new tree and the parent
    PATCH /git/refs/heads/<branch>   once, at the end

Only changed paths are uploaded, which is the difference between pushing a commit and pushing a repository. The
messages are preserved, and so are the author names and dates.

This is a workaround, not a replacement: it is slower than git, it cannot be interrupted in the middle (the ref is
updated last, so an interrupted run leaves nothing half-pushed), and it should be used only while the git
transport is refused. `--dry-run` says what it would send and sends nothing.
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"


def git(*args: str, cwd: pathlib.Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def call(token: str, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(API + path, method=method, data=data,
                                     headers={"User-Agent": "webmc-port/push-via-api",
                                              "Accept": "application/vnd.github+json",
                                              "Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"{method} {path} -> {error.code}: {detail}") from None


def commits_to_send(repo_dir: pathlib.Path, remote_ref: str, local_ref: str) -> list[str]:
    """The local commits the remote does not have, oldest first."""
    have = git("rev-parse", remote_ref, cwd=repo_dir).strip()
    revs = git("rev-list", "--reverse", f"{have}..{local_ref}", cwd=repo_dir).split()
    return revs


def changed_paths(repo_dir: pathlib.Path, commit: str) -> list[tuple[str, str | None]]:
    """(path, status) for one commit against its parent: A/M/D/R with the new path."""
    if git("rev-list", "--max-parents=0", commit, cwd=repo_dir).strip() == commit:
        names = git("ls-tree", "-r", "--name-only", commit, cwd=repo_dir).split("\n")
        return [(name, "A") for name in names if name]
    out = git("diff-tree", "--no-commit-id", "--name-status", "-r", "-M", commit, cwd=repo_dir)
    rows: list[tuple[str, str | None]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            rows.append((parts[1], parts[0][0]))
        elif len(parts) == 3:  # a rename or copy: the old path goes, the new one arrives
            rows.append((parts[1], "D"))
            rows.append((parts[2], "A"))
    return rows


def blob_entry(token: str, repo: str, repo_dir: pathlib.Path, commit: str, path: str, status: str) -> dict:
    if status == "D":
        return {"path": path, "mode": "100644", "type": "blob", "sha": None}
    blob = git("cat-file", "blob", f"{commit}:{path}", cwd=repo_dir).encode("utf-8", "surrogateescape")
    # `git cat-file` decodes text; get the bytes back exactly as git has them
    raw = subprocess.run(["git", "cat-file", "blob", f"{commit}:{path}"], cwd=repo_dir,
                         capture_output=True).stdout
    if blob != raw:
        raw = blob
    created = call(token, f"/repos/{repo}/git/blobs", "POST",
                   {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
    executable = git("ls-tree", commit, "--", path, cwd=repo_dir).startswith("100755")
    return {"path": path, "mode": "100755" if executable else "100644", "type": "blob", "sha": created["sha"]}


def push(token: str, repo: str, branch: str, repo_dir: pathlib.Path, remote_head: str | None,
         local_ref: str = "HEAD", dry_run: bool = False, log=print) -> dict:
    commits = commits_to_send(repo_dir, remote_head or "HEAD", local_ref)
    if not commits:
        log("nothing to send: the remote already has every local commit")
        return {"sent": [], "head": remote_head}

    parent_sha = remote_head
    parent_tree = None
    if parent_sha:
        parent_tree = call(token, f"/repos/{repo}/git/commits/{parent_sha}")["tree"]["sha"]

    sent = []
    for commit in commits:
        message = git("log", "-1", "--format=%B", commit, cwd=repo_dir).rstrip() + "\n"
        author = git("log", "-1", "--format=%an%x00%ae%x00%aI", commit, cwd=repo_dir).strip().split("\x00")
        entries = [blob_entry(token, repo, repo_dir, commit, path, status)
                   for path, status in changed_paths(repo_dir, commit)
                   if status is not None]
        log(f"  {commit[:8]}  {len(entries)} path(s)  {message.splitlines()[0][:70]}")
        if dry_run:
            sent.append({"commit": commit, "paths": len(entries), "message": message.splitlines()[0]})
            parent_sha = commit  # only used for the log line in a dry run
            continue
        tree = call(token, f"/repos/{repo}/git/trees", "POST",
                    {"base_tree": parent_tree, "tree": entries}) if parent_tree else \
               call(token, f"/repos/{repo}/git/trees", "POST", {"tree": entries})
        created = call(token, f"/repos/{repo}/git/commits", "POST", {
            "message": message, "tree": tree["sha"],
            "parents": [parent_sha] if parent_sha else [],
            "author": {"name": author[0], "email": author[1], "date": author[2]},
            "committer": {"name": author[0], "email": author[1], "date": author[2]},
        })
        parent_sha, parent_tree = created["sha"], tree["sha"]
        sent.append({"commit": commit, "remote": created["sha"], "paths": len(entries),
                     "message": message.splitlines()[0]})

    if dry_run:
        log(f"would update {branch} to {parent_sha[:12] if parent_sha else '?'} (dry run: nothing sent)")
        return {"sent": sent, "dry_run": True}

    call(token, f"/repos/{repo}/git/refs/heads/{branch}", "PATCH",
         {"sha": parent_sha, "force": False})
    log(f"{branch} is now {parent_sha[:12]} ({len(sent)} commit(s) sent through the API)")
    return {"sent": sent, "head": parent_sha}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--dir", default=".", help="the local repository")
    parser.add_argument("--token-file", default="/tmp/.ghtok", help="a file holding a token (never a command line)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    repo_dir = pathlib.Path(args.dir).resolve()
    token_path = pathlib.Path(args.token_file)
    if not token_path.is_file():
        print(f"no token at {token_path}: put one there (chmod 600) and try again", file=sys.stderr)
        return 2
    token = token_path.read_text().strip()

    # where the remote is, according to the API rather than to git: the local remote-tracking ref may be stale
    try:
        head = call(token, f"/repos/{args.repo}/git/ref/heads/{args.branch}")["object"]["sha"]
    except SystemExit:
        head = None
    print(f"{args.repo} {args.branch} is at {(head or 'nothing')[:12]} on the remote")
    result = push(token, args.repo, args.branch, repo_dir, head, dry_run=args.dry_run)
    for entry in result["sent"]:
        print(f"  {'would send' if args.dry_run else 'sent'}: {entry['commit'][:8]} {entry['message'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
