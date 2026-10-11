# webmc-port — Minecraft versions, compiled for the browser

A pipeline that takes a Minecraft version and turns it into something a browser can run, plus a
launcher page that looks like the Minecraft launcher and a daily job that notices new releases
and snapshots.

It is not a reimplementation and it is not a stream. The game runs **in the visitor's tab**, which
is why there is no input lag: nothing travels, nothing is encoded, nothing is rented.

## The idea in one table

| Stage | Tool | What it decides |
| --- | --- | --- |
| Discovery | `tools/versions.py` | what exists (release, snapshot, old beta/alpha) and what it needs |
| Verdict | `tools/plan.py` + `ports.json` | can it be **built**, is there a **prebuilt** client, or does it **need a port** |
| Acquisition | `tools/acquire.py` | fetch the official client jar + asset index into `work/` (checksums verified) |
| Build | the engine named in `ports.json` | compile the client for the web (Gaius: official bytecode → WebAssembly) |
| Catalog | `tools/update.py` | record what is new, queue what has no port, write the page's manifest |
| Site | `site/` | the launcher page: Play, versions, what porting takes, how it works |

## Try it locally, no Mojang access needed

```sh
cd webmc-port
python3 tests/test_pipeline.py                                   # 31 checks, offline, on a fixture
python3 tools/plan.py --manifest fixtures/manifest.json --kinds release,snapshot,old_beta
python3 tools/update.py --manifest fixtures/manifest.json --state work/state.json --write
node tests/test_site.mjs                                         # the launcher page's contract
python3 -m http.server 8099 --directory site                     # open the page
```

Against the real version list (this is the one thing that talks to Mojang):

```sh
python3 tools/plan.py --limit 12                                 # what would happen to each version
python3 tools/update.py --write                                  # refresh the catalog and the page
python3 tools/acquire.py --version 26.2 --dry-run                # what a build would fetch
```

## Engines

An engine compiles — or *runs* — a client for the web. This is the list `ports.json` carries:

| Engine | Versions | How it works | Textures | Mods |
| --- | --- | --- | --- | --- |
| **cheerpj-unmodified** (CheerpJ + cheerpj-natives) | 1.0 → **1.12.2** (the LWJGL 2 era) | **nothing is compiled and nothing is copied**: the visitor's browser fetches the official jar from `piston-data.mojang.com`, CheerpJ runs it with the JS natives on `java.library.path`, main class `net.minecraft.client.Minecraft` | **Mojang's, untouched** | ✅ real `.jar`s at runtime — it is a real JVM |
| **browsercraft** (the reference implementation) | 1.2.5 | the CheerpJ team's own demo, Apache-2.0, Svelte UI; the pattern everything above copies | Mojang's, untouched | runtime |
| **eagler-workspace-26.2** | 26.2 | Gradle + `wasm-toolchain/` node scripts compile the patched official source into one HTML | official resource overlay | ✅ compiled in via source hooks |
| **eaglercraftx** | 1.5.2, 1.8.8, 1.12.2 | TeaVM builds, the tuned-performance route on the web | official assets | JS mod API |
| **eagler-ports** | Indev → 1.2.5 | release assets are the clients | official assets | — |
| **webforge** | 1.6.4 + Forge | CheerpJ again, with Forge's launcher in the JVM | Mojang's, untouched | ✅ real Forge jars |
| ~~gaius~~ (disabled) | — | kept as the headless record | — | — |

### Why the first row is the interesting one

It is the only route where **the game is never copied by anyone but Mojang**:

```js
const urlDownloadMinecraft = 'https://piston-data.mojang.com/v1/objects/…/client.jar';
await cheerpjInit({ javaProperties: ['java.library.path=/app/lwjgl-natives/'] });
await cheerpjRunMain('net.minecraft.client.Minecraft', classpath);
```

* the jar is fetched **in the visitor's browser**, from Mojang — this repository and the site
  serve no Minecraft file at all (`site/lwjgl` and `site/lwjgl-natives` are LWJGL (BSD) and
  cheerpj-natives (Apache-2.0), vendored at build time);
* the client is **unmodified**, so textures, UI and behaviour are exactly Mojang's;
* it is a **real JVM**, so mods are real `.jar` files, loaded at runtime with no rebuild.

Coverage is bounded by the LWJGL implementation: LWJGL **2** covers everything up to **1.12.2**;
1.13+ uses LWJGL 3, whose JS native layer does not exist yet, and Java 21 puts 1.20.5+ out of any
JVM-in-page route for now. `cheerpj-natives` describes its LWJGL support as partial, so the two
versions with public proof (1.2.5 via browsercraft, 1.6.4+Forge via WebForge) are marked
`proven`, and everything else on that row carries that caveat onto its card.

### Adding an engine

```json
{
  "id": "my-engine",
  "repo": "owner/project",
  "kind": "eagler-workspace",
  "enabled": true,
  "profiles": ["1.21.4"],
  "requires": {"java_compile": 21, "heap_gb": 10, "ram_gb": 16, "minutes": 40},
  "mods": "yes - source hooks",
  "build": {
    "clone": "git clone --depth 1 https://github.com/{repo} engine",
    "compile": "cd engine && ./gradlew :game:compileJava --console=plain --no-daemon",
    "package": "cd engine && node wasm-toolchain/build-single-html.js --output dist/{version}.html",
    "output": "engine/dist/{version}.html"
  }
}
```

`{version}` and `{repo}` are substituted. `profiles` makes a version buildable; `versions`
(a version → repo-slug map) makes a prebuilt client appear as a link; `enabled: false` documents
an engine without ever using it.

## The honest bottleneck

A browser client is the real client with the parts a browser does not have replaced: windowing,
OpenGL, audio, networking, the filesystem. That replacement layer is **per-version work** — it is
why this catalog marks most versions `needs-port` instead of pretending they build. Everything
around that work is done:

* adding an engine = adding an entry to `ports.json` (repo, profiles, build commands, output path)
* adding a version to an existing engine = a version profile in that engine
* the pipeline, the catalog, the updater, the page and the tests need no changes at all

So a new release or snapshot shows up in the catalog within a day as `needs-port` with the reason,
and becomes playable the moment an engine gains a profile for it.

## What this repository never contains

No Minecraft jar, no compiled client, no assets, no libraries — `work/` and `dist/` are ignored, and
acquisition happens at build time on the machine that is building. Three consequences, stated
plainly because they are your call:

* `CLIENT_HOSTING=release` (default) puts a **built client** in this repository's releases and the
  page links it. Convenient, and it is a compiled copy of the game sitting in your account.
* `CLIENT_HOSTING=artifact` keeps the built client inside the workflow run: the page then keeps
  linking the project that builds that version, and nothing of the game is published by you.
* Prebuilt clients from other projects are always **linked, never copied** (`tools/plan.py`
  builds every one of those URLs).

That is also why the catalog path (the daily job) is safe by construction: it fetches one JSON
version list, writes two small files, and deploys a page.

## Layout

```
ports.json              which engine can build which version, and how
tools/versions.py       version list parsing, classification, sorting
tools/plan.py           verdicts: build / reuse / needs-port
tools/acquire.py        client jar + asset index, checksums, work/ only
tools/update.py         the updater: what is new, what to queue, the page's manifest
tools/targets.py        verdicts for the versions this project is meant to cover
tools/lwjgl_surface.py  measures the LWJGL 3 gap (the one layer between us and 1.13-1.20.4)
mods/                   the Fabric side: a build-time loader for ahead-of-time clients, the
                        shim layer, the mod scanner, and a README on what "injector" means here
site/                   the launcher page (Minecraft-launcher styled)
state.json              the catalog's memory: every version seen, and what it needs
targets.json            the versions this project is supposed to cover
tests/                  offline tests: pipeline (31 checks) and the page's contract (14)
fixtures/               a manifest to test against, so no test touches the network
.github/workflows/      daily catalog + Pages, and a manual build job
```

## Adding an engine

```json
{
  "id": "my-engine",
  "repo": "owner/project",
  "kind": "real-client",
  "profiles": ["1.21.4"],
  "java": 21,
  "ram_gb": 16,
  "build": {
    "clone": "git clone --depth 1 https://github.com/{repo} engine",
    "acquire": "bash port/scripts/fetch-version.sh",
    "remap": "bash port/scripts/remap-client.sh",
    "compile": "bash port/scripts/build-version-release.sh {version}",
    "output": "port/web/dist/{version}/Gaius.html"
  }
}
```

`{version}` and `{repo}` are substituted. `kind` is documentation for the page; `profiles` is what
makes a version buildable, and `versions` (a map of version → repo slug) is what makes a prebuilt
client show up as a link.

## Notes on the two older paths

* **EaglerForge-style JS mods** need an injector for a *specific* client build, which is why the
  latest one is for 1.12.2. A compile route does not need one: mods are baked at build time — that
  loader already exists in the sibling project (`../webmc-browser/modded/loader.py`), and it prunes
  the mixins a given client cannot support instead of failing the build.
* **Streaming** (a remote desktop in a tab) is a different product: it needs a machine of its own,
  never a CI runner. `../webmc/host-here.sh` starts it on hardware you own.
