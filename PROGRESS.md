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

## 2026-10-15 (evening): multiplayer that works, and mods that are really merged

### Multiplayer

A browser page cannot open a TCP socket - the API does not exist, which is why every browser client that joins a
normal Java server has a gateway behind it. `gateway/server.js` is ours: one file, no dependencies, and it speaks
the protocol the downloaded 26.3 client already implements (`goldproxy.v1`), which is what makes it useful now
rather than after a rewrite of somebody else's client.

The protocol was read out of the client's own modules, extracted with `tools/eagler_offline.py` and verified
against the sha256s in the file it came from: `POST /v1/token` for a short-lived token scoped to one destination,
then one WebSocket carrying control frames (open/ready/ack/ping/end/error) and raw TCP bytes, with the page
owning Minecraft's framing and AES/CFB8 so the gateway never holds a key and never parses a packet. Its one
packet-shaped check is that a session starts with a Minecraft handshake, before the target sees anything,
because a gateway that dials any port for any byte stream is a port scanner with extra steps. Private and
reserved targets are refused by rule, by name, and again after DNS.

`tests/test_gateway.mjs` is the protocol rather than a description of it: 25 checks over a real socket and a real
WebSocket, including every refusal, a browser preflight, and the flow-control window. `MULTIPLAYER.md` is the
written form.

Playing the client you already have:

```sh
python3 tools/play_local.py                 # gateway + page, one command, stops on Ctrl-C
python3 tools/play_local.py --server mc.example.com
```

It serves the client with the gateway set inside it, and the patch is one script tag: removing it gives the file
back byte for byte, which is checked against what was actually served - 200 MB of somebody else's payload is not
something to reformat. 13 checks, no mocks.

### Mods

Two blockers, both now understood rather than guessed at.

**Entrypoints** work: the bake instantiates each mod's `ClientModInitializer` and calls it from the client's own
static initialiser, as bytecode. The previous failure was our own generated Java naming the mod's package - a
compile error the engine reports as `package com.example does not exist`, because its sources are compiled before
the patched classes are on its classpath. The hook class is gone.

**Mixins** now work for the shapes that matter. `mods/mixin-applier` merges a mod's mixin classes into the
client's class files before the client is compiled, so the compiler sees the merged result: no reflection, no
interpreter, no runtime loader. Applied: `@Mixin` by class or name, `@Overwrite` when the descriptor matches,
`@Inject` at HEAD/RETURN/TAIL for `(CallbackInfo)` handlers, `@Unique` members (self-references redirected to the
target), and `@Shadow` - never copied, because after the merge a shadowed call resolves to the target's own
member, which is the only reason a shadow exists.

Refused, loudly, as a failed build: a mixin whose target is not in the client, a descriptor naming a class the jar
does not carry, an `@Overwrite` or `@Inject` of a method that is not there. Reported by name but not fatal:
handlers taking the target's own arguments, `@At` selectors beyond the three, `@Redirect`, `@ModifyArg`,
`@ModifyVariable`, `@Accessor`, access wideners. Half a mixin is a client that looks fine until it does not.

The test is a JVM run: compile a client, compile a mixin mod against this repository's annotation stubs, apply the
mixins, then *run* it and read what it printed. 17 checks including that the shadowed field read the target's real
value and that the build-time annotations did not travel into the compiled client. `MIXINS.md` is the contract;
`CREDITS.md` credits SpongePowered/Mixin for the annotations and their meanings.

**Meteor Client cannot load here yet**, and the reason is specific: its descriptor declares six mixin configs, an
access widener, and its code calls Fabric API throughout. The applier has no Fabric API to offer and does not
implement access wideners or `@Redirect`. What can be baked today is a mod written as an entrypoint plus the
mixin shapes above - `mods/examples/hello-mixin` is exactly that, opt-in with
`build-1.21.11+hello-fabric,hello-mixin`.

### The branding, settled with bytes

Fetching the first 400 KB of the released client and counting settled where the "big Gaius logo" comes from: the
engine injects its own launcher shell (a GAIUS CLIENT header, a footer carrying the "not affiliated with Mojang
Studios" sentence) plus a `#boot-brand` block that renders GAIUS CLIENT over the loading screen. Our strip had
already removed the header and footer; it missed `#boot-brand`, whose id contains no brand word and only its text
does. The tool now removes that block and hides the rest (the engine's own boot overlay, the profile
kicker/subtitle/legal lines, anything it builds later under `[data-gaius-shell]`) - hidden rather than removed
where the engine's own script looks the element up, because a null there is a client that stops.

### Loading

The packed page fetches its chunks in parallel and its boot screen cannot go backwards. The bar is monotonic
because two things reporting progress on different scales is what "the bar went backwards" always was. The screen
names the version, and for a modded build it names the mod set: "Minecraft 1.21.11 - with hello-fabric baked in".

## 2026-10-15 (late): the account was suspended, and what is kept instead

Pushes to `AmphibianDev/webmc-port` stopped at 12:03 UTC with "Your account is suspended". The repository and the
site kept serving (both answered 200 while this was written), the running build kept running, and every commit is
in the local history - but nothing can be pushed to that remote until the suspension is resolved or the project
moves.

**The likely cause is visible in this repository's own history.** `site/clients/` held 108 MB of a compiled
client in six chunks, and it was committed three times. `.git` grew to 259 MB (578 MB in a clone, before packing),
and the largest blobs in the whole history were those chunks. That is a repository carrying a build of the game in
every clone - the thing this project's COMPLIANCE.md says it does not do, and the pattern that ended the previous
account. `site/clients/` is now in `.gitignore`, the tracked files are removed from the index, and COMPLIANCE.md
records both the decision and that earlier revisions got it wrong.

**What still works:** the release asset is the copy of record for a compiled client; the catalog job fetches it
into the working tree it deploys (`tools/fetch_packed.py`), and the deploy uploads that tree rather than the git
history - so the site serves the page and a clone of this repository does not carry it.

**A clean history, ready to move.** `~/devspace/WebMC/backups/webmc-port-history-cleaned.bundle` is the whole
project with the client chunks removed from *every* commit: 231 commits, 1.4 MB of history instead of 578 MB, and
the full test suite passes on a fresh clone of it. That is the artifact to push to a new host; the local
repository keeps its history as it is.

**For a fresh clone to be green.** A missing third-party jar (the ASM jars in `work/asm`, a mod jar in
`mods/samples`) is now a skip with a message, not a failed test. A suite that goes red on a clean checkout is a
suite people learn to ignore.
