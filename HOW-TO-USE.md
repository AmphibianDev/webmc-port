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
