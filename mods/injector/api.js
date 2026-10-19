/* WebMC mod API v3 — the mods folder, in a page.
 *
 * Spliced into an already-built client by mods/injector/inject_mod_api.py, or by the packer for every
 * client this project publishes. There is no JVM in these clients, so a `.jar`'s bytecode cannot load:
 * what loads here is a payload the mod ships *for the browser*, plus the metadata Fabric mods already
 * carry. That combination is the point of this file - a mod is a zip or a script you add at play time,
 * and nothing is rebuilt to add it.
 *
 * What it can honour, and what it cannot, stated once so nobody has to find out by experiment:
 *
 *   fabric.mod.json      read. id, name, version, entrypoints, mixins, depends - the metadata is the same
 *                        document Fabric reads, so a mod that ships a browser payload needs no second
 *                        manifest and no fork of itself.
 *   a browser payload    run. `entrypoints.webmc_runtime` names it, or it defaults to webmc.runtime.js.
 *   mixin declarations   applied at run time, where the target is reachable from the page: `head`,
 *                        `return`, `overwrite`. Refused, by name and with the reason, where it is not.
 *   Java bytecode        refused, by name and with the reason. Mixin's real job is transforming bytecode
 *                        at class-load time inside a JVM; a client compiled ahead of time has no class
 *                        load to hook and no way to execute a new .class. That is a fact about the
 *                        platform, not a limitation of this file, and the real-JVM row is where a .jar
 *                        mod of that kind actually runs.
 *   resource packs       mounted where the client has a door for them (the Eagler26 lineage exposes one),
 *                        reported where it does not.
 *
 * Three tiers of mod, because what a mod can reach differs per build and pretending otherwise is how a
 * mod author wastes a weekend:
 *
 *   page tier   always available. The game draws into a canvas on a page, so a mod can draw over it,
 *               read input, register keybinds, send chat, time things, log.
 *   deep tier   where the build's own JavaScript still names its game objects: a mod can patch a real
 *               method at run time, which is what a mixin-shaped mod needs.
 *   jvm tier    not here at all. A real Fabric Loader, in a real JVM, is the other row of this project.
 *
 * Everything is idempotent, every mod runs inside its own try/catch, and a mod that throws is dropped
 * and reported instead of taking the game with it.
 */
(function () {
  "use strict";

  if (window.ModAPI && window.ModAPI.version >= 3) return;   // idempotent

  const VERSION = 3;
  const STARTED = Date.now();
  const EVENT_NAMES = ["load", "tick", "render", "chat", "key", "resize", "error", "mod"];
  const STORAGE_KEY = "webmc.mods";
  const listeners = new Map(EVENT_NAMES.map((name) => [name, new Set()]));
  const keybinds = [];
  const started = [];

  const deep = window.WebMCDeepHooks || { available: false, anchors: [], targets: null };
  if (!deep.targets) deep.targets = {};

  const log = (...args) => {
    try { console.log("[WebMC]", ...args); } catch (error) { /* a console that is not there */ }
  };

  // ------------------------------------------------------------------ events

  function on(name, handler) {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(handler);
    return () => listeners.get(name).delete(handler);
  }

  function emit(name, payload) {
    for (const handler of [...(listeners.get(name) || [])]) {
      try {
        handler(payload);
      } catch (error) {
        log(`a ${name} handler threw:`, error);
        if (name !== "error") emit("error", { where: name, error });
      }
    }
  }

  // ------------------------------------------------------------------ the page tier

  /** The game's canvas: the biggest one on the page, whatever the build called it. */
  function getCanvas() {
    const isCanvas = typeof HTMLCanvasElement !== "undefined";
    if (isCanvas && window.lwjglCanvasElement instanceof HTMLCanvasElement) return window.lwjglCanvasElement;
    if (typeof document === "undefined" || !document.querySelectorAll) return null;
    let best = null;
    for (const canvas of document.querySelectorAll("canvas")) {
      const area = (canvas.width || 0) * (canvas.height || 0);
      if (!best || area > (best.width || 0) * (best.height || 0)) best = canvas;
    }
    return best;
  }

  /** A 2D canvas layered exactly over the game, for HUD mods. Created on first use. */
  let overlayCanvas = null;
  function overlay() {
    if (overlayCanvas && overlayCanvas.isConnected) return overlayCanvas;
    const game = getCanvas();
    if (!game || typeof document === "undefined") return null;
    const canvas = document.createElement("canvas");
    canvas.width = game.width || 854;
    canvas.height = game.height || 480;
    canvas.style.cssText = "position:absolute;left:0;top:0;pointer-events:none;z-index:9";
    const holder = game.parentElement || document.body;
    if (!holder) return null;
    if (typeof getComputedStyle === "function" && getComputedStyle(holder).position === "static") {
      holder.style.position = "relative";
    }
    holder.appendChild(canvas);
    overlayCanvas = canvas;
    return canvas;
  }

  /** Chat text, sent the way the game accepts it: through the key events it listens for. */
  function sendKeys(text) {
    const target = (typeof document !== "undefined" && document.activeElement) || window;
    for (const char of String(text)) {
      for (const type of ["keydown", "keyup"]) {
        target.dispatchEvent(new KeyboardEvent(type, {
          key: char, code: `Key${char.toUpperCase()}`, bubbles: true,
        }));
      }
    }
  }

  function pressKey(key, code) {
    for (const type of ["keydown", "keyup"]) {
      window.dispatchEvent(new KeyboardEvent(type, { key, code: code || key, bubbles: true }));
    }
  }

  function displayToChat(text) {
    log(text);
    const canvas = overlay();
    if (!canvas) return;
    const context = canvas.getContext("2d");
    // draw for a few seconds, then let it fade on its own
    const until = performance.now() + 4000;
    const draw = () => {
      context.clearRect(0, 0, canvas.width, 64);
      context.font = "16px monospace";
      context.fillStyle = "rgba(0,0,0,0.55)";
      context.fillRect(4, 6, Math.min(canvas.width - 8, context.measureText(text).width + 16), 24);
      context.fillStyle = "#fff";
      context.fillText(text, 12, 23);
      if (performance.now() < until) requestAnimationFrame(draw);
      else context.clearRect(0, 0, canvas.width, canvas.height);
    };
    requestAnimationFrame(draw);
  }

  function getPlayerName() {
    for (const key of ["webmc_username", "username", "eagler_username", "playerName"]) {
      const value = window.localStorage?.getItem(key);
      if (value) return value;
    }
    const input = document.querySelector?.("input[type=text]");
    return input && input.value ? input.value : "Player";
  }

  /** Register a keybind; the handler fires on keydown and gets the event. */
  function registerKeybind(name, key, handler) {
    keybinds.push({ name, key: String(key).toLowerCase(), handler });
    log(`keybind ${name} -> ${key}`);
  }

  function onKey(event) {
    const key = (event.key || "").toLowerCase();
    if (key === modsKey) openMods();
    for (const bind of keybinds) {
      if (bind.key === key) {
        try {
          bind.handler(event);
        } catch (error) {
          log(`keybind ${bind.name} threw:`, error);
        }
      }
    }
    emit("key", event);
  }

  // ------------------------------------------------------------------ the deep tier: real runtime patches

  /** Resolve a dotted path - `window.Game.prototype.tick` - to what it names, or null. */
  function resolve(path) {
    if (typeof path !== "string" || !path) return null;
    const parts = path.split(".");
    let holder = window;
    for (let index = 0; index < parts.length; index += 1) {
      const name = parts[index];
      if (holder === undefined || holder === null) return null;
      if (index === 0 && name === "window") continue;      // `window.` is how a mod names the root
      if (index === 0 && name === "globalThis") continue;
      if (!(name in holder)) return null;
      holder = holder[name];
    }
    return holder || null;
  }

  /**
   * Patch a real method, the way a runtime mixin does: wrap the function in place.
   *
   * `inject` is one of the three shapes this can honour honestly:
   *   head       the handler runs before the original; returning false cancels it
   *   return     the handler runs after the original and may replace its return value
   *   overwrite  the handler *is* the method (it is given the original as its last argument)
   *
   * The original is always reachable (`this.__original`) so a patch can be composed and a mod can be
   * removed again. Refused, with a reason, when the target is not reachable from the page: a compiled
   * client's game code is not a JavaScript object anybody can wrap.
   */
  function mixin(spec, owner) {
    const where = `${spec.target}${spec.method ? "#" + spec.method : ""}`;
    const refuse = (reason) => {
      const record = { target: where, inject: spec.inject || "head", applied: false, reason };
      (owner || currentMod || { mixinLog: [] }).mixinLog?.push(record);
      log(`mixin refused: ${where} - ${reason}`);
      return record;
    };
    if (!spec || !spec.target) return refuse("no target was named");
    const inject = spec.inject || "head";
    if (!["head", "return", "overwrite"].includes(inject)) {
      return refuse(`'${inject}' needs bytecode: this client has no class load to hook`);
    }
    if (typeof spec.handler !== "function") return refuse("no handler function");

    const holder = resolve(spec.target);
    if (!holder) {
      return refuse(`not reachable from the page in this build - a compiled client's game code is not a `
                    + `JavaScript object, so a mixin of this kind needs a real JVM (the real-Java row)`);
    }
    const method = spec.method;
    const original = method ? holder[method] : holder;
    if (typeof original !== "function") {
      return refuse(method ? `is not a function on the target (it is ${typeof original})`
                           : `is not a function (it is ${typeof original})`);
    }

    const patched = function (...args) {
      if (inject === "head") {
        const verdict = spec.handler.apply(this, args);
        if (verdict === false) return undefined;
        return original.apply(this, args);
      }
      if (inject === "return") {
        const value = original.apply(this, args);
        const replacement = spec.handler.apply(this, [value, ...args]);
        return replacement === undefined ? value : replacement;
      }
      return spec.handler.apply(this, [original, ...args]);      // overwrite
    };
    patched.__original = original;
    patched.__webmcMixin = { target: where, inject, mod: (owner || currentMod || {}).id || "unknown" };
    if (method) holder[method] = patched;
    const record = { target: where, inject, applied: true };
    (owner || currentMod || { mixinLog: [] }).mixinLog?.push(record);
    log(`mixin applied: ${where} (${inject})`);
    return record;
  }

  /** Undo one patch: put the original back where the patch still is. */
  function unmixin(record) {
    if (!record || !record.applied || !record.target) return false;
    const [path, method] = record.target.split("#");
    const holder = resolve(path);
    if (!holder) return false;
    const current = method ? holder[method] : holder;
    if (typeof current !== "function" || !current.__original) return false;
    if (method) holder[method] = current.__original;
    return true;
  }

  // ------------------------------------------------------------------ zip: a mod archive, read in the page

  /**
   * Read a zip's entries. Fabric mods are jars, jars are zips, and a mod that ships a browser payload has
   * exactly the same shape as one that does not - so the door reads the archive rather than demanding a
   * different container.
   *
   * Only what a mod needs: the central directory for names and offsets, then the bytes. Stored and
   * deflated entries; anything else is reported rather than guessed at.
   */
  async function unzip(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    // the end-of-central-directory record: 22 bytes plus a comment, which is why it is searched for
    let eocd = -1;
    for (let at = bytes.length - 22; at >= 0 && at >= bytes.length - 22 - 65535; at -= 1) {
      if (view.getUint32(at, true) === 0x06054b50) { eocd = at; break; }
    }
    if (eocd < 0) throw new Error("not a zip (no end-of-central-directory record)");
    const count = view.getUint16(eocd + 10, true);
    let at = view.getUint32(eocd + 16, true);
    const entries = [];
    for (let index = 0; index < count; index += 1) {
      if (view.getUint32(at, true) !== 0x02014b50) break;
      const method = view.getUint16(at + 10, true);
      const compressed = view.getUint32(at + 20, true);
      const nameLength = view.getUint16(at + 28, true);
      const extraLength = view.getUint16(at + 30, true);
      const commentLength = view.getUint16(at + 32, true);
      const localAt = view.getUint32(at + 42, true);
      const name = new TextDecoder().decode(bytes.subarray(at + 46, at + 46 + nameLength));
      entries.push({ name, method, compressed, localAt });
      at += 46 + nameLength + extraLength + commentLength;
    }

    const decoder = new TextDecoder();
    async function read(entry) {
      const local = entry.localAt;
      const nameLength = view.getUint16(local + 26, true);
      const extraLength = view.getUint16(local + 28, true);
      const start = local + 30 + nameLength + extraLength;
      const raw = bytes.subarray(start, start + entry.compressed);
      if (entry.method === 0) return raw;
      if (entry.method === 8) {
        if (typeof DecompressionStream !== "function") {
          throw new Error("this browser cannot inflate a zip entry (no DecompressionStream), so a "
                          + "compressed mod archive cannot be read here");
        }
        const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
        return new Uint8Array(await new Response(stream).arrayBuffer());
      }
      throw new Error(`zip entry ${entry.name} uses compression method ${entry.method}, which this door `
                      + `does not implement (0 and 8 are)`);
    }

    return {
      names: entries.map((entry) => entry.name),
      has: (name) => entries.some((entry) => entry.name === name),
      text: async (name) => {
        const entry = entries.find((candidate) => candidate.name === name);
        return entry ? decoder.decode(await read(entry)) : null;
      },
      bytes: async (name) => {
        const entry = entries.find((candidate) => candidate.name === name);
        return entry ? read(entry) : null;
      },
      /** Everything under a prefix, for a resource pack: `[{name, bytes}]`. */
      under: async (prefix) => {
        const out = [];
        for (const entry of entries) {
          if (entry.name.startsWith(prefix) && !entry.name.endsWith("/")) {
            out.push({ name: entry.name.slice(prefix.length), bytes: await read(entry) });
          }
        }
        return out;
      },
    };
  }

  // ------------------------------------------------------------------ the mod registry

  const mods = [];
  let currentMod = null;
  let modsKey = "f10";

  function addRecord(record) {
    mods.push(record);
    emit("mod", record);
    return record;
  }

  function byId(id) {
    return mods.find((record) => record.id === id) || null;
  }

  /** The enabled list, so a reload (or the launcher's next page) starts the same set. */
  function remember() {
    try {
      window.localStorage?.setItem(STORAGE_KEY, JSON.stringify(
        mods.filter((record) => record.enabled).map((record) => ({ id: record.id, url: record.url,
                                                                   name: record.name }))));
    } catch (error) { /* private mode, or no storage: the door still works, it just forgets */ }
  }

  async function fetchBytes(url) {
    const response = await fetch(url, { cache: "force-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    if (typeof response.arrayBuffer !== "function") {
      throw new Error("this environment cannot read bytes from a response");
    }
    return new Uint8Array(await response.arrayBuffer());
  }

  /** Run a mod's browser payload. Its own scope, its own try/catch, and the API handed to it. */
  function runPayload(source, record, kind) {
    const previous = currentMod;
    currentMod = record;
    const scope = {
      ModAPI,
      WebMC: ModAPI,
      mod: record,
      log: (...args) => {
        record.log.push(args.map(String).join(" "));
        log(`[${record.id}]`, ...args);
      },
    };
    try {
      // eslint-disable-next-line no-new-func
      const factory = new Function("mod", "ModAPI", "WebMC", "log", source);
      factory(record, ModAPI, ModAPI, scope.log);
      record.status = "running";
      record.kind = kind;
      return true;
    } catch (error) {
      record.status = "failed";
      record.error = String(error && error.message ? error.message : error);
      record.log.push(`threw while loading: ${record.error}`);
      log(`mod ${record.id} threw while loading:`, error);
      emit("error", { where: "mod", id: record.id, error });
      return false;
    } finally {
      currentMod = previous;
    }
  }

  /**
   * Load a Fabric mod: read `fabric.mod.json`, run the browser payload it names, apply what it declares.
   *
   * This is the whole "Fabric, without rebuilding anything" story in one function, including the honest
   * half: a mod whose code is Java bytecode is reported as such, by name, with the reason - because a
   * player who uploads a normal Fabric mod deserves to be told why nothing happened rather than left
   * wondering.
   */
  async function loadArchive(bytes, record) {
    const archive = await unzip(bytes);
    const manifestText = await archive.text("fabric.mod.json");
    if (!manifestText) {
      record.status = "failed";
      record.error = "no fabric.mod.json: this is not a mod archive";
      record.log.push(record.error);
      log(`${record.url || record.name}: ${record.error} (entries: ${archive.names.slice(0, 6).join(", ")})`);
      return false;
    }
    let manifest;
    try {
      manifest = JSON.parse(manifestText);
    } catch (error) {
      record.status = "failed";
      record.error = "fabric.mod.json is not valid JSON";
      record.log.push(record.error);
      return false;
    }
    record.id = manifest.id || record.id;
    record.name = manifest.name || manifest.id || record.name;
    record.version = manifest.version || "?";
    record.manifest = manifest;
    record.declared = {
      entrypoints: Object.keys(manifest.entrypoints || {}),
      mixins: Array.isArray(manifest.mixins) ? manifest.mixins.length : (manifest.mixins ? 1 : 0),
    };
    record.mixinLog = [];

    // which file is the payload? named by the mod, or the one convention this door also knows
    const declared = manifest.entrypoints?.webmc_runtime || manifest.entrypoints?.browser;
    const named = Array.isArray(declared) ? declared[0] : declared;
    const payloadName = (typeof named === "string" && named.endsWith(".js")) ? named : "webmc.runtime.js";
    const payload = await archive.text(payloadName);

    if (!payload) {
      const javaEntrypoints = manifest.entrypoints?.client || manifest.entrypoints?.main;
      record.status = "not-runnable";
      record.error = javaEntrypoints
        ? `its code is Java bytecode (${[].concat(javaEntrypoints)[0]}): a client compiled ahead of time `
          + `has no class load for a mixin to hook, so a mod of this kind needs a real JVM - that is what `
          + `the real-Java row of this project is for. A mod for this door ships ${payloadName}.`
        : `nothing to run: the archive has no ${payloadName} and names no browser payload`;
      record.log.push(record.error);
      log(`mod ${record.id}: ${record.error}`);
      return false;
    }

    const ran = runPayload(payload, record, "fabric-archive");

    // resource packs, where the client exposes a door for them (the Eagler26 lineage does)
    if (archive.names.some((name) => name.startsWith("resources/"))) {
      record.resources = await mountResources(archive, record);
    }

    // declared mixins, checked against what the payload actually applied: "declared 3, applied 2" is a
    // fact a mod author can act on, and "0 applied" with no explanation is not
    if (record.declared.mixins) {
      const applied = record.mixinLog.filter((entry) => entry.applied).length;
      record.mixins = { declared: record.declared.mixins, applied, refused: record.mixinLog.length - applied };
      if (applied < record.declared.mixins) {
        record.log.push(`declared ${record.declared.mixins} mixin(s), applied ${applied}`
                        + (record.mixinLog.length ? `: ${record.mixinLog[0].reason || ""}` : ""));
      }
    }
    return ran && true;
  }

  /** The client's own runtime resource door, if it has one. The Eagler26 lineage exposes it; a client that
   * does not is not a failure, it just cannot take a mod's textures at play time. */
  function resourceDoor() {
    const registry = window.EaglerModResourceRegistry;
    return registry && typeof registry.publishResourceReload === "function" ? registry : null;
  }

  /** Mount a mod's `resources/` folder as a resource pack, where the client has a registry to mount into. */
  async function mountResources(archive, record) {
    const registry = resourceDoor();
    if (!registry) {
      record.log.push("this client has no runtime resource registry, so the mod's resources/ folder "
                      + "was not mounted (its code still ran)");
      return { mounted: false, reason: "no resource registry in this client" };
    }
    try {
      const files = await archive.under("resources/");
      registry.publishResourceReload(record.id, files.map((file) => ({ path: file.name, bytes: file.bytes })));
      record.log.push(`mounted ${files.length} resource file(s)`);
      return { mounted: true, files: files.length };
    } catch (error) {
      record.log.push(`could not mount resources: ${error}`);
      return { mounted: false, reason: String(error) };
    }
  }

  /** A `.js` mod: a script tag, as before. Kept because the EaglerForge-style mods are written this way. */
  function loadScript(url, record) {
    return new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = url;
      if (url.endsWith(".mjs")) script.type = "module";
      script.onload = () => {
        record.status = "running";
        record.kind = "script";
        log(`mod loaded: ${url}`);
        resolve(true);
      };
      script.onerror = () => {
        record.status = "failed";
        record.error = "the script could not be fetched";
        record.log.push(record.error);
        log(`mod failed to load: ${url}`);
        emit("error", { where: "load", url });
        resolve(false);
      };
      document.head.appendChild(script);
    });
  }

  /** Add a mod by URL. Decides what it is from its name, not from a promise. */
  async function addMod(urlOrFile, options = {}) {
    const isFile = typeof File !== "undefined" && urlOrFile instanceof File;
    const name = isFile ? urlOrFile.name : String(urlOrFile).split("/").pop() || "mod";
    const id = options.id || name.replace(/\.(zip|jar|js|mjs)$/i, "");
    const existing = byId(id);
    if (existing) return existing;
    const record = addRecord({
      id, name: name.replace(/\.(zip|jar|js|mjs)$/i, ""), version: options.version || "?",
      url: isFile ? null : String(urlOrFile), source: isFile ? "file" : "url",
      enabled: true, status: "loading", log: [], mixinLog: [], added: Date.now(),
    });
    try {
      const payload = isFile ? urlOrFile : String(urlOrFile);
      if (/\.(zip|jar)$/i.test(name)) {
        const bytes = isFile ? new Uint8Array(await urlOrFile.arrayBuffer()) : await fetchBytes(payload);
        await loadArchive(bytes, record);
      } else {
        await loadScript(payload, record);
      }
    } catch (error) {
      record.status = "failed";
      record.error = String(error && error.message ? error.message : error);
      record.log.push(record.error);
      log(`mod ${id} failed: ${record.error}`);
      emit("error", { where: "mod", id, error });
    }
    remember();
    return record;
  }

  function removeMod(id) {
    const at = mods.findIndex((record) => record.id === id);
    if (at < 0) return false;
    const [record] = mods.splice(at, 1);
    for (const patch of record.mixinLog || []) unmixin(patch);
    record.status = "removed";
    remember();
    renderPanel();
    return true;
  }

  function setEnabled(id, enabled) {
    const record = byId(id);
    if (!record) return false;
    record.enabled = Boolean(enabled);
    if (record.enabled && record.mixinLog) for (const patch of record.mixinLog) unmixin(patch);
    if (record.enabled) {
      record.log.push("re-enabled: reload the page to run its code again (a patch cannot be un-run)");
    }
    remember();
    renderPanel();
    return true;
  }

  // ------------------------------------------------------------------ the mods panel

  const PANEL_CSS = `
  #webmc-mods { position: fixed; inset: auto 12px 12px auto; z-index: 2147483000;
    font: 13px/1.5 ui-monospace, Menlo, Consolas, monospace; color: #ddd; }
  #webmc-mods button { font: inherit; background: #2a2a2a; color: #e8e8e8; border: 1px solid #444;
    border-radius: 4px; padding: 4px 9px; cursor: pointer; }
  #webmc-mods button:hover { background: #383838; }
  #webmc-mods-toggle { box-shadow: 0 2px 10px rgba(0,0,0,.4); }
  #webmc-mods-list { position: absolute; right: 0; bottom: 40px; width: 400px; max-height: 60vh;
    overflow: auto; background: rgba(16,16,16,.96); border: 1px solid #3a3a3a; border-radius: 6px;
    padding: 10px; box-shadow: 0 8px 30px rgba(0,0,0,.5); }
  #webmc-mods-list h2 { font-size: 13px; margin: 0 0 8px; letter-spacing: .08em; text-transform: uppercase;
    color: #9a9a9a; }
  #webmc-mods .mod { border-top: 1px solid #2c2c2c; padding: 7px 0; }
  #webmc-mods .mod small { color: #8d8d8d; }
  #webmc-mods .mod.bad small { color: #d08a6a; }
  #webmc-mods input[type=text], #webmc-mods input[type=file] { width: 100%; background: #111; color: #ddd;
    border: 1px solid #3a3a3a; border-radius: 4px; padding: 4px 6px; font: inherit; margin: 4px 0; }
  #webmc-mods .row { display: flex; gap: 6px; align-items: center; }
  #webmc-mods .grow { flex: 1; }
  `;

  let panel = null;
  let panelOpen = false;

  function buildPanel() {
    if (panel || typeof document === "undefined" || !document.createElement) return panel;
    const wrap = document.createElement("div");
    wrap.id = "webmc-mods";
    const style = document.createElement("style");
    style.textContent = PANEL_CSS;
    const list = document.createElement("div");
    list.id = "webmc-mods-list";
    list.style.display = "none";
    const toggle = document.createElement("button");
    toggle.id = "webmc-mods-toggle";
    toggle.textContent = "Mods";
    toggle.onclick = () => openMods(!panelOpen);
    wrap.appendChild(style);
    wrap.appendChild(list);
    wrap.appendChild(toggle);
    panel = { wrap, list, toggle };
    if (document.body) document.body.appendChild(wrap);
    return panel;
  }

  function renderPanel() {
    if (!panel) return;
    const list = panel.list;
    list.innerHTML = "";
    const heading = document.createElement("h2");
    heading.textContent = `Mods (${mods.length})`;
    list.appendChild(heading);

    const door = document.createElement("p");
    door.style.cssText = "color:#8d8d8d;margin:0 0 8px";
    door.textContent = door_summary();
    list.appendChild(door);

    if (!mods.length) {
      const empty = document.createElement("p");
      empty.textContent = "No mods yet. Add one by URL, or upload a .zip / .jar (a Fabric mod with a "
                        + "browser payload) or a .js mod.";
      list.appendChild(empty);
    }

    for (const record of mods) {
      const row = document.createElement("div");
      row.className = "mod" + (record.status === "running" ? "" : " bad");
      const line = document.createElement("div");
      line.className = "row";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = record.enabled !== false;
      box.onchange = () => setEnabled(record.id, box.checked);
      const label = document.createElement("span");
      label.className = "grow";
      label.textContent = `${record.name || record.id} ${record.version || ""}`.trim();
      const drop = document.createElement("button");
      drop.textContent = "Remove";
      drop.onclick = () => removeMod(record.id);
      line.appendChild(box);
      line.appendChild(label);
      line.appendChild(drop);
      row.appendChild(line);
      const detail = document.createElement("small");
      detail.textContent = `${record.status}${record.source ? ` · ${record.source}` : ""}`
        + (record.error ? ` · ${record.error}` : "")
        + (record.mixins ? ` · mixins ${record.mixins.applied}/${record.mixins.declared}` : "");
      row.appendChild(detail);
      list.appendChild(row);
    }

    const url = document.createElement("input");
    url.type = "text";
    url.id = "webmc-mods-url";
    url.placeholder = "https://…/mod.zip or …/mod.js";
    const add = document.createElement("button");
    add.textContent = "Add by URL";
    add.onclick = () => {
      if (!url.value) return;
      const value = url.value;
      url.value = "";
      addMod(value).then(renderPanel);
    };
    const upload = document.createElement("input");
    upload.type = "file";
    upload.accept = ".zip,.jar,.js,.mjs";
    upload.onchange = () => {
      const file = upload.files && upload.files[0];
      if (file) addMod(file).then(renderPanel);
    };
    const row = document.createElement("div");
    row.className = "row";
    row.appendChild(url);
    row.appendChild(add);
    list.appendChild(row);
    list.appendChild(upload);
  }

  function door_summary() {
    const parts = ["page tier: yes"];
    parts.push(deep.available ? "deep tier: yes" : "deep tier: no (this build's game code is not reachable)");
    parts.push("Java bytecode: no (needs a real JVM)");
    return parts.join(" · ");
  }

  function openMods(open) {
    if (!buildPanel() || !document.body) {
      log("this environment has no document to draw a mods panel in; the API is still here");
      return false;
    }
    panelOpen = open === undefined ? !panelOpen : Boolean(open);
    panel.list.style.display = panelOpen ? "block" : "none";
    if (panelOpen) renderPanel();
    return panelOpen;
  }

  // ------------------------------------------------------------------ wiring

  window.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", () => emit("resize", { width: innerWidth, height: innerHeight }));
  window.addEventListener("error", (event) => emit("error", { error: event.error || event.message }));

  /** One requestAnimationFrame loop for every mod: tick then render, like the game's own. */
  let last = performance.now();
  function loop(now) {
    const delta = (now - last) / 1000;
    last = now;
    emit("tick", { delta, elapsed: (now - STARTED) / 1000 });
    emit("render", { delta, canvas: getCanvas() });
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  const ModAPI = {
    version: VERSION,
    on,
    addEventListener: on,
    emit,
    log,
    getCanvas,
    overlay,
    registerKeybind,
    displayToChat,
    sendKeys,
    pressKey,
    getPlayerName,

    // the mods folder
    addMod,
    addModFromFile: (file) => addMod(file),
    addModFromUrl: (url) => addMod(url),
    loadMod: (url) => addMod(url),
    removeMod,
    setEnabled,
    mods: () => mods.slice(),
    mod: byId,
    loadedMods: () => mods.map((record) => record.url || record.id),
    openMods,
    toggleMods: () => openMods(),
    setModsKey: (key) => { modsKey = String(key).toLowerCase(); return modsKey; },
    uptimeSeconds: () => (Date.now() - STARTED) / 1000,

    // the deep tier
    mixin,
    unmixin,
    resolve,
    hooks: deep,

    // what this build can honour, in one object a mod can read before it tries
    door: {
      static: {
        page: true,
        deep: Boolean(deep.available),
        resources: Boolean(resourceDoor()),
        bytecode: false,
        archives: "zip/jar with a fabric.mod.json and a browser payload (entrypoints.webmc_runtime)",
        mixins: ["head", "return", "overwrite"],
      },
      summary: () => door_summary(),
    },
  };

  // A build can already have a mod API - the EaglerForge builds do. Overwriting it would break
  // every mod written for it, so merge instead: ours fills the gaps it leaves and stays reachable
  // under its own name either way.
  const existing = window.ModAPI;
  if (existing && typeof existing === "object") {
    for (const [key, value] of Object.entries(ModAPI)) {
      if (!(key in existing)) existing[key] = value;
    }
    if (!existing.WebMCModAPI) existing.WebMCModAPI = ModAPI;
    log("an existing mod API was found; merged with it instead of replacing it");
    window.WebMCModAPI = ModAPI;
    if (typeof existing.emit === "function") {
      try { existing.emit("webmc-api-ready", { version: VERSION }); } catch (error) { /* optional */ }
    }
  } else {
    window.ModAPI = ModAPI;
    window.WebMCModAPI = ModAPI;
  }

  // where mods come from: the page's own query, the page's own variable, or the list the packer spliced in
  const params = new URLSearchParams(location.search || "");
  const fromQuery = [...params.getAll("mod"), ...params.getAll("mods").flatMap((value) => value.split(","))];
  const fromPage = Array.isArray(window.WebMC_MODS) ? window.WebMC_MODS : [];
  const fromPacker = Array.isArray(window.__WebMC_MODS__) ? window.__WebMC_MODS__ : [];
  const remembered = (() => {
    try {
      const raw = window.localStorage?.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw).map((entry) => entry.url || entry.id) : [];
    } catch (error) {
      return [];
    }
  })();
  const requested = [...fromQuery, ...fromPage, ...fromPacker].filter(Boolean);
  if (remembered.length && !fromQuery.length) {
    for (const entry of remembered) if (!requested.includes(entry)) requested.push(entry);
  }

  async function boot() {
    log(`mod api v${VERSION} ready (deep hooks: ${deep.available ? "yes" : "no"})`);
    // Order matters: a mod registers its handlers at module scope, so it has to be loaded before
    // "load" is announced - otherwise every mod misses the one event it is guaranteed to want.
    for (const url of requested) {
      await addMod(url);
    }
    started.push(...mods.map((record) => record.id));
    emit("load", { version: VERSION, mods: mods.length, door: ModAPI.door.static });
    if (window.WebMC_SHOW_MODS) openMods(true);
  }

  if (typeof document !== "undefined" && document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
