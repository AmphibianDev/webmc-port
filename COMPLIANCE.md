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
