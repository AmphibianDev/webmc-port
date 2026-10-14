# The goal: Fabric mods on every playable version, and loading that feels instant

Written down because "Fabric on all versions" is three different problems wearing one name, and the answer
differs per era. Fast loading is a fourth problem and mostly orthogonal.

## The three shapes of "Fabric support"

| versions | how the client runs | how a Fabric mod gets in | state |
|---|---|---|---|
| **1.0 - 1.12.2** | Mojang's jar in a real JVM (CheerpJ, LWJGL 2 natives) | **Legacy Fabric / Ornithe** on the classpath - a real class loader, so jars load at play time | route exists, nothing wired yet |
| **1.13 - 1.20.4** | our LWJGL 3 shim under the same JVM | **Fabric Loader + its libraries on the classpath**, before the game; our own loader assembles that classpath | blocked on one bug: the first frame |
| **1.20.5 - 26.x** | a client we compile (Java 21/25, no class loader exists at play time) | **baked at build time**: mod jars are applied before the link, by `mods/build_modded_workspace.py` | 26.2 builds; the bake-in path is unproven |

The one that people actually mean by "add a mod" is the middle row: drop a jar in, it loads, no rebuild.
That is possible **only** under a real JVM, which is exactly why the shim matters more than any port.

## What is already true, so the plan starts from reality

* The shim runs a JVM in a browser, registers **216 natives**, creates a window and a WebGL2 context, and
  stops at the first shader (`glCreateShader` returns null - the context report now says whether the
  context exists or was lost).
* `mods/injector/` already patches a built client with a page-tier API (overlay, keybinds, tick/render,
  chat) - that is the layer a mod author writes against when a jar cannot run.
* `mods/build_modded_workspace.py` already plans, prunes and hooks a Fabric mod set for a build.
* The launcher classifies every version by date, so snapshots and April Fools snapshots are routed with
  the same rules as their release, and 274 versions are playable today by one of the three routes.

## Milestones, in order, each one verifiable

0. **First frame in the shim.** Fix the null from `glCreateShader`; then shaders, geometry, draw, and a
   pixel read back. Everything in the middle row is behind this one bug.
1. **Boot a real client through the shim**: 1.16.5 first (Java 8), then 1.18.2, 1.19.4, 1.20.4 (Java 17).
   Success is the title screen, not "it started".
2. **Fabric Loader on that classpath** for 1.16.5: our loader puts `fabric-loader` and its libraries
   before the game, plus one trivial mod whose mixin writes a line. Success is that line appearing.
3. **Our loader, manifest driven**: `mods.json` per version listing loader + mods + our API shim; the
   launcher grows an "add a mod" action that edits that manifest and nothing else. No rebuild, no CDN.
4. **Legacy Fabric for 1.8.9 - 1.12.2**, same classpath trick, different loader jar. This gives Fabric on
   the real-Java versions that exist today.
5. **Bake-in for 1.20.5 - 26.x**: apply mod jars at build time in the workspace pipeline. Document honestly
   which mods survive (anything reflection- or native-heavy does not) - it is not "install a jar".
6. **Snapshots and April Fools**: nothing new to build. They route by date through whichever of the three
   rows applies; the work is verifying a few of each on the way past.

### Where these stand now

* **Milestone 0 (first frame)** is unchanged and still the blocker in front of the middle row.
* **Milestone 3 (manifest driven)** is built and tested offline: `site/launch.json` + `site/plans/`
  describe every version and mode, `mods/mods.json` is the mod manifest, and our own loader
  (`mods/loader-jvm/`, Java 8, reproducible) starts vanilla mods and hands Fabric the properties it
  reads. `LOADER.md` is the account; `tests/test_launchplan.py` and `tests/test_loader.py` are the
  proof that needs no browser. What is missing is the same thing milestone 1 is missing: a browser
  that has run it.
* **Milestone 4 (Legacy Fabric on 1.8.9 - 1.12.2)** needs no more code: Legacy Fabric's own profile
  becomes the classpath, and its Maven was measured to send CORS through its redirect. The launcher
  offers `fabric` and `legacyfabric` per version today, and starts refusing honestly where the wall is
  real (1.13+ and the shim, 1.20.5+ and Java 21).
* **Two constraints measured while wiring this up**, both of which bound what any row can do:
  `resources.download.minecraft.net` sends no `access-control-allow-origin`, so asset *objects* cannot
  be fetched by the page or by Java inside CheerpJ; and CheerpJ 4.3 tops out at Java 17, so the
  compiled row is not a workaround, it is the only route for 1.20.5+.

## Fast loading, which is a separate list

The long waits are downloads and JVM start, not the game:

* **Cache the payloads.** Client jars, libraries, the loader and mod jars go in Cache Storage keyed by
  URL, so a second visit starts with nothing to fetch. The launch page does this already for libraries;
  the same cache should hold mods and the AOT clients.
* **Service worker for the AOT clients.** A 39 MB single-file client should be served from cache after the
  first visit, and the brotli payloads decoded once rather than per load.
* **Warm on intent, not on load.** Opening a version card should start fetching that version's jar and
  libraries in the background, so pressing play is not the beginning of a download.
* **Ship the smallest classpath that boots.** The launcher should ask the version's metadata which
  libraries it needs and fetch those, not a superset; the measured library set already exists per version.
* **Assets after the title screen.** Textures and sounds are the largest payload and nothing needs them
  until the game draws. Load them lazily and let the first frame arrive sooner.
* **Mods last, in parallel.** Loader first, mods while the main menu is up, so a mod list does not delay
  the first frame.

## The honest ceiling

* Mods with native libraries cannot work in any row - there are no native libraries.
* Mods that need a Java version the browser JVM does not have cannot work in the middle row.
* Mods that lean on desktop-only GL features (geometry shaders, compute) will not work until the shim's
  GL coverage grows; the measured surface tells us exactly which members a client calls, so gaps are
  discovered by running clients rather than by guessing.
* A public streaming or hosting service for this stays out: the only machine that runs the game is one a
  person owns, which is where all three rows already put it.

## The starter: mods that compile in front of you, in fifteen minutes

The shape asked for: a `mods/` folder, a start command, and the mods are compiled into the client as it
starts. That is two different mechanisms, and the honest design says which one is instant and which one has
a budget.

### Where it is instant: the real JVM routes (1.0 - 1.20.4)

Under a real JVM there is nothing to compile ahead of time - the class loader is the mechanism:

```
/mods/
  my-mod/                 sources, a built jar, or a Fabric mod jar - all three work
  my-mod/mod.json         what to load and when (optional; sensible defaults otherwise)
start.sh 1.16.5           compiles what needs compiling, assembles the classpath, opens the client
```

`start.sh` does, in order: read the version's route (Fabric Loader for 1.14+, Legacy Fabric for 1.8.9 -
1.12.2, our own loader for the page-tier API); compile any `mods/*/src/**/*.java` with `javac` against the
client's own jars, which is seconds, not minutes; put the result, plus every `mods/*.jar`, **before** the
game on the classpath; then start. Success is a served game with the mod live, and the whole start is
seconds - the client jar and libraries are already in the browser cache after the first visit.

That is the row where "compiles in front of you" is literally true, because the JVM compiles and loads
classes anyway.

### Where it has a 15-minute budget: the compiled routes (1.20.5 - 26.x)

Those clients are ahead-of-time compiled, so a mod cannot be loaded at play time and must go in **before**
the link. The starter therefore runs the same pipeline our 26.2 build runs, but incrementally, and the
budget is the point:

| step | cold (first ever run) | warm, one mod changed | how |
|---|---|---|---|
| gradle compile | ~25 s | ~5 s | gradle build cache, daemon-less, `--offline` after the first resolve |
| mod sources (`mods/*/src`) | ~1 min | ~10 s | compiled against the client's classpath, only changed files |
| TeaVM wasm link | ~20 min | **~8 min** | incremental link: only mod-owning classes recompile; the client's 102 MB image is reused |
| brotli recompress | ~4 min | **~1 min** | only payloads whose bytes changed; unchanged `.br` files are reused |
| package single HTML | ~20 s | ~20 s | the packager already does this |
| **total** | ~45 min (today's cold build) | **~15 min or less** | |

Rules that keep it inside the budget: the link cache in `wasm-toolchain/standalone-link-cache.js` is kept
between runs; a mod that changes nothing in the client's own classes must not trigger a client relink; and
the precompressed wasm is reused unless the link actually produced new bytes.

### What the starter does not promise

* Baked mods must be **AOT-friendly**: no native libraries, no runtime bytecode generation, no assuming a
  class loader exists. A mod that reflects its way around the game will not survive 26.x, and the starter
  says so before it spends fifteen minutes finding out.
* On the real-JVM rows the mods are ordinary Fabric mods, with the ordinary caveats: a mod needing a Java
  version the browser JVM lacks, or a GL feature the shim has not grown, will not run.
* Fifteen minutes is the target for **one changed mod**, measured from a warm cache on the machine doing
  the build; the first build of a version is the ~45 minutes the 26.2 job already takes.

### The order this lands in

1. `start.sh` for the real-JVM rows, with `javac` over `mods/`, because it is small and instantly useful.
2. The manifest (`mods.json`) that both rows read, so the launcher can add a mod by editing one file.
3. The incremental build for the compiled rows, with the budget above as its acceptance test: **one mod
   changed, fifteen minutes, from the warm cache**.
