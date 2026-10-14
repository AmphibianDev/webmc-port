# Where it stands

Written to be read when nobody is watching the build queue. Everything below is either verified (with
where to look) or explicitly marked as not.

## Live right now

**https://amphibiandev.github.io/webmc-port/** — the launcher, with `routes.json`: **919 versions**, each
saying how it runs, which engine would build it, that engine's licence, and what the mods policy is.

| Route | Versions | What it means |
| --- | --- | --- |
| `real-java` | 349 | the official jar runs in a JVM in the page, **Fabric/Legacy Fabric mods load at play time** |
| `needs-shim` | 398 | the same, waiting on the JavaScript LWJGL 3 (`lwjgl3-webgl/`) — it boots a JVM, opens a WebGL2 context, stops at the first shader |
| `aot` | 172 | no browser JVM runs this client's Java, so an engine compiles it, mods baked in at build time |

## What works, and where the proof is

| Thing | Proof |
| --- | --- |
| **1.21.11 compiles from the official jar** | `client-1.21.11.html`, 107 MB, in the `built-clients` release; the build log says `BUILD_EXIT=0` and `Portable Gaius HTML … (113005251 bytes)` |
| **26.2 compiles** | `client-26.2.html`, 115 MB, same release |
| **Packing a client into a page** | `tools/pack_client.py`; the 1.21.11 run reported `status: packed, packed: 113025295` |
| **The loader's brain** | `tools/route.py` (routes every version) and `tools/fetch_packed.py` (puts a packed client on the site) |
| **Our own JVM loader** | `mods/loader-jvm/`, verified in a real JVM: starts a mod, the mod reads a game class, sets the five properties Fabric reads, reproducible jar |
| **The virtual machine the mods folder promised** | 1.0 – 1.12.2: real JVM, real jars, at play time |
| **Offline checks** | `bash run-tests.sh` in `webmc-port` — 862 checks, no network, no JDK, no browser |

## In flight / next

1. **1.21.11's page**: the build is re-running; when it finishes it uploads `packed-1.21.11.tar.gz` as a
   release asset and asks the catalog job to run, which unpacks it to `site/clients/1.21.11/` and pushes.
   Then `https://amphibiandev.github.io/webmc-port/clients/1.21.11/` is a playable page.
   Check: `python3 tools/watch_builds.py`, then `curl -sI .../clients/1.21.11/`.
2. **The shim's first frame**: `build-log/shim-report.md` on the `shim-log` branch is the report. It now
   logs every shader native on the way in and out, and answers a handle of 0 directly, because the last
   report said only "glGetShaderi entered, then 405 seconds of nothing".
3. **1.21.4 / 1.21.10 / 1.21.11-pre5**: they build through the patch chain and fail on version-specific
   patchers; three fixups exist (`tools/aot_fixups.json`) and each failure has been one line. Not needed
   for v1 (1.21.11 is the target), but they are how the 1.21 line grows.

## What is honestly not there yet

* **Fabric on a compiled version.** Every mod-capable engine is unlicensed, and the licensed one (Gaius,
  MIT) has no mod hooks at all. `MODERN-JAVA.md` has the three ways in, in the order worth trying; the
  build job **refuses** a mod set rather than producing a vanilla client called modded.
* **1.13 – 1.20.4.** No engine exists for those clients anywhere on GitHub that is licensed, and the
  LwJGL 3 shim that would let the real JVM run them has not drawn a frame.
* **26.x.** Out of scope by decision: Java 25 plus a rewritten renderer (`blaze3d/pipeline/*` →
  `com/mojang/renderpearl/*`). `ports.json` records it as a blocker with the reason, and the plan says
  "out of scope" rather than offering a build that cannot happen.
* **Snapshots of the 1.21 line** route correctly and have a generated profile, but none has been built
  yet — the same patcher work as the releases.

## Things that cost time and are written down so they do not again

* A duplicated YAML key in a workflow made GitHub reject the whole file: every dispatch died in the same
  second with no jobs. `tools/workflow_lint.py` exists because of it.
* `JAVA_TOOL_OPTIONS` makes every JVM print a "Picked up …" line, which breaks any tool that parses
  `javac -version` — the engine's JDK detection did exactly that, and five runs failed with "requires JDK
  21 or newer" on a machine that had a JDK 21.
* A runner has 16 GB and no swap: the TeaVM link and a parallel patch chain both die there. The build job
  adds 16 GB of swap.
* Every git push from a tag-triggered build run failed while every release upload worked. The build now
  publishes and hands off to the catalog job, which pushes.

## Commands

```sh
cd ~/devspace/WebMC/webmc-port
bash run-tests.sh                         # everything offline: 862 checks
python3 tools/watch_builds.py             # the build queue, and the last report
python3 tools/route.py --version 1.21.11  # how one version runs
python3 tools/route.py --span 1.0 1.21.11 # the coverage, by route
python3 tools/fetch_packed.py --all --record   # put packed clients on the site (what the catalog job does)
git tag build-1.21.11 && git push origin build-1.21.11   # build one version (≈45–75 min)
```
