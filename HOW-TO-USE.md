# How to use this, and where the URL comes from

## Nothing is on GitHub, and why

The repository that used to host this (`AnAmphibianWhoLovesWeb`) is suspended, and everything here
lives in **local git commits** in `~/devspace/WebMC/webmc-port` plus bundle backups in
`~/devspace/WebMC/backups/`. Publishing it again needs an account and a token, and one warning
worth repeating once: the *code* is publishable anywhere, the **917 MB of Minecraft clients in
`work/builds/` are not** — that is the redistribution that ended the last account. The scripts in
`~/devspace/WebMC/` are ready for a move (`move-to-gitlab.sh`, `move-to-04DEREK28.sh`); both are
code-only pushes and will refuse if anything game-sized is in the history.

## Use it right now: a URL on your own machine

```sh
cd ~/devspace/WebMC/webmc-port
python3 tools/fetch_builds.py                  # done already: 29 builds, 917 MB, from GitHub
python3 mods/injector/inject_mod_api.py --all  # done already: the mod API is in all of them
python3 tools/serve_builds.py                  # -> http://127.0.0.1:8099/
```

Open **http://127.0.0.1:8099/** and press Play on any version. That page is generated from whatever
is in `work/builds-modded/`, so it lists exactly what you have (29 builds, from Indev to 26.3).

Add `?mod=` to any Play link to load a JavaScript mod:

```
http://127.0.0.1:8099/1.12.2/Eaglercraft%201.12.2%20(WASM-GC).modded.html?mod=http://127.0.0.1:8099/mods/my-mod.js
```

Put your `.js` files in `work/builds-modded/mods/` and they are served from there. A mod:

```js
if (globalThis.WebMCModAPI) {
  WebMCModAPI.on("load", () => WebMCModAPI.displayToChat("my-mod loaded"));
  WebMCModAPI.registerKeybind("Toggle overlay", "h", () => toggleMyHud());
}
```

What mods can do at that tier: draw on an overlay canvas over the game, read keys, register keybinds,
send chat and drive input, run on tick/render, log. What they cannot do: reach into the game's own
classes — the builds are minified or WebAssembly, so nothing in them names `net.minecraft.*` any more.
For that depth the mod has to be compiled in from a workspace (`mods/build_modded_workspace.py`) or
run on the EaglerForge 1.12.2 build, whose own mod API does reach the game.

## The mod button

Open the local launcher (`python3 tools/serve_builds.py` → http://127.0.0.1:8099/) and the top card is
**Add a mod**: every `.js` in `work/builds-modded/mods/` shows up as a tickbox, and ticking one puts
`?mod=…` on every Play button, with the exact resulting link shown underneath. A worked example
(a clock + session timer HUD, F6 to toggle) is copied in the first time you run it, from
`mods/examples/hud-clock.js`.

**Fabric `.jar` mods have no button, and cannot have one on these builds.** They are compiled ahead of
time: no JVM in the tab means nothing can read a jar while the game runs. A Fabric mod gets in by being
compiled in — `mods/build_modded_workspace.py` on a source workspace, or the repo's Actions workflow for
26.2. The one place a jar loads *at runtime* is the browser JVM (the `▶ launch` cards on the published
page, up to 1.12.2), because there a real Fabric Loader has a real JVM to run in.

## Why there are "Eaglercraft" builds at all, and why versions come first

The unit of this project is the **version**, and each version is served by whichever route reaches it:

| Route | What runs the game | Versions |
| --- | --- | --- |
| **real Java** | a JVM in the page (CheerpJ), official jar fetched from Mojang | 1.0 → 1.12.2 (9 catalogued, 857 available) |
| **compiled by us** | official source compiled to WebAssembly at build time | 26.2 today; any version an engine has a profile for |
| **prebuilt port** | a client somebody else already compiled for the browser | 1.13.1, 1.14.4, 1.16.5, 1.20.6, 26.2, 26.3, the classics |

Those "Eaglercraft" files are not a different game: they are the same Minecraft versions, ported to run
in a browser. They exist because the first two routes cannot reach most versions yet — Java 21 is not in
any browser JVM (1.20.5+), and LWJGL 3 has no JavaScript implementation (1.13–1.20.4). So for those
versions the only thing that runs in a tab is somebody else's compile.

Clients that already ship a mod loader (the EaglerForge build) or that are pre-modded (Astra, WurstX,
Resent, DragonX, GX-Client, Pixel) were removed: two loaders in one page fight each other, and mods here
come from this project's injector.

## When you want a public URL again

| What you want | Where it goes | What it costs you |
| --- | --- | --- |
| The code, public | GitLab or GitHub, one repository | nothing; no game files, so no takedown surface |
| The launcher page, public | GitLab Pages (free, and private sites are free too) | the page links builds, it does not host them |
| The builds themselves, public | your own hosting behind your own account | this is the part that got suspended before; keep it private or local |
| Everything, local | `tools/serve_builds.py` (above) | nothing, and it works today |

To publish the code: `brew install glab && glab auth login`, then
`GITLAB_NAMESPACE=<your-user> ./move-to-gitlab.sh` from `~/devspace/WebMC/`. Tell me the namespace
and I'll run it and wire the Pages deploy.
