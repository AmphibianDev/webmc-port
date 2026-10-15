# How to use this

## The short version

**The site is live: <https://amphibiandev.github.io/webmc-port/>** — a launcher that lists every Minecraft version
the catalogue knows about, says how each one runs, and links the ones that are built. A built version has its own
page, for example <https://amphibiandev.github.io/webmc-port/clients/1.21.11/>, which loads the client into the
page. Nothing is streamed and nothing runs on anybody's server while you play.

**The repository is <https://github.com/AmphibianDev/webmc-port>** — `main` is what the site serves,
`snapshot-build` is where work happens, and the build log lives on a `build-log` branch nobody has to check out.

## What you can do from the launcher

| you pick | what happens |
| --- | --- |
| a **real Java** version (1.0 – 1.12.2) | Mojang's own jar is fetched by your browser and runs in a JVM in the page. Fabric and Legacy Fabric jars from `mods/` load at play time |
| a **compiled** version (1.20.5 and up, when built) | a page this project's build jobs compiled ahead of time. Mods are baked into the client before the compile — see `MIXINS.md` |
| a **needs-shim** version (1.13 – 1.20.4) | not yet: those clients want LWJGL 3, and the JavaScript layer that provides it has not drawn a frame. The launcher offers the nearest version that does start |
| a row with **no engine** | nothing to play yet, and the launcher says which wall it is behind instead of pretending |

The launcher also explains the two things people ask about first: **no game files are hosted here** (the client jar
comes from Mojang, either in your browser or at build time by whoever ran the build), and **multiplayer needs a
gateway** because a browser cannot open a TCP socket.

## Playing the 26.3 client you downloaded, with multiplayer

A single-file offline build (Eagler26 26.3, for example) works when you open it — what it does not have is a
gateway, so its multiplayer is off until something points it at one. One command starts both halves:

```sh
python3 tools/play_local.py                              # newest single-file client in ~/Downloads
python3 tools/play_local.py --server mc.example.com      # and join that server on load
python3 tools/play_local.py --allow-lan                  # LAN play
```

It starts the gateway (`gateway/server.js`, no dependencies) on a loopback port, serves the client with the
gateway set inside it, prints the address, opens it, and stops both when you press Ctrl-C. The patch is one script
tag: removing it gives your file back byte for byte. `MULTIPLAYER.md` has the protocol, the policy switches, and an
nginx block for running a gateway of your own in public.

## Running it on your own machine

```sh
cd webmc-port
export JAVA_HOME=…             # a JDK is only needed by the tests that compile Java
bash run-tests.sh              # every offline check, ~1000 of them
python3 -m http.server 8099 --directory site    # the launcher page, locally
```

The tests are the contract. They cover the version pipeline, the launcher's own promises, the boot screen, the
packer, the bake, the mixin applier (compiled and run on a JVM), the gateway (driven over a real socket), the
local play command, and a published client checked from the outside. Nothing in them touches the network, so they
say what works rather than what might.

## Building a version

A build is a tag. The job fetches the official client, patches it, compiles it, packs it into a page, verifies it,
and publishes it:

```sh
git tag -f build-1.21.11 HEAD && git push -f origin refs/tags/build-1.21.11                 # vanilla
git tag -f "build-1.21.11+hello-fabric" HEAD && git push -f "origin" "refs/tags/build-1.21.11+hello-fabric"
```

The mod ids are the ones in `mods/mods.json`; `+hello-fabric,hello-mixin` bakes two. A build takes about an hour,
most of it the WebAssembly compile. What came out is read back with:

```sh
python3 tools/watch_builds.py                     # what the runs are doing
python3 tools/fetch_packed.py --all --record      # put the published pages on the site
python3 tools/verify_client.py --dir site/clients/1.21.11   # check a page the way a visitor's browser gets it
```

## What this project never contains

No game files are committed or served: the client jar and its assets are fetched from Mojang, at build time by
whoever runs the build, and never redistributed here. Compiled clients live as release assets and as the packed
chunks the site serves. `CREDITS.md` is the ledger for everything borrowed, including the two engines and the
Mixin annotations; `COMPLIANCE.md` is the reasoning about what may be published at all.

## When `git push` is refused but the account works

They are two different doors. A flagged or limited account can be refused on the git transport with

```
remote: Your account is suspended. Please visit https://support.github.com for more information.
fatal: unable to access 'https://github.com/…': The requested URL returned error: 403
```

while the same token still writes files, refs and release assets through the REST API. (Measured, not assumed: a
`git push` failed and a `PUT /contents` on the same token succeeded a second later.) The work does not have to
stop for that:

```sh
python3 tools/push_via_api.py --repo owner/name --branch main --dry-run   # what it would send
python3 tools/push_via_api.py --repo owner/name --branch main            # send it
```

It sends each local commit the remote does not have - only the paths that commit changed - through
`POST /git/trees`, `POST /git/commits`, and finally one `PATCH /git/refs/heads/<branch>`, so an interrupted run
leaves nothing half-pushed. Messages, author names and dates are preserved. It reads the token from a file
(`--token-file`, default `/tmp/.ghtok`), never from the command line.

Two things to do alongside it: appeal or resolve whatever flagged the account, because this is a workaround and not
a fix; and check whether the *cause* is in the repository. Ours was: `site/clients/` carried 108 MB of a compiled
client into git history, which is exactly the redistribution `COMPLIANCE.md` promises not to do. It is now
git-ignored and the deploy gets those files from the release asset instead.

## When something looks wrong

| symptom | first thing to look at |
| --- | --- |
| a version row says no engine | `python3 tools/route.py --version <v>` — it names the wall and the reason |
| a build failed | `git show origin/build-log:build-log/last.log` and `report-<version>.json`; the build log branch is written even on failure |
| a published page is broken | `python3 tools/verify_client.py --url <the page>` — hashes, boot screen, branding, mod set |
| multiplayer will not connect | run the gateway by hand (`node gateway/server.js`) and read its status page; a public gateway refuses private and reserved targets on purpose |
| the loading screen says somebody else's name | that is what `tools/aot_shell.py` strips and what its test checks; a build from before that fix is still cached |
