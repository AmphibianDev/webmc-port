/* WebMC mod API — spliced into a browser client by mods/injector/inject_mod_api.py.
 *
 * What it is: a JavaScript modding layer for builds that are already compiled. There is no JVM in
 * these clients, so a `.jar` cannot load; what can load is a `.js` file, and this is the surface it
 * gets. It is deliberately two-tiered, because what is reachable differs per build:
 *
 *   page tier   always available. The game runs in a canvas on a page, so a mod can draw on top of
 *               it, read input, register keybinds, send chat through synthetic keys, time things and
 *               log. A HUD, a waypoint overlay, a macro, a performance readout: all of that is here.
 *
 *   deep tier   only where the build's own JavaScript exposes named game classes (the unminified JS
 *               builds). The injector reports whether it does, and if so this API exposes the emit
 *               points it managed to insert, so a mod can ride the game's own tick instead of a
 *               requestAnimationFrame loop.
 *
 * Mods are plain scripts loaded from ?mod=<url> (repeated) or window.WebMC_MODS, and each one runs
 * inside its own try/catch: a broken mod logs and is dropped, it does not take the game with it.
 */
(function () {
  "use strict";

  if (window.ModAPI && window.ModAPI.version >= 2) return;   // idempotent

  const EVENT_NAMES = ["load", "tick", "render", "chat", "key", "resize", "error"];
  const listeners = new Map(EVENT_NAMES.map((name) => [name, new Set()]));
  const keybinds = [];
  const mods = [];
  const started = Date.now();
  const deep = window.WebMCDeepHooks || { available: false, anchors: [] };

  const log = (...args) => console.log("[WebMC]", ...args);

  function on(name, handler) {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(handler);
    return () => listeners.get(name).delete(handler);
  }

  function emit(name, payload) {
    for (const handler of listeners.get(name) || []) {
      try {
        handler(payload);
      } catch (error) {
        log(`a ${name} handler threw:`, error);
        emit("error", { where: name, error });
      }
    }
    if (name === "error") return;
  }

  /** The game's canvas: the biggest one on the page, whatever the build called it. */
  function getCanvas() {
    const isCanvas = typeof HTMLCanvasElement !== "undefined";
    if (isCanvas && window.lwjglCanvasElement instanceof HTMLCanvasElement) return window.lwjglCanvasElement;
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
    if (!game) return null;
    const canvas = document.createElement("canvas");
    canvas.width = game.width || 854;
    canvas.height = game.height || 480;
    canvas.style.cssText = "position:absolute;left:0;top:0;pointer-events:none;z-index:9";
    const holder = game.parentElement || document.body;
    if (getComputedStyle(holder).position === "static") holder.style.position = "relative";
    holder.appendChild(canvas);
    overlayCanvas = canvas;
    return canvas;
  }

  /** Chat text, sent the way the game accepts it: through the key events it listens for. */
  function sendKeys(text) {
    const target = document.activeElement || document.body;
    for (const char of text) {
      const down = new KeyboardEvent("keydown", { key: char, code: `Key${char.toUpperCase()}`, bubbles: true });
      const up = new KeyboardEvent("keyup", { key: char, code: `Key${char.toUpperCase()}`, bubbles: true });
      target.dispatchEvent(down);
      target.dispatchEvent(up);
    }
  }

  function pressKey(key, code) {
    for (const type of ["keydown", "keyup"]) {
      window.dispatchEvent(new KeyboardEvent(type, { key, code: code || key, bubbles: true }));
    }
  }

  function displayToChat(text) {
    log(text);
    const game = getCanvas();
    if (!game) return;
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
    const input = document.querySelector("input[type=text]");
    return input && input.value ? input.value : "Player";
  }

  /** Register a keybind; the handler fires on keydown and gets the event. */
  function registerKeybind(name, key, handler) {
    keybinds.push({ name, key: key.toLowerCase(), handler });
    log(`keybind ${name} -> ${key}`);
  }

  function onKey(event) {
    const key = (event.key || "").toLowerCase();
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

  window.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", () => emit("resize", { width: innerWidth, height: innerHeight }));
  window.addEventListener("error", (event) => emit("error", { error: event.error || event.message }));

  /** One requestAnimationFrame loop for every mod: tick then render, like the game's own. */
  let last = performance.now();
  function loop(now) {
    const delta = (now - last) / 1000;
    last = now;
    emit("tick", { delta, elapsed: (now - started) / 1000 });
    emit("render", { delta, canvas: getCanvas() });
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  function loadMod(url) {
    return new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = url;
      if (url.endsWith(".mjs")) script.type = "module";
      script.onload = () => {
        mods.push(url);
        log(`mod loaded: ${url}`);
        resolve(true);
      };
      script.onerror = () => {
        log(`mod failed to load: ${url}`);
        emit("error", { where: "load", url });
        resolve(false);
      };
      document.head.appendChild(script);
    });
  }

  const ModAPI = {
    version: 2,
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
    loadMod,
    loadedMods: () => mods.slice(),
    uptimeSeconds: () => (Date.now() - started) / 1000,
    deep: {
      available: Boolean(deep.available),
      anchors: deep.anchors || [],
      note: deep.available
        ? "this build keeps its game code in JavaScript, so the injector could place hooks in it"
        : "this build's game code is not reachable from JavaScript (compiled WebAssembly or minified), so mods here have the page tier only",
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
      // hand our events to the build's own API as well, when it can carry them
      try { existing.emit("webmc-api-ready", { version: 2 }); } catch (error) { /* optional */ }
    }
  } else {
    window.ModAPI = ModAPI;
    window.WebMCModAPI = ModAPI;
  }

  // mods named in the page url: ?mod=<url>&mod=<url>
  const params = new URLSearchParams(location.search);
  const urls = [...params.getAll("mod"), ...(Array.isArray(window.WebMC_MODS) ? window.WebMC_MODS : [])];

  async function boot() {
    log(`mod api v2 ready (deep hooks: ${ModAPI.deep.available ? "yes" : "no"})`);
    // Order matters: a mod registers its handlers at module scope, so it has to be loaded before
    // "load" is announced - otherwise every mod misses the one event it is guaranteed to want.
    const results = await Promise.all(urls.map(loadMod));
    emit("load", { version: 2, mods: results.filter(Boolean).length });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
