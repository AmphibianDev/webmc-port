# Where it stands

Written for whoever comes back and wants the state without reading a build queue. Every line is either
verified (with where to look) or marked as not.

## Live now

**https://amphibiandev.github.io/webmc-port/clients/1.21.11/** — **Minecraft 1.21.11, playable in a page.**

* compiled from the **official Mojang client jar** at build time (never committed),
* by the MIT-licensed engine (Gaius), with our own boot screen in front of it — verified in the served
  bytes: `id="webmc-boot"`, and the engine's own launcher page removed,
* served as 6 chunks (108 MB) with a `manifest.json` and a start page that fetches them, **verifies each
  chunk's sha256**, reassembles them in the browser and runs the client in a fullscreen-capable frame,
* published as `packed-1.21.11.tar.gz` (81 MB) in the `built-clients` release; the catalog job unpacks it
  onto the site, which is what uploading rather than pushing is for (see below).

The launcher (**https://amphibiandev.github.io/webmc-port/**) lists **919 versions**, each with its route,
the engine that would build it, that engine's licence, and the mod policy.

| Route | Versions | What it means |
| --- | --- | --- |
| `real-java` | 349 | official jar in a JVM in the page; **Fabric/Legacy Fabric load at play time** |
| `needs-shim` | 398 | the same, waiting on the JavaScript LWJGL 3 |
| `aot` | 172 | compiled by an engine; mods go in before the compile |

## Fabric: where it works, and where it is being built

| Versions | How a Fabric mod is loaded | State |
| --- | --- | --- |
| **1.0 – 1.12.2** | Fabric Loader at play time, in the page's JVM | ✅ works; the launch page now loads CheerpJ from 4.3 → 4.2 → 3.1 with fallbacks, because one CDN script failing used to end a launch |
| **1.13 – 1.14.4** | our build-time bake into a workspace engine | wired (`tools/build_modded_workspace.py`), builds dispatched |
| **1.21.11** | **our own bake** (`tools/bake_mods.py`): the mod's classes are copied into the client and its entrypoint is called from the client's own static initialiser | verified locally; a CI run is testing the whole chain now |
| 1.16.5 / 1.19.2 / 1.20.4 | nothing can run these in a browser at all yet (no compiled engine exists; the browser JVM needs the LWJGL 3 shim) | ❌ |
| April Fools snapshots | same route as the release they lead to; each needs its own patch fixes | 25w14craftmine and 24w14potato have been attempted and fail on version-specific patchers |

### What the bake can and cannot do

A compiled client has **no class loader at play time**, so a mod cannot be loaded the way Fabric loads it.
The bake therefore:

1. reads each jar's `fabric.mod.json` for its client entrypoint,
2. writes `WebMcModBakePatcher` into the engine's own patch chain (a Java class with
   `main(jar, classOutputDir, version)`, exactly the contract the engine's patchers use),
3. which copies every class out of the mod jars into the patch output the build merges over the client,
4. and injects one call — `webmc/baked/WebMcBakedMods.initialize()` — at the end of
   `net/minecraft/client/Minecraft`'s static initialiser,
5. while `WebMcBakedMods.java` (a source override for that version) instantiates and calls each mod's
   entrypoint, which is what Fabric Loader would have done.

**Mixins are not applied yet.** A mod whose behaviour is mixins needs those transformed into the client at
build time, which is a bigger job; the bake reports which mods carry them so nobody is surprised.

That this works is not a hope: `tests/test_bake_mods.py` compiles the generated patcher with a real
`javac`, runs it against a client jar it builds itself, and reads the result back with `javap` — the class
copied, the `<clinit>` rewritten, the `invokestatic` on the way out. 933 checks pass in total, offline.

## Starting a build (no token needed)

```sh
git tag build-1.21.11                     && git push origin build-1.21.11              # vanilla
git tag build-1.21.11+hello-fabric        && git push origin build-1.21.11+hello-fabric   # with a mod set
python3 tools/watch_builds.py                                                            # the queue, and the last report
```

The mod set travels in the tag name because a tag is the one way to start a build without a token. A
modded build compiles the example mods from `mods/examples/`, copies the set out of `mods/`, pre-flights the
generated patcher with `javac` (2 minutes, instead of discovering a Java mistake an hour in), bakes, then
compiles, packs and publishes.

## Four things that cost a full build each, now written down and tested

1. **`case "$RAW" in *+)` never matches**, so a mod set in a tag name was silently dropped and the build
   produced a healthy vanilla client. `tests/test_workflows.py` now runs the workflow's own parsing lines
   with four tags.
2. **`JAVA_TOOL_OPTIONS` breaks JDK detection**: it makes every `javac` print "Picked up …", and any tool
   parsing `javac -version` then sees an unparsable version. Five runs failed with "requires JDK 21 or
   newer" on a machine that had one.
3. **A 16 GB runner with no swap** dies in the middle of the TeaVM link or a parallel patch chain, and a
   job killed by the OOM killer never runs its `always()` steps: no report, no log. The build job adds
   16 GB of swap.
4. **Every git push from a tag-triggered run failed** while every release upload worked. So the build
   publishes `packed-<version>.tar.gz` as a release asset and asks the catalog job to run, which fetches
   it (`tools/fetch_packed.py`), records it in `state.json` and deploys — a division of labour that uses
   only the channels that work.

## Still not there

* **Mixins at build time**, which is what turns "a mod's entrypoint is called" into "a real mod behaves".
* **1.16.5 / 1.19.2 / 1.20.4**: no route exists. Either the LWJGL 3 shim (`lwjgl3-webgl/`, 216 natives,
  the first shader still failing — its report is on the `shim-log` branch) or our own AOT engine.
* **26.x**: out of scope by decision (Java 25 and a rewritten renderer).
* **April Fools snapshots**: each is its own patch job against the engine's chain, like 1.21.4 and 1.21.10.

## 2026-10-15 (later): the front end the user actually looks at

**Launcher.** `site/index.html` is the real launcher's shape now: a version column on the left with a search
box, kind chips (release/snapshot/beta/alpha), a "startable only" switch and a curated default of twelve
rows, and a detail card on the right with the selected version's route, engine, licence, mod policy and its
play target. One function decides all of it - `playTarget()` - and it answers in five ways: a page this site
serves, the Java applet page for assets that are not baked yet, a direct download, somebody else's link (with
a warning that it only downloads), or nothing at all, which offers the nearest version that does start. The
multiplayer truth is on the front page too, because it is the question everybody asks: a browser cannot open
a TCP socket, so a typed server address is reached through a relay node, the default one being
`wss://ellan.site/tunnel`, and self-hosting is a documented environment variable away.

**Loading.** The packed page fetches its chunks in parallel now - they are independent files with a hash
each, so six chunks of 18 MB should cost one chunk's wait, not six - and the boot screen's bar is monotonic:
two things reporting progress on different scales is what "the bar went backwards" always was, so a step
that reports less than the last one is ignored rather than shown. The screen names the version it is loading,
has a soft gradient on the fill, and carries no engine branding.

**Branding, settled with bytes rather than guesses.** Fetching the first 400 KB of the released client and
counting settled where the "big Gaius logo" comes from: the engine injects its own launcher shell into the
packaged page (a GAIUS CLIENT header and a footer that carried the "not affiliated with Mojang Studios"
sentence) plus a `#boot-brand` block that renders GAIUS CLIENT over the loading screen. Our strip had already
removed the header and the footer - zero occurrences of either in the shipped bytes - and missed `#boot-brand`,
whose id contains no brand word and only its text does. The tool now removes `#boot-brand` and hides
`#boot-screen`, the shell header/footer, the profile kicker/subtitle/legal lines and anything the engine
builds later under `[data-gaius-shell]`. Hidden rather than removed where the engine's own script looks the
element up: a null there is a client that stops, and a hidden element gives the same result safely.

**Multiplayer, checked rather than assumed.** The default relay answers `/relay-node/v1` with
`ok:true, protocolVersion 1, availableConnections 64/64, requiresToken:false, allowsPrivateTargets:false`:
so typing a public server address needs no token and should work, a LAN/private address is refused by design,
and the failure mode to look for is a public server that will not accept the relay's connection.

**The button textures question, answered honestly.** The screens the game draws are the official client's own
screens, buttons included: this engine ships no `textures/gui` of its own (the repository has no such files),
so there is nothing of ours to restyle. What is *not* vanilla is the shell we now hide and the engine's four
extra screens for browser-only facts (profile name, relay editor). Those exist because a browser client needs
them; they are additions beside the vanilla UI, not a repaint of it.
