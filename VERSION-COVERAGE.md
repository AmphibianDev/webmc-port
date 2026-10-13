# What runs real Java, what runs at all, and what each version needs

Answering the questions directly: which versions are real Java, whether snapshots are covered, what Fabric
needs, and where our own mod loader fits. "Real Java" means Mojang's own client jar executed by a Java
runtime in the browser - not a reimplementation, not a port of a port.

## The rule that decides everything

A version needs three things: a **Java version the browser runtime can run**, a **graphics layer**, and
**its libraries**. CheerpJ (the JVM in the browser we use) runs Java 8, 11 and 17, and ships the LWJGL 2
natives. That single paragraph divides Minecraft in two:

| versions | Java needed | graphics | route |
|---|---|---|---|
| **1.0 - 1.12.2** (releases and snapshots) | 8 | LWJGL 2 - already provided | **real Java, working today** |
| **1.13 - 1.16.4** | 8 | LWJGL 3 - ours | real Java through the shim |
| **1.16.5 - 1.20.4** | 8 and 17 | LWJGL 3 - ours | real Java through the shim |
| **1.20.5 - 26.x** | **21 and 25** | - | impossible in a browser JVM: compiled ahead of time instead |

So: **1.16, 1.18, 1.19, 1.20 and their snapshots are reachable as real Java**, and they all depend on the
same one thing - the LWJGL 3 shim in `lwjgl3-webgl/`, which is what is being built. 1.21 and 26.2 are not
reachable that way at all: Java 21 is past the browser JVM's ceiling, and 26.2 already works as a client we
compile ourselves (the release asset in the launcher).

## Snapshots, including April Fools

Snapshots are versions like any other, and the classification is by date rather than by name
(`tools/jvm_route.py`): everything up to 1.12.2 is real Java today - **221 snapshots are playable right
now** - and snapshots after that follow their release's row in the table above.

April Fools snapshots are ordinary snapshots to Mojang's metadata (20w14infinite, 22w13oneBlockAtATime,
23w13a_or_b, 24w14potato, 25w14craftmine). The rules by date apply to them exactly as to any other
snapshot: those up to 1.20.4 come through the shim, the ones after it need a compiled build. They are also
the ones most likely to contain a joke class doing something a shim has never seen, so they are a good
stress test rather than a first target.

## Fabric, and our own loader

Here is the part worth being precise about, because it is the reason real Java matters at all:

* **Under a real JVM (1.13 - 1.20.4, once the shim draws a frame): Fabric can run as it does on the
  desktop.** Our shim *is* the classpath: CheerpJ loads classes normally, so Fabric Loader and its
  libraries can be placed before the game and the game will find them. Fabric uses ASM to transform
  classes at load time, and a real JVM supports that - which is exactly what an ahead-of-time compiled
  client cannot do.
* **Our own mod loader sits in front of that.** `mods/injector/` already patches a built client with a
  page-tier API (overlay, keybinds, tick/render, chat); `mods/build_modded_workspace.py` plans and bakes
  Fabric mods into a compiled build. With a real JVM the third option opens up: a loader that puts our own
  mods and Fabric's on the classpath together, which is the "our own loader, with Fabric support" shape.
* **Under a compiled client (1.20.5 - 26.x) mods must be baked in at build time.** There is no class
  loader at play time, so "install a jar" cannot work there, by construction.

## So the answer to "does it support snapshots"

Yes, in the same shape as releases: 1.0 - 1.12.2 snapshots are playable now as real Java; 1.13 - 1.20.4
snapshots (April Fools included) arrive with the shim; 1.20.5+ snapshots need a compiled build per version,
which is the slow, per-version path.

## Where it actually stands this minute

The shim boots a JVM, loads our class, calls all 216 natives, creates a window and a **WebGL2 context**, and
then stops: WebGL refuses the first shader object. The page and CI now print the context's own report -
version, renderer, whether it is already lost, and the canvas size - plus, for any refused object, the
argument and the GL error. That was the last unknown in this round, and the next run names it.
