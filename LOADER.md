# The loader: one launcher, three engines, one mods folder

This is the piece that answers "let the user pick a version, vanilla or Fabric, and add mods".
It is two things that work together:

* **the plan** (`tools/launchplan.py` → `site/launch.json` + `site/plans/<version>.json`): for every
  version, what to load and in what order. Data, not code - adding a version does not change a page.
* **the loader** (`mods/loader-jvm/` → `site/loader/webmc-loader.jar`): a few kilobytes of Java 8 that
  reads a plan and starts the game, with either Fabric Loader or with our own mod door.

Nothing here holds Minecraft. The client jar comes from Mojang, the loader's own libraries from
Fabric's Maven, the mods from this repository - all fetched by the visitor's browser.

## The three rows, and why the mods differ

A version's row is decided by its own metadata (`tools/jvm_route.py`), not by a list of favourites.

| row | versions | engine | vanilla | Fabric | a jar dropped in `mods/` |
| --- | --- | --- | --- | --- | --- |
| **cheerpj** | 1.0 - 1.12.2 | the official jar under CheerpJ (Java 8, LWJGL 2) | ✅ works today | ✅ **Legacy Fabric** (1.3 - 1.13.2) | ✅ Fabric mods, or our own `webmc.mod.json` mods |
| **shim** | 1.13 - 1.20.4 | the same JVM, with our JavaScript LWJGL 3 | ⛔ blocked | ⛔ blocked | planned, not yet |
| **aot** | 1.20.5 - 26.x | a client this project compiles (Java 21/25) | ⛔ no browser JVM | ⛔ | baked in at build time only |

So "choose Fabric" means **Legacy Fabric** on the row that runs today, and modern Fabric the moment
the shim draws a frame. The launcher offers exactly what a version has, and a blocked row says which
wall it is behind instead of showing a black canvas.

## What the plan actually contains

`site/launch.json` is the index the launcher reads: per version, its row, whether it is playable, why
not, and which modes exist with which mods. It carries **no URLs**, so the launcher card can say
"vanilla or Fabric, 2 compatible mods" for 600 versions without downloading 600 launches.

`site/plans/1.12.2.json` (fetched only when somebody presses play) is the launch itself:

```
client      https://piston-data.mojang.com/.../client.jar      from Mojang
libs        libs/1.12.2.json                                   this site, vendored
modes
  vanilla   main net.minecraft.client.main.Main
  fabric    main net.fabricmc.loader.impl.launch.knot.KnotClient
            jars  .../fabric-loader-0.19.5.jar, .../sponge-mixin-0.17.4+mixin.0.8.7.jar, ...
            props fabric.side=client, fabric.modsFolder=/str/mods
mods        hello-webmc (what the folder offers for this version in this mode)
```

Fabric's jars are not guessed: they are Fabric's **own loader profile** from
`meta.fabricmc.net/v2/versions/loader/<game>/<loader>/profile/json` - the same document the official
launcher consumes - so the ASM, Mixin and intermediary versions are Fabric's choice, not ours.
Legacy Fabric's profile comes from `meta.legacyfabric.net`, and its Maven redirects to a host that
does send `access-control-allow-origin: *` (measured; without that the page could not fetch it at all).

## The loader jar

`webmc.loader.WebMcLoader`, compiled to **Java 8 bytecode** so the same jar loads in CheerpJ's Java 8
mode (1.12.2 and older) and its Java 17 mode (up to 1.20.4), built reproducibly so the sha256 in
`site/launch.json` means something.

The page writes `/str/webmc/plan.properties` plus the jars, then starts this class instead of the game.
It reads the plan and either:

* **`mode=vanilla`** - builds a `URLClassLoader` over the mod jars, reads each `webmc.mod.json`, calls
  its entrypoint with a small API (`WebMcApi`: log, a shared string bus, a heartbeat clock), then
  starts the game's own main class. Mods can use the game's classes; they cannot rewrite them - that is
  what Fabric is for, and the loader says so when a jar with a `fabric.mod.json` shows up in vanilla
  mode.
* **`mode=fabric` / `legacyfabric`** - sets the properties Fabric reads
  (`fabric.gameJarPath`, `fabric.addMods`, `fabric.modsFolder`, `fabric.side`, `fabric.gameVersion`)
  and starts `net.fabricmc.loader.impl.launch.knot.KnotClient`. Fabric then does what only Fabric can:
  transforms the game's classes as they load, so mixins work.

Either way it reports every step to the page through a JNI native the page registers
(`Java_webmc_loader_WebMcLoader_nativeReport`) - the same mechanism the LWJGL shim uses for its 216
members. A launch that fails prints the class, the message and the loader-side frame it failed at,
instead of a canvas that stays black.

## The mods folder

```
mods/
  mods.json      what each jar is, which versions and modes it is for   (committed)
  jars/          drop-in jars                                          (git-ignored)
  examples/      source-built mods (tool/built)                        (committed)
  loader-jvm/    the loader itself                                     (committed)
```

`mods/mods.json` describes each jar; `tools/launchplan.py` checks the jar is really there, hashes it,
matches it against a version and mode, and stages it into the deployed site as `site/mods/<name>.jar`.
Compatibility is deliberately simple and tested: `"1.12.2"`, `"1.12.*"`, `"1.8.9 1.12.2"` (a list),
`">=1.14 <1.20.5"` (a window), `"*"`.

Mods are **opt-out**, not opt-in: a version with compatible mods starts with them on, and the
checkbox in the launcher card is how you turn them off. The choice is in the play link
(`play.html?version=1.12.2&mode=fabric&mods=a,b`), so a launch is reproducible and can be pasted.

## Where the real-Java route stands (the LWJGL 3 shim)

The loader above is for versions a browser JVM can run. The range 1.13 - 1.20.4 needs one more thing: a
JavaScript implementation of **LWJGL 3**, because CheerpJ ships LWJGL 2's natives and nothing for 3.
`lwjgl3-webgl/` is that implementation, and its state is:

* 216 native methods implemented, measured against real clients: `tools/lwjgl3_surface.py` reads every
  `org.lwjgl` member out of a client jar's constant pool (458 members across 1.13, 1.16.5 and 1.20.4);
* the boot test gets as far as a real JVM, a window, and a WebGL2 context through our JavaScript, then
  stops at the first shader object - `glCreateShader` returns null, which is a bug in the bridge rather
  than a missing capability;
* it is the only route to 1.13 - 1.20.4 **vanilla** without compiling anything, and the only route at all
  that keeps the official jar unmodified on those versions;
* **Fabric is not fixed by it.** Fabric Loader's boot is reported to hang inside its ASM transformation
  under CheerpJ (leaningtech/cheerpj-meta#231, open, vendor says it is not being worked on), so mods on
  that range have to be applied at build time - the same bake the compiled row uses.

## What is verified here, and what is not

Verified without a browser (`bash run-tests.sh`, 722 checks):

* the plan for every row, from real metadata: a playable version, a shim-blocked one, a Java-25 one;
* Fabric's profile turned into a classpath, including Legacy Fabric's Maven;
* mod matching, hashing, staging, and per-mode attachment;
* **the loader itself, in a real JVM** (`tests/test_loader.py`, run on 2026-10-14 with Temurin 21.0.12):
  a stub game, a stub mod that reads a game class, and a stub Fabric entry class that asserts the
  properties it was handed. The loader compiles to Java 8 bytecode, finds the mod, starts it, passes
  the game its arguments, reports a jar that is not a mod, sets the five properties Fabric reads,
  exits non-zero with a message when the plan is missing, and builds byte-identically twice - so the
  hash pinned in `site/launch.json` means something. The example mod that ships in `mods/examples/`
  was run through it the same way;
* both pages' contracts, including that the classpath order is loader → libraries → game → mods; and
  the boot screen's (the game's own loading screen shape, no network requests, an idempotent edit).

**Not** verified, and this is the honest part: no browser has run this yet. CheerpJ has to load the
jar, our loader has to find the game inside the JVM's filesystem, and Fabric has to remap and mixin
under CheerpJ's class loader. The pieces that could stop it are known and named: the first frame of
the LWJGL 3 shim for 1.13+, and whether Fabric's class loading works inside CheerpJ (its docs claim
custom class loaders and reflection are fully supported, which is why this is worth trying).

Two measured constraints worth writing down:

* `resources.download.minecraft.net` sends **no** `access-control-allow-origin`, so neither the page
  nor Java inside CheerpJ (its HTTP is the browser's fetch, same-origin only) can fetch asset objects.
  The plan installs the asset *index* only; objects have to be bundled or proxied, which is a
  decision for whoever runs a deployment, not for the loader.
* CheerpJ 4.3 (the current release) runs Java 8, 11 and 17. That is why 1.20.5+ is the compiled row:
  no amount of loader work changes the runtime's ceiling.

## Adding a mod

1. Drop the jar into `mods/jars/`.
2. Add it to `mods/mods.json`:

```json
{ "id": "some-mod", "name": "Some Mod", "version": "1.2.3",
  "file": "jars/some-mod-1.2.3.jar",
  "game": ["1.12.2"], "loaders": ["legacyfabric"] }
```

3. Re-run the pipeline (`python3 tools/launchplan.py --write --stage site/mods`) or just push: the
   catalog workflow does it on every deploy.

A Fabric mod needs no entrypoint field - Fabric finds it. A mod of our own adds
`"entrypoint": "com.example.TheMod"` and implements `webmc.loader.WebMcMod`.
