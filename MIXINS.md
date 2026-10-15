# Mods and mixins

Two questions come up together: *do mods load at all*, and *what about Fabric*. They have different answers, so
they are answered separately here.

## What a browser client can do with a mod

Fabric Loader is a **runtime** loader: it runs before the game, opens jars, transforms classes as they load, and
calls `onInitializeClient` on each mod. An ahead-of-time browser client has no such moment. The client was
compiled from Java to WebAssembly at build time, and there is no class loader at play time to interrupt.

That is why this project puts mods into the client **at build time** instead:

| what a mod needs | how it happens here | state |
| --- | --- | --- |
| an entrypoint (`ClientModInitializer`) | the bake instantiates the class and calls `onInitializeClient()` from the client's own static initialiser, as bytecode | **works**, proved on a JVM (`tests/test_bake_mods.py`) |
| mixins | `mods/mixin-applier` merges the mixin's code into the client's class files before the client is compiled | **works for the shapes below**, proved on a JVM (`tests/test_mixin_applier.py`) |
| Fabric API (`net.fabricmc.fabric.*`) | a mod that calls it needs those classes; the API is not shipped yet | **not yet** - a mod that only uses Minecraft itself is fine |
| anything a mod does at play time through reflection | an AOT compiler cannot see a class named only in a string | **not yet** - which is one more reason the mixin merge is done on the class files |

So "does it support Fabric" has a precise answer: **a Fabric mod that is written as an entrypoint plus mixins is
baked in and runs; a mod that expects Fabric Loader and Fabric API at play time does not.** Meteor Client is the
second kind: its descriptor declares six mixin configs, an access widener, and its code calls Fabric API
throughout, so it cannot load here yet, and saying otherwise would be a lie with a download button.

## What the mixin applier applies

Read this as the contract. Every line is either applied, or reported by name in the build log - never quietly
skipped, because a mod that half works is worse than a mod that is not there.

**Applied**

- `@Mixin(Target.class)` and `@Mixin(targets = "a.b.C")`, with the config's `package` prefix.
- `@Overwrite` - the target method's body becomes the mixin's. The method must exist with the same descriptor; if
  it does not, the mixin is **refused** (not attempted), because half an overwrite is a broken client.
- `@Inject(method = "x", at = @At("HEAD" | "RETURN" | "TAIL"))` for handlers whose parameters are
  `(CallbackInfo)` or `(CallbackInfoReturnable)`. `RETURN` and `TAIL` both mean "before an exit": every exit gets
  the call, because a method with several exits has several places that can mean.
- `@Shadow` - never copied. After the merge the mixin's code *is* the target's code, so a shadowed call resolves
  to the target's own member. That is the only reason a shadow exists.
- `@Unique` and any other member the mixin declares - copied with it, and references it makes to its own class
  are redirected to the target, because that is where the code now lives.

**Not applied, and named in the build log**

- A handler that also takes the target's own arguments. Passing them correctly needs Mixin's local-variable
  analysis; guessing is a wrong argument at play time, which is far worse than a missing injection.
- `@At` selectors other than `HEAD`, `RETURN` and `TAIL` (`INVOKE`, `FIELD`, `NEW`, `CONSTANT`, ...).
- `@Redirect`, `@ModifyArg`, `@ModifyVariable`, `@Accessor`, `@Invoker`, `@Mixin`'s config options such as
  `injectors.defaultRequire`, and access wideners.

**Refused, and a failed build**

- A mixin whose target class is not in the client (a server-side class, or a different version).
- A mixin named in a config but missing from the jar, or a config named in the descriptor but missing from the
  jar.
- An `@Overwrite` of a method the client does not have with that descriptor.
- An `@Inject` into a method the client does not have.

A refusal stops the build on purpose. The alternative - a client that starts, looks right, and misbehaves the
moment that code runs - is the kind of bug that takes a week to find.

## Crediting Mixin

The annotations - `@Mixin`, `@Inject`, `@Overwrite`, `@Shadow`, `@At`, `CallbackInfo` - and the semantics they
describe are [SpongePowered/Mixin](https://github.com/SpongePowered/Mixin) (MIT). This project ships *stubs* of
those annotations in `mods/mixin-shims` so a mod can be compiled without Sponge's toolchain, and reads the same
annotations out of the compiled classes. It is not Mixin, and it says so: the list above is deliberately much
smaller than what Mixin does. `CallbackInfo` and `CallbackInfoReturnable` are compiled into the client, because
injected code constructs them; the annotation stubs are `CLASS` retention and never travel.

## Adding a mixin mod to a build

```bash
python3 tools/build_mixin_shims.py            # the callback classes, once
python3 tools/build_mods.py                   # the example mods
python3 tools/bake_mods.py --engine ../engine --version 1.21.11 \
    --mods mods/jars/hello-fabric.jar --mods mods/jars/webmc-mixin-shims.jar --report /tmp/bake.json
```

Then build with the mod set: push a tag `build-1.21.11+hello-fabric`. The release notes and the page the client
is served from both name the mod set that went in, and the loading screen says so before the game opens.
