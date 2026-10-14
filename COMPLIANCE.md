# What this project does and does not do with GitHub

Written down because a previous account of mine was suspended, and the reason is easy to repeat by
accident.

## The suspension

The earlier project ran Minecraft **on GitHub's runners** and streamed the desktop out
(`.github/workflows/play.yml` in the archived `webmc` repository: Xvfb + x11vnc + noVNC + a tunnel).
That is the violation: GitHub Actions may be used for building, testing, deploying and publishing a
software project, not as a game host or a streaming box. It consumed hours of their compute and
streamed their bandwidth out of it, every time.

## Making a repository private does not help

This is the part that is most often wrong in advice passed around:

* **The violation is the resource use, not the visibility.** Private repositories run on the same
  runners, with the same abuse telemetry - long-lived jobs, sustained CPU, sustained network egress
  and tunnels are what get noticed, and none of that is hidden by a private setting. If private
  repositories were a loophole, mining cryptocurrency in one would be free.
* **Private does not make redistribution legal either.** Copyright applies the same way; a private
  repository is just less likely to be *reported* by somebody who sees it.
* **Opening another account to carry on after a suspension is circumvention**, which is a much worse
  problem than the original suspension - it typically takes the new account, and anything linked to
  it, with it.
* Suspensions for this are **not time-limited**. They do not expire; they are appealed, and an appeal
  that amounts to "it was private" is not a defence.

## What this repository does instead

* **The visitor's browser does the work.** Nothing is streamed, nothing runs on a server: a client
  either compiles to wasm ahead of time or runs Mojang's jar under a browser JVM, on the visitor's
  own machine.
* **No game files are committed.** The repo is code, fixtures and documentation. The browser fetches
  clients from Mojang directly.
* **Other people's ports are linked, not copied.** If a version is only playable through somebody
  else's build, the launcher links it where it lives.
* **Long builds are manual.** The 45-minute compile only runs when a person dispatches it, so no
  push ever starts a large job by surprise.
* **Nothing serves the game continuously.** Pages serves a static launcher and the files the launch
  page needs; there is no session, no tunnel, no stream.
* Publishing a *compiled build* stays a grey area, which is why the launcher offers it as a download
  and why the file is standalone rather than served as a play page.

`tests/test_pipeline.py` asserts these things: no workflow can mention display, streaming or tunnel
tooling, and the long job stays manual. If that test fails, something has been added that would
recreate the problem.

## Where any of this may run

Asked often enough to be written down once. The rule underneath: **building** this project is fine
anywhere; running a *game session* on somebody else's compute is not.

| Where | Runs the game? | Notes |
| --- | --- | --- |
| GitHub Actions (public repo) | **no** | 4 vCPU / 16 GB, unlimited minutes, and the terms cover building, testing, deploying and publishing *this software*. A runner as a game host is the unrelated activity that got the earlier account suspended. |
| Azure Pipelines / GitLab SaaS / CircleCI / Travis / Codespaces | **no** | same clause, same shape; the smaller ones could not fit a client anyway |
| Colab, Kaggle, Hugging Face Spaces, Vercel/Netlify/Cloudflare free tiers | **no** | notebook and static-hosting platforms; CI-style game hosting is against their terms |
| **Oracle Cloud Always Free** | **yes** | a general-purpose VM you own: 4 ARM cores, 24 GB RAM, 200 GB disk, 10 TB egress/month, permanently. Needs a card for verification (not charged); "out of capacity" is common |
| **Azure for Students** | **yes** | no card, student verification; credit runs a 4 vCPU box for weeks |
| **a machine a person owns** (this Mac, a friend's PC, an old laptop) | **yes** | the only shape with no third party in the middle: open the page on that machine and let it host itself |
| Google Cloud free trial | yes, but it ends | the Always Free e2-micro has 1 GB RAM and cannot run a client |

No credit card at all? Then: the machine you already have, a friend's PC, or **Azure for Students**
(student verification, no card). Signing up under a false identity, cycling through free trials, or
"disguising" what a job does are fraud and circumvention - they are not workarounds this project
describes, and they are the fastest way to lose an account and everything linked to it.

Two consequences worth remembering: **a static host is always allowed** (the launcher, the boot screen,
the downloads are just files, and the game runs in the visitor's tab), and **the CI job here only ever
compiles** — it fetches nothing at play time and runs no session.

## The streaming stack is gone, on purpose

This repository used to carry a self-hosted desktop-streaming stack (`stream/`, plus its costs and host
notes). It was removed: a streamed session needs a machine of your own or a paid host (≈ $33/month CPU,
$1.25/hour GPU), it is the shape that costs an account, and the browser-native rows are strictly better
for the person playing. The one thing worth keeping from that work was the answer to "where could it
run at all", which is the table above.

## Compiled clients are served as pages (a decision, with its risk)

The launcher used to offer a compiled client only as a *download*, because serving a client is serving a
build of the game. It now also serves one as a page: the client is split into chunks under
`site/clients/<version>/` with a hash each, and a small start page reassembles it in the visitor's browser.
That is the operator's decision, taken knowingly - the download is still published beside it, and the
sections above about licences, attribution and what this repository may host still apply.

## Engines have to be licensed

Every compiled-client engine (a "workspace") is somebody else's source tree, and it ends up inside the
build. So an engine is only wired up here if its licence allows it - MIT and Apache-2.0 do, with the
notice kept - and a repository with **no licence file is not usable**, because the default is all rights
reserved. `ports.json` carries the licence on each engine for exactly this reason, and one 26.2 workspace
is disabled because of it. TeaVM itself is Apache-2.0, which is what makes writing our own engine a
clean path.

## The older repositories

Three earlier attempts (`webmc`, `webmc-browser`, `webmc-fabric`) lived on an account GitHub suspended.
Their working copies were deleted from this folder; **their complete git history is still here**, as
bundles in `backups/`: `git clone <bundle> <dir>` restores any of them, and that folder is the only
remaining copy. Nothing in the current project depends on them.
