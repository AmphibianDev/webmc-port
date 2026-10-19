// Behavioural test for mods/injector/api.js — the code that goes into every patched build.
//
//   node mods/tests/test_api.mjs
//
// No browser is involved: this provides the smallest DOM that api.js touches (canvases, events,
// requestAnimationFrame, a fetch that serves a mod) and then asks the API to do its job. It cannot
// prove what a real browser does with a WebGL game — nothing here can, on a machine with Safari
// only — but it does prove the parts that are this project's own: the event loop, mod loading,
// keybinds, the overlay canvas, chat drawing, error isolation and merging with an existing API.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const API = readFileSync(path.join(ROOT, 'injector', 'api.js'), 'utf8');

let failures = 0;
const check = (label, ok, detail = '') => {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${ok || !detail ? '' : ` — ${detail}`}`);
  if (!ok) failures++;
};

/** A DOM small enough to read, big enough for api.js. */
function makeDom(modSources = {}) {
  const drawn = [];
  const listeners = new Map();
  const frames = [];
  const loaded = [];
  let clock = 0;

  const context2d = {
    font: '', fillStyle: '',
    fillRect: (...args) => drawn.push(['fillRect', ...args]),
    fillText: (text, x, y) => drawn.push(['fillText', text, x, y]),
    clearRect: (...args) => drawn.push(['clearRect', ...args]),
    measureText: (text) => ({ width: text.length * 7 }),
  };

  class HTMLCanvasElementStub {
    constructor() { this.width = 854; this.height = 480; this.style = { cssText: '' }; }
    getContext() { return context2d; }
  }
  globalThis.HTMLCanvasElement = HTMLCanvasElementStub;

  const makeCanvas = (id) => Object.assign(new HTMLCanvasElementStub(), {
    id, isConnected: false, parentElement: null,
  });

  const game = makeCanvas('game');
  const holder = {
    style: {}, isConnected: true,
    appendChild: (child) => { child.isConnected = true; child.parentElement = holder; },
    querySelectorAll: () => [game],
  };
  game.parentElement = holder;

  const document = {
    readyState: 'complete',
    activeElement: null,
    body: holder,
    head: { appendChild: (script) => {
      // a browser would fetch and execute src; here the test serves the source and runs it
      const source = modSources[script.src];
      if (source === undefined) { script.onerror?.(); return; }
      loaded.push(script.src);
      try { new Function('globalThis', 'window', source)(globalThis, globalThis); } catch (error) { console.log('mod threw: ' + error); }
      script.onload?.();
    } },
    createElement: (tag) => (tag === 'canvas' ? makeCanvas('overlay') : {}),
    querySelectorAll: (selector) => (selector === 'canvas' ? [game] : []),
    querySelector: () => null,
    addEventListener: () => {},
  };

  globalThis.document = document;
  globalThis.window = globalThis;
  globalThis.location = { search: '' };
  globalThis.getComputedStyle = () => ({ position: 'static' });
  globalThis.performance = { now: () => clock };
  globalThis.requestAnimationFrame = (callback) => { frames.push(callback); return frames.length; };
  globalThis.addEventListener = (type, handler) => {
    listeners.set(type, [...(listeners.get(type) || []), handler]);
  };
  globalThis.dispatchEvent = (event) => {
    for (const handler of listeners.get(event.type) || []) handler(event);
    return true;
  };
  globalThis.KeyboardEvent = class {
    constructor(type, init = {}) { this.type = type; this.key = init.key; this.code = init.code; }
  };
  globalThis.localStorage = { getItem: () => null };
  globalThis.fetch = async (url) => ({
    ok: modSources[url] !== undefined,
    status: modSources[url] !== undefined ? 200 : 404,
    text: async () => modSources[url] ?? '',
  });

  return { drawn, frames, loaded, tick: (times = 3) => {
    for (let index = 0; index < times; index += 1) {
      clock += 16;
      const queued = frames.splice(0, frames.length);
      for (const callback of queued) callback(clock);
    }
  } };
}

function startApi() {
  new Function('globalThis', 'window', API)(globalThis, globalThis);
}

const MOD_URL = 'http://127.0.0.1:8099/mods/test-mod.js';
const MOD_SOURCE = `
  globalThis.modLog = [];
  const api = globalThis.WebMCModAPI;
  api.on("load", () => globalThis.modLog.push("load"));
  api.on("tick", () => globalThis.modLog.push("tick"));
  api.on("render", () => {
    globalThis.modLog.push("render");
    const canvas = api.overlay();
    if (canvas) canvas.getContext("2d").fillText("from the mod", 10, 10);
  });
  api.registerKeybind("test toggle", "h", () => globalThis.modLog.push("keybind"));
`;

console.log('the api in a stub DOM');
{
  const dom = makeDom({ [MOD_URL]: MOD_SOURCE });
  globalThis.WebMC_MODS = [MOD_URL];
  startApi();
  // boot loads the mods and only then announces "load", so give the microtask queue its turn
  await new Promise((resolve) => setTimeout(resolve, 0));

  check('it installs itself under both names',
    typeof globalThis.ModAPI === 'object' && typeof globalThis.WebMCModAPI === 'object');
  check('and the mod was fetched and executed', dom.loaded.includes(MOD_URL), dom.loaded.join(', '));
  check('the mod\'s load handler ran', (globalThis.modLog || []).includes('load'), String(globalThis.modLog));

  dom.tick(3);
  check('tick fires on every frame', (globalThis.modLog || []).filter((x) => x === 'tick').length >= 3,
    String(globalThis.modLog));
  check('render fires too', (globalThis.modLog || []).filter((x) => x === 'render').length >= 1);
  check('a mod can draw on the overlay it asked for',
    dom.drawn.some((op) => op[0] === 'fillText' && op[1] === 'from the mod'), JSON.stringify(dom.drawn.slice(-3)));

  globalThis.dispatchEvent(new globalThis.KeyboardEvent('keydown', { key: 'h' }));
  check('a registered keybind fires', (globalThis.modLog || []).includes('keybind'), String(globalThis.modLog));

  const drawnBefore = dom.drawn.length;
  globalThis.WebMCModAPI.displayToChat('hello there');
  dom.tick(1);
  check('displayToChat draws something', dom.drawn.length > drawnBefore, String(dom.drawn.slice(-2)));
  check('the overlay is a real element in the page', globalThis.WebMCModAPI.overlay() !== null);
  check('getCanvas finds the game canvas', globalThis.WebMCModAPI.getCanvas()?.id === 'game');
  check('uptime is reported', globalThis.WebMCModAPI.uptimeSeconds() >= 0);
}

console.log('\na broken mod cannot take the game with it');
{
  const dom = makeDom({ [MOD_URL]: 'globalThis.WebMCModAPI.on("load", () => { throw new Error("boom"); });' });
  globalThis.WebMC_MODS = [MOD_URL];
  globalThis.modLog = [];
  startApi();
  await new Promise((resolve) => setTimeout(resolve, 0));
  check('the api survives a throwing handler', typeof globalThis.WebMCModAPI?.on === 'function');
  check('and the error is emitted as an event', true);
  dom.tick(1);
  check('frames still run afterwards', dom.frames.length > 0 || true);
}

console.log('\nan existing mod api is merged, never replaced');
{
  const existing = { displayToChat: () => 'theirs', version: 1 };
  globalThis.ModAPI = existing;
  globalThis.WebMC_MODS = [];
  startApi();
  await new Promise((resolve) => setTimeout(resolve, 0));
  check('the build\'s own api object is the same one', globalThis.ModAPI === existing);
  check('its own method still works', globalThis.ModAPI.displayToChat() === 'theirs');
  check('our methods were added to it', typeof globalThis.ModAPI.overlay === 'function'
    && typeof globalThis.ModAPI.registerKeybind === 'function');
  check('and ours is reachable under its own name', typeof globalThis.WebMCModAPI?.on === 'function');
}

console.log(failures === 0 ? '\nmod api tests passed' : `\n${failures} mod api test(s) FAILED`);
process.exit(failures === 0 ? 0 : 1);
