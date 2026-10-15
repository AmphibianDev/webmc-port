# Credits, licences, and what we changed in borrowed work

This project builds on other people's work. This file is the ledger: what each borrowed piece is, its
licence, what we use it for, and what we changed. `ports.json` carries the same licence on each engine,
and a test keeps the two in step.

Minecraft itself is Mojang's. **This is not an official Minecraft product. It is not approved by or
associated with Mojang or Microsoft.** The client is fetched from Mojang's own servers by the visitor's
browser (or, for a compiled build, at build time by the person building) and is never redistributed here.

## Engines we build clients with

| Project | Licence | Versions | What we use | What we changed |
| --- | --- | --- | --- | --- |
| [TypeThe0ry/Gaius](https://github.com/TypeThe0ry/Gaius) | **MIT** | 1.21.11, 26.2, 26.3 | the whole compile pipeline: it fetches the official client, patches it for the browser and compiles it | its launcher/loading page is replaced by our own boot screen (`site/boot/shell.html`); no other change |
| [CynTheSolveroftheabsolutefabric/Eaglercraft-26.2-Workspace](https://github.com/CynTheSolveroftheabsolutefabric/Eaglercraft-26.2-Workspace) | **no licence file** | 26.2 | the same, for the mod-baking path | launcher page replaced by ours; engine currently **disabled** in `ports.json` pending a licence |
| [Eagler-Versions/eaglercraft-1.14-workspace](https://github.com/Eagler-Versions/eaglercraft-1.14-workspace) | **no licence file** | 1.13 – 1.14.4 | the compile pipeline for that era | launcher page replaced by ours; branding stripped by `tools/aot_shell.py --strip-brand` |
| [Eaglercraft-Archive/EaglercraftX-1_8-workspace](https://github.com/Eaglercraft-Archive/EaglercraftX-1_8-workspace) | **no licence file** | 1.8 – 1.8.9 | the compile pipeline for that era | as above |
| [Eagler-Versions/eaglercraft-1.7.10-workspace](https://github.com/Eagler-Versions/eaglercraft-1.7.10-workspace) | **no licence file** | 1.7.10 | the compile pipeline for that era | as above |
| [NT9712/eaglercraft-262-workspace](https://github.com/NT9712/eaglercraft-262-workspace) | Apache-2.0 (a fork; does not relicense upstream) | 26.2 | not used | — |

**About the "no licence file" rows.** A repository with no licence file is *all rights reserved* by
default: crediting the author is the right thing to do but it is not the same as permission. Those engines
are used because the project owner decided to accept that risk knowingly, and each one is a candidate to
be replaced by our own engine. If you are the author of one of them and want it removed or licensed, that
is a one-line change in `ports.json`.

| [Cucuzacu/WebForge](https://github.com/Cucuzacu/WebForge) | **no licence file** | 1.6.4 with Forge | linked where it lives, nothing rebuilt from it |
| [EaglerPorts](https://github.com/EaglerPorts) | **no licence file** | several classics | linked where they live, nothing copied |

## Clients we host

| Project | What it is | What we do with it | What we changed |
| --- | --- | --- | --- |
| **Eagler26** (`Eagler26-26.3-Full.html`, built by the Eagler26 project, distributed by the client's own site) | a single-file offline build of Minecraft **26.3** (Eagler V0.99, build V0.99-3a9034f1): the whole client compiled to WebAssembly, its assets, sound and music archive, an integrated server for singleplayer, and a `goldproxy.v1` multiplayer transport | a visitor can load it, play singleplayer, and join a normal Java server through a gateway | none inside the client: we do not repackage it. Our site sets the gateway it should use and the address to join, through the client's own supported switches (`?gateway=`, `?server=`, `?port=`, or `localStorage["eagler26.goldproxy"]'), and our page is the loading screen around it |

The Eagler26 build's own metadata names its payload (`eagler26-offline-meta`): `minecraftVersion` 26.3,
`eaglerVersion` V0.99, singleplayer included, sound/music/records at original quality, wasm sha256
`3a9034f1...`, and a sha256 for every resource. `tools/eagler_offline.py` reads exactly that metadata and can
prove any extraction against it; the file that was checked is the one downloaded to the project owner's
machine, not a copy fetched by this repository.

## Mixin (the annotations, not the tool)

| Project | Licence | What we use | What we changed |
| --- | --- | --- | --- |
| [SpongePowered/Mixin](https://github.com/SpongePowered/Mixin) | **MIT** | the names and meanings of `@Mixin`, `@Inject`, `@Overwrite`, `@Shadow`, `@Unique`, `@At`, `CallbackInfo` and `CallbackInfoReturnable` | we ship compile-time **stubs** of those annotations (`mods/mixin-shims`) and our own build-time applier (`mods/mixin-applier`) that reads the same annotations out of compiled classes. Neither is Mixin: `MIXINS.md` lists exactly which shapes are applied and which are refused by name |

## The gateway

A browser cannot open a TCP socket, so joining a Java server from a page needs a server-side gateway. Ours
lives in `gateway/server.js`, speaks the `goldproxy.v1` protocol the Eagler26 client already implements, and
is ours (no borrowed code). The protocol itself - the token endpoint, the `open`/`ready` handshake, the
control and binary frames, the close codes - is documented in `MULTIPLAYER.md` from the client's own modules.

## Runtimes and toolchains

| Project | Licence | What we use |
| --- | --- | --- |
| [Leaning Technologies **CheerpJ**](https://cheerpj.com) | proprietary, free for personal and open-source use | the JVM in the page for the real-Java row (1.0 – 1.12.2 today, 1.13 – 1.20.4 with our shim) |
| [leaningtech/**cheerpj-natives**](https://github.com/leaningtech/cheerpj-natives) | Apache-2.0 | the LWJGL 2 platform layer CheerpJ's real-Java row runs on |
| [leaningtech/**browsercraft**](https://github.com/leaningtech/browsercraft) | Apache-2.0 | the reference implementation for the real-Java row (unmodified client in CheerpJ) |
| [konsoletyper/**TeaVM**](https://github.com/konsoletyper/teavm) | Apache-2.0 | the compiler our own engine will be built on |
| [wangyenshu/**recipes**](https://github.com/wangyenshu/recipes) (`recipes_emscripten/openjdk21`) | BSD-3-Clause | the OpenJDK-to-wasm recipe for the planned Java-runtime row |
| [wangyenshu/**openjdk21-kit**](https://github.com/wangyenshu/openjdk21-kit) | GPL-2.0 | its launcher, link script and page shell. **GPL** means a distributed client built on it has to be GPL-compatible too — a licensing decision for that row, not a technical one |
| [emscripten-core/**emscripten**](https://github.com/emscripten-core/emscripten) | MIT (with the University of Illinois notice) | the C-to-wasm toolchain, GLFW 3, GLES-over-WebGL and OpenAL for any native layer we write |
| [lax1dude/**eaglercraftx**](https://github.com/lax1dude/eaglercraftx) | no licence file | not built from; the lineage the workspaces above descend from |
| [TypeThe0ry/Gaius](https://github.com/TypeThe0ry/Gaius) contributors | MIT | credited above; the engine the 26.x row builds from |
| [Eagler-Versions](https://github.com/Eagler-Versions) and [Eaglercraft-Archive](https://github.com/Eaglercraft-Archive) maintainers | no licence file | the 1.7.10, 1.13–1.14 and 1.8 workspaces |

## What we changed in the clients we publish

Every built client is the official Mojang client, patched for the browser by the engine named above, and
then:

1. **the engine's launcher and loading page is removed** and replaced by `site/boot/shell.html`, which
   reproduces the loading screen the game itself draws (black background, the game's 200×2 progress bar,
   its GUI scale);
2. **branding that is not ours is stripped from the page** by `tools/aot_shell.py --strip-brand`: the
   engine's titles, headings and wordmarks go, and what remains is credited here rather than displayed;
3. **nothing of ours is added to the game** — no watermarks, no menus beyond the game's own.

Branding inside the compiled JavaScript or wasm (identifiers, comments) is not rewritten: doing that
blindly would break the client. Attribution for that code lives in this file.

## Mods

Mods are the mod author's work under their own licence, and are fetched from their own distribution
points. `mods/jars/` is git-ignored for exactly that reason: only a jar you are allowed to redistribute
should be placed there, and it is the person deploying who does the redeploying.
