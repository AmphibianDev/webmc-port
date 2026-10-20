# The Fabric-mod question for browser clients

Two different things share the name "Fabric loader for Eaglercraft". Only one can exist.

## 1. A runtime loader — impossible

An Eaglercraft-style client is ahead-of-time compiled (TeaVM → JS/WASM). There is no JVM, no
class loader, no `defineClass`, and no class-load step. Fabric mods are Java **bytecode** in a
`.jar`, and Mixin's whole job is transforming that bytecode **at class-load time inside a JVM**.
With nothing loading classes, there is no point to hook, and nothing that can execute a `.jar`.
Executing bytecode in a tab needs a WASM JVM (CheerpJ) — but then the *game itself* has to run as
bytecode in that JVM, which is a different architecture (not Eaglercraft), and it caps at Java 17
(so ≤ 1.20.4; 1.20.5+ requires Java 21).

## 2. A build-time loader — this is the real one

Implemented. `modded/loader.py` is the loader's front end and runs on every build:

```sh
python3 modded/loader.py --mods client-mods --game-jar client-original.jar --out build/loader
```

It reads each jar's `fabric.mod.json` (id, version, entrypoints, mixin configs), then:

| Step | What it does | Why |
| --- | --- | --- |
| **plan** | every mod's entrypoints, class count, dependencies, mixin configs | the number that decides whether a build is worth starting |
| **prune** | drops mixins whose target class is not in this client and rewrites the config files | a `required: true` config that still lists them **stops the bake**; integration mixins (Sodium, Lithium, Indigo, ViaFabricPlus, Baritone) are exactly this case |
| **generate** | `net.fabricmc.eaglerbridge.EaglerFabricLoader` — one direct entrypoint call per mod | Fabric's startup step, minus the jar scan; no reflection, because the client is AOT compiled |
| **stage** | `build/loader/mods` (pruned jars + `webmc-loader.jar`), `build/loader/overlay-mods` (mod classes), `build/loader/src` (generated loader) | the bake, the overlay and the TeaVM compile each need a different subset |

The call site is planted by `modded/loader-src/loader-mod`: a mixin that injects
`EaglerFabricLoader.ensureInitialized()` into `Minecraft.runTick`. The **real Fabric loader applies
it during the bake**, so the captured bytecode already contains the call, the AOT compiler keeps
the mods reachable, and the browser runs them when the client ticks. `-Dwebmc.bake=1` makes the
loader a no-op during the bake itself, where Fabric is already running the mods for the capture.

Measured on the real jar in `client-mods/` (Meteor Client 26.2-35, 1340 classes):

```
186 of 195 core mixins applied · 22 mixins pruned (Baritone, Indigo, Sodium, Lithium, ViaFabricPlus)
1 entrypoint: meteordevelopment.meteorclient.MeteorClient  · no Fabric API dependency at all
```

The mod's classes are compiled **into** the client with TeaVM, and the parts of Fabric API it
calls are **shimmed** on top of the ported client. The mod's Mixins are applied **offline**, at
build time, against the ported source tree. One build per (version × mod set), ~30–42 min.

Pipeline: resolve `fabric.mod.json` + dependencies → prune the mixins whose target is not in this
client → shim the Fabric API modules the set uses → inject the entrypoint call (a mixin, applied by
the real Fabric loader during the bake) → TeaVM compile, link, package → per-mod report.

## The scanner

`portscan.py` tells you what a mod would need *before* anyone spends 40 minutes rebuilding.

```sh
python3 modded/portscan.py mods/somemod.jar
python3 modded/portscan.py https://github.com/owner/repo/releases/download/v1/mod.jar
python3 modded/portscan.py --json mods/*.jar
```

It reads the jar's bytecode constants (no Java needed) and reports: class count and size, whether
class names are official (`no remap needed`) or intermediary (`needs remap`), how many Mixin
injections must be applied offline, which Fabric API modules need shims, which other mods it
depends on, and a verdict:

| Verdict | Meaning |
| --- | --- |
| ✅ portable | no game hooks to reimplement — compile and go |
| 🟡 portable, needs shims | small number of injections + Fabric API modules to implement |
| 🟠 hard | 25+ injections, i.e. it rewrites hot game code (renderer/perf mods) |
| ⛔ impossible | native libraries, or ASM rewriting bytecode at runtime |

### Measured on real mods (2026-10-11)

| Mod | Classes | Size | Names | Injections | Verdict |
| --- | --- | --- | --- | --- | --- |
| ModMenu 20.0.3 (26.x) | 71 | 344 KB | official — no remap | 3 | 🟡 shims: lifecycle, screen, keymapping |
| Zoomify (26.4) | 88 | 246 KB | official — no remap | 2 | 🟡 also needs Kotlin adapter + YACL |
| EMI (1.21.1) | 454 | 1.9 MB | intermediary — remap needed | 16 | ⛔ ASM at runtime |

Two things fall out of that: Minecraft 26.x is **unobfuscated**, so modern mods need no remapping;
and the popular client-side mods hook the game through a handful of shared Fabric API modules —
so the shim layer is written once and then every ported mod reuses it.

## The 26.x port already has a runtime mod door

The ported 26.2 tree ships `net/lax1dude/eaglercraft/v1_8/mods/resources/`, and the whole
system reads from one page-side object:

```js
globalThis.EaglerModResourceRegistry = {
  version: 1, validated: true,
  snapshotPacks()            -> { resources, mountRequests, languageRequests,
                                  modelCapability, modelRequests },
  readResource(packHandle, resourceHandle) -> ArrayBuffer,
  publishResolvedLanguage(moduleId, generation, json),
  publishResourceReload(...), publishResourceModel(...),
  clearResourceModel(...), modelPublicationAllowed(...),
  performanceTelemetry,
};
```

So the launcher can hand a running 26.x client **mod resource packs at runtime** (textures, models,
languages, declared virtual blockstates) with no rebuild — the same shape as `?mod=` for the
EaglerForge 1.12.2 build, but for assets and content instead of code. Code mods still come from the
build.

### Code hook sites in the 26.2 tree (for the Fabric shims)

| Fabric API member | Hook site in `game/src/main/java` |
| --- | --- |
| `ClientTickEvents.END_CLIENT_TICK` | `net/minecraft/client/Minecraft.java:1259` `runTick(boolean)` (end of method) |
| `ScreenEvents.AFTER_INIT` | `net/minecraft/client/gui/screens/Screen.java:362` `init()` (reached via `init(width,height)` at :323) |
| `Screens.getWidgets(Screen)` | `Screen.java:67` `renderables` |
| `KeyMappingHelper.registerKeyMapping` | `net/minecraft/client/KeyMapping.java:21` `ALL` map (`eaglerUnregister` at :212) |
| `ClientCommandRegistrationCallback.EVENT` | `net/minecraft/client/multiplayer/ClientPacketListener.java:1776` (commands rebuilt) |
| `ClientCommands.literal` | wrapper over `LiteralArgumentBuilder.literal(name)` |
| `Event.register` | our own bus, no game hook |
