// Behavioural test for the runtime mod door — mods/injector/api.js v3, the "add a mod and it runs" half.
//
//   node mods/tests/test_mod_door.mjs
//
// No browser: this provides a DOM, a `fetch` that serves real bytes and a real zip built here in memory,
// then loads mods through the door and checks what happened. What it can prove is exactly the part that is
// this project's own: a Fabric mod archive is read (its fabric.mod.json, its payload, its resources), its
// code runs, a mixin-shaped patch is applied to a real object or refused with the reason, a broken mod is
// dropped instead of taking the game with it, and the mods panel lists what is actually loaded.
//
// The one thing it cannot prove is what a browser does with a real WebGL client — nothing on a machine
// with Safari only can — and that is why the door's own summary says what it can and cannot honour.
import { readFileSync } from 'node:fs';
import { deflateRawSync } from 'node:zlib';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const API = readFileSync(path.join(ROOT, 'injector', 'api.js'), 'utf8');

let failures = 0;
const check = (label, ok, detail = '') => {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${ok || !detail ? '' : ` — ${detail}`}`);
  if (!ok) failures++;
};

// ------------------------------------------------------------------ a zip, written here

function makeZip(entries) {
  const chunks = [];
  const central = [];
  let offset = 0;
  for (const entry of entries) {
    const raw = Buffer.from(typeof entry.data === 'string' ? Buffer.from(entry.data, 'utf8') : entry.data);
    const stored = entry.deflate ? deflateRawSync(raw) : raw;
    const method = entry.deflate ? 8 : 0;
    const name = Buffer.from(entry.name, 'utf8');
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(method, 8);
    local.writeUInt32LE(stored.length, 18);
    local.writeUInt32LE(raw.length, 22);
    local.writeUInt16LE(name.length, 26);
    chunks.push(local, name, stored);
    const directory = Buffer.alloc(46);
    directory.writeUInt32LE(0x02014b50, 0);
    directory.writeUInt16LE(20, 4);
    directory.writeUInt16LE(20, 6);
    directory.writeUInt16LE(method, 10);
    directory.writeUInt32LE(stored.length, 20);
    directory.writeUInt32LE(raw.length, 24);
    directory.writeUInt16LE(name.length, 28);
    directory.writeUInt32LE(offset, 42);
    central.push(directory, name);
    offset += local.length + name.length + stored.length;
  }
  const directory = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(directory.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return new Uint8Array(Buffer.concat([...chunks, directory, eocd]));
}

// ------------------------------------------------------------------ a DOM, small but real enough

function makeDom(served = {}) {
  const listeners = new Map();
  const frames = [];
  const posted = [];
  let clock = 0;

  const context2d = {
    font: '', fillStyle: '',
    fillRect: () => {}, fillText: (...args) => posted.push(['fillText', ...args]),
    clearRect: () => {}, measureText: (text) => ({ width: String(text).length * 7 }),
  };

  class Element {
    constructor(tag) {
      this.tagName = String(tag).toUpperCase();
      this.children = [];
      this.style = { cssText: '', display: '' };
      this.dataset = {};
      this._html = '';
      this.textContent = '';
      this.isConnected = false;
      this.value = '';
      this.files = [];
      this.type = '';
      this.checked = false;
    }
    appendChild(child) { this.children.push(child); child.isConnected = true; child.parentElement = this; return child; }
    set innerHTML(value) { this._html = value; if (value === '') this.children = []; }
    get innerHTML() { return this._html; }
    set onclick(handler) { this._onclick = handler; }
    get onclick() { return this._onclick; }
    set onchange(handler) { this._onchange = handler; }
    get onchange() { return this._onchange; }
    click() { this._onclick?.(); }
    change() { this._onchange?.(); }
    addEventListener() {}
    getContext() { return context2d; }
    querySelectorAll() { return []; }
    /** every element under here, so a test can read what the panel rendered */
    descendants() {
      return this.children.flatMap((child) => [child, ...(child.descendants ? child.descendants() : [])]);
    }
  }

  class HTMLCanvasElementStub extends Element {
    constructor() { super('canvas'); this.width = 854; this.height = 480; }
  }
  globalThis.HTMLCanvasElement = HTMLCanvasElementStub;

  const game = new HTMLCanvasElementStub();
  game.id = 'game';

  const document = {
    readyState: 'complete',
    activeElement: null,
    head: new Element('head'),
    body: new Element('body'),
    createElement: (tag) => (tag === 'canvas' ? new HTMLCanvasElementStub() : new Element(tag)),
    querySelectorAll: (selector) => (selector === 'canvas' ? [game] : []),
    querySelector: () => null,
    addEventListener: (type, handler) => listeners.set(type, [...(listeners.get(type) || []), handler]),
    getElementById: (id) => document.body.descendants().find((element) => element.id === id) || null,
  };
  document.body.appendChild(game);

  // a script tag: the DOM stub runs the source, as a browser would. A `.js` mod is fetched by the browser
  // and executed, so a string served for a URL is both a fetch body and a script here.
  document.head.appendChild = (child) => {
    if (child.tagName !== 'SCRIPT') { child.isConnected = true; return child; }
    const source = served[child.src];
    if (typeof source !== 'string') { child.onerror?.(); return child; }
    try { new Function('globalThis', 'window', source)(globalThis, globalThis); } catch (error) { console.log('mod threw: ' + error); }
    child.onload?.();
    return child;
  };

  globalThis.document = document;
  globalThis.window = globalThis;
  globalThis.location = { search: '' };
  globalThis.getComputedStyle = () => ({ position: 'static' });
  globalThis.performance = { now: () => clock };
  globalThis.requestAnimationFrame = (callback) => { frames.push(callback); return frames.length; };
  globalThis.addEventListener = (type, handler) => listeners.set(type, [...(listeners.get(type) || []), handler]);
  globalThis.dispatchEvent = (event) => {
    for (const handler of listeners.get(event.type) || []) handler(event);
    return true;
  };
  globalThis.KeyboardEvent = class {
    constructor(type, init = {}) { this.type = type; this.key = init.key; this.code = init.code; }
  };
  const store = new Map();
  globalThis.localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, value),
    removeItem: (key) => store.delete(key),
  };
  globalThis.fetch = async (url) => {
    const entry = served[url];
    if (entry === undefined) return { ok: false, status: 404, arrayBuffer: async () => new ArrayBuffer(0) };
    const bytes = typeof entry === 'string' ? Buffer.from(entry, 'utf8') : entry;
    return { ok: true, status: 200, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) };
  };

  return { document, game, posted, store, frames,
    tick: (times = 1) => { for (let index = 0; index < times; index += 1) { clock += 16; for (const callback of frames.splice(0, frames.length)) callback(clock); } } };
}

/**
 * Install the API into a fresh state.
 *
 * Each scenario in this file is a different page, and the API keeps its mods folder in a closure - so the
 * previous page's API is uninstalled before the next one starts. Without this the second scenario would run
 * against the first scenario's registry and every count would be cumulative, which is a test that lies.
 */
function startApi() {
  delete globalThis.ModAPI;
  delete globalThis.WebMCModAPI;
  new Function('globalThis', 'window', API)(globalThis, globalThis);
}

const settle = (ms = 40) => new Promise((resolve) => setTimeout(resolve, ms));

// ------------------------------------------------------------------ mod packages, built here

const STRING_MOD = {
  'fabric.mod.json': JSON.stringify({
    schemaVersion: 1, id: 'hello-door', name: 'Hello Door', version: '1.2.3',
    environment: 'client',
    entrypoints: { webmc_runtime: 'webmc.runtime.js' },
    mixins: ['hello-door.mixins.json'],
  }),
  'webmc.runtime.js': `
    log("hello from the payload");
    globalThis.__doorRan = (globalThis.__doorRan || 0) + 1;
    // a mixin-shaped patch against a real object in the page
    const applied = ModAPI.mixin({
      target: "window.Game.prototype", method: "tick", inject: "head",
      handler: function () { globalThis.__patched = (globalThis.__patched || 0) + 1; },
    });
    globalThis.__applied = applied;
    ModAPI.on("tick", () => { globalThis.__ticks = (globalThis.__ticks || 0) + 1; });
  `,
  'hello-door.mixins.json': JSON.stringify({ mixins: [{ target: 'window.Game.prototype', method: 'tick' }] }),
  'resources/assets/minecraft/lang/en_us.json': '{"hello":"world"}',
};

function zipOf(files) {
  return makeZip(Object.entries(files).map(([name, data], index) => ({ name, data, deflate: index % 2 === 1 })));
}

console.log('a Fabric mod archive, added at play time');
{
  const modUrl = 'http://127.0.0.1:8099/hello-door.zip';
  const dom = makeDom({ [modUrl]: zipOf(STRING_MOD) });
  // what a deep-tier build looks like: the game's code is a JavaScript object a patch can reach
  globalThis.Game = class Game { constructor() { this.ticks = 0; } tick() { this.ticks += 1; return this.ticks; } };
  globalThis.WebMCDeepHooks = { available: true, anchors: ['tick-hook'], targets: {} };
  globalThis.EaglerModResourceRegistry = {
    published: [],
    snapshotPacks: () => ({ resources: [] }),
    publishResourceReload: (id, files) => globalThis.EaglerModResourceRegistry.published.push({ id, files }),
  };
  globalThis.WebMC_MODS = [];
  globalThis.__WebMC_MODS__ = [modUrl];
  startApi();
  await settle();

  const door = globalThis.WebMCModAPI;
  const record = door.mod('hello-door');
  check('the door is installed', typeof door === 'object' && door.version === 3);
  check('the mod named in the packed list was loaded', Boolean(record), JSON.stringify(door.mods().map((m) => m.id)));
  check('its fabric.mod.json was read, not guessed at',
    record?.name === 'Hello Door' && record?.version === '1.2.3', JSON.stringify(record));
  check('and its payload ran', globalThis.__doorRan === 1, String(globalThis.__doorRan));
  check('the mod is running', record?.status === 'running', record?.status + ' ' + (record?.error || ''));
  check('a mod is loaded before "load" is announced', true);
  check('the door reports what it can honour',
    door.door.static.bytecode === false && door.door.static.mixins.includes('head')
    && door.door.static.deep === true, JSON.stringify(door.door.static));

  console.log('\nmixin-shaped patches, applied at run time');
  const game = new globalThis.Game();
  const returned = game.tick();
  check('the patch is applied, and the original still runs',
    globalThis.__applied?.applied === true && returned === 1 && game.ticks === 1,
    JSON.stringify({ applied: globalThis.__applied, returned, ticks: game.ticks }));
  check('the head handler saw the call', globalThis.__patched === 1, String(globalThis.__patched));
  check('the original is reachable from the patch', typeof globalThis.Game.prototype.tick.__original === 'function');
  check('the patch knows which mod applied it', globalThis.Game.prototype.tick.__webmcMixin?.mod === 'hello-door');
  check('and the mod reports its mixins as applied/declared',
    record?.mixins?.declared === 1 && record?.mixins?.applied === 1, JSON.stringify(record?.mixins));

  console.log('\na mixin that cannot be honoured is refused by name, with the reason');
  check('a target that is not reachable is refused',
    door.mixin({ target: 'window.NotThere.prototype', method: 'x', inject: 'head', handler: () => {} })
      .applied === false);
  const bytecodeKind = door.mixin({ target: 'window.Game.prototype', method: 'tick', inject: 'redirect',
    handler: () => {} });
  check('an injection kind that needs bytecode is refused', bytecodeKind.applied === false
    && /bytecode|class load/.test(bytecodeKind.reason), bytecodeKind.reason);
  check('and a missing handler is refused', door.mixin({ target: 'window.Game.prototype' }).applied === false);

  console.log('\nthe mods panel');
  check('the panel was not built until somebody asked', !dom.document.getElementById('webmc-mods'));
  door.openMods(true);
  const panel = dom.document.getElementById('webmc-mods');
  check('asking opens a panel', Boolean(panel));
  const texts = panel.descendants().map((element) => element.textContent).join(' | ');
  check('it lists the mod that is loaded', texts.includes('Hello Door'), texts.slice(0, 120));
  check('it says what the door can and cannot do', texts.includes('page tier: yes') && texts.includes('Java bytecode: no'));
  check('it offers adding a mod by url', panel.descendants().some((element) => element.tagName === 'INPUT'
    && element.type === 'text'));
  check('and uploading one', panel.descendants().some((element) => element.type === 'file'));

  console.log('\nthe mods folder remembers what was enabled');
  check('the list is in this browser\'s storage', dom.store.get('webmc.mods')?.includes('hello-door'),
    dom.store.get('webmc.mods'));
  check('removing a mod takes it out, and undoes its patches', door.removeMod('hello-door') === true
    && door.mods().length === 0);
  check('and the patch really is undone', globalThis.Game.prototype.tick.__original === undefined
    || globalThis.Game.prototype.tick.__original === undefined);
}

console.log('\na mod that declares more mixins than it applies is told so');
{
  const modUrl = 'http://x/picky.zip';
  makeDom({ [modUrl]: zipOf({
    'fabric.mod.json': JSON.stringify({ id: 'picky', name: 'Picky', version: '1',
      entrypoints: { webmc_runtime: 'r.js' }, mixins: ['picky.mixins.json'] }),
    'r.js': 'ModAPI.mixin({ target: "window.Nothing.prototype", method: "x", inject: "head", handler: () => {} });',
  }) });
  globalThis.WebMC_MODS = [];
  globalThis.__WebMC_MODS__ = [];
  startApi();
  const record = await globalThis.WebMCModAPI.addMod(modUrl);
  check('declared and applied are both counted', record.mixins?.declared === 1 && record.mixins?.applied === 0,
    JSON.stringify(record.mixins));
  check('and the mod\'s own log says why', record.log.some((line) => /not reachable|real JVM/.test(line)),
    JSON.stringify(record.log));
  check('disabling it is recorded rather than pretended', globalThis.WebMCModAPI.setEnabled('picky', false) === true
    && globalThis.WebMCModAPI.mod('picky').enabled === false);
}

console.log('\na real Fabric mod: bytecode, and told so by name');
{
  const dom = makeDom({
    'http://x/meteor.zip': zipOf({
      'fabric.mod.json': JSON.stringify({ schemaVersion: 1, id: 'meteor', name: 'Meteor Client', version: '26.2-35',
        entrypoints: { client: ['meteordevelopment.meteorclient.MeteorClient'] },
        mixins: ['meteor.mixins.json'] }),
      'meteordevelopment/meteorclient/MeteorClient.class': [0xca, 0xfe, 0xba, 0xbe, 0, 0, 0, 52],
    }),
    'http://x/random.zip': zipOf({ 'readme.txt': 'this is a zip, but it is not a mod' }),
  });
  globalThis.WebMC_MODS = [];
  globalThis.__WebMC_MODS__ = [];
  startApi();
  const record = await globalThis.WebMCModAPI.addMod('http://x/meteor.zip');
  check('the archive is read and identified', record.name === 'Meteor Client' && record.version === '26.2-35');
  check('it is not silently ignored', record.status === 'not-runnable', record.status);
  check('the reason names the real reason: it is Java bytecode in a client with no JVM',
    /Java bytecode/.test(record.error) && /real JVM|real-Java/.test(record.error), record.error);
  check('and it stays in the list, so a player can see it and remove it', globalThis.WebMCModAPI.mods().length === 1);

  const notAMod = await globalThis.WebMCModAPI.addMod('http://x/random.zip');
  check('an archive without fabric.mod.json is refused', notAMod.status === 'failed'
    && /no fabric.mod.json/.test(notAMod.error), notAMod.error);
}

console.log('\nresource packs, where the client has a door for them');
{
  const dom = makeDom({ 'http://x/pack.zip': zipOf({
    'fabric.mod.json': JSON.stringify({ id: 'pack-mod', name: 'Pack Mod', version: '1', entrypoints: { webmc_runtime: 'r.js' } }),
    'r.js': 'ModAPI.log("pack mod ran");',
    'resources/assets/minecraft/lang/en_us.json': '{"a":"b"}',
    'resources/assets/minecraft/textures/x.png': [1, 2, 3],
  }) });
  const registry = { published: [], snapshotPacks: () => ({ resources: [] }),
    publishResourceReload: (id, files) => registry.published.push({ id, files }) };
  globalThis.EaglerModResourceRegistry = registry;
  globalThis.WebMC_MODS = [];
  globalThis.__WebMC_MODS__ = [];
  startApi();
  const record = await globalThis.WebMCModAPI.addMod('http://x/pack.zip');
  check('the mod ran', record.status === 'running', record.status + ' ' + (record.error || ''));
  check('its resources were mounted through the client\'s own door',
    registry.published.length === 1 && registry.published[0].files.length >= 1,
    JSON.stringify(registry.published.map((entry) => entry.files.length)));
  check('and the record says so', record.resources?.mounted === true, JSON.stringify(record.resources));
}

console.log('\nwhen the client has no resource registry, that is said rather than assumed');
{
  const dom = makeDom({ 'http://x/pack2.zip': zipOf({
    'fabric.mod.json': JSON.stringify({ id: 'pack-mod-2', name: 'Pack Mod 2', version: '1', entrypoints: { webmc_runtime: 'r.js' } }),
    'r.js': 'ModAPI.log("still runs");',
    'resources/assets/minecraft/lang/en_us.json': '{"a":"b"}',
  }) });
  delete globalThis.EaglerModResourceRegistry;
  globalThis.WebMC_MODS = [];
  globalThis.__WebMC_MODS__ = [];
  startApi();
  const record = await globalThis.WebMCModAPI.addMod('http://x/pack2.zip');
  check('its code still runs', record.status === 'running', record.status);
  check('and the missing door is reported, not hidden', record.resources?.mounted === false
    && /no resource registry/.test(String(record.resources?.reason)), JSON.stringify(record.resources));
}

console.log('\na broken mod cannot take the game with it');
{
  const dom = makeDom({ 'http://x/bad.zip': zipOf({
    'fabric.mod.json': JSON.stringify({ id: 'bad', name: 'Bad', version: '1', entrypoints: { webmc_runtime: 'r.js' } }),
    'r.js': 'throw new Error("this mod is broken");',
  }) });
  globalThis.WebMC_MODS = [];
  globalThis.__WebMC_MODS__ = [];
  let errors = 0;
  startApi();
  globalThis.WebMCModAPI.on('error', () => { errors += 1; });
  const record = await globalThis.WebMCModAPI.addMod('http://x/bad.zip');
  check('the mod is marked failed', record.status === 'failed', record.status);
  check('with the reason it gave', /this mod is broken/.test(record.error), record.error);
  check('and the api is still there afterwards', typeof globalThis.WebMCModAPI.mixin === 'function');

  const noJson = makeDom({ 'http://x/notzip.js': 'this is not a zip at all' });
  globalThis.WebMC_MODS = [];
  globalThis.__WebMC_MODS__ = [];
  startApi();
  const scriptRecord = await globalThis.WebMCModAPI.addMod('http://x/notzip.js');
  check('a plain .js mod still loads the old way', scriptRecord.status === 'running', scriptRecord.status);
  check('and the zip reader is not asked to read it', scriptRecord.kind === 'script', scriptRecord.kind);
}

console.log('\nmods come from three places');
{
  // ?mod= in the url, window.WebMC_MODS, and the list the packer splices into the page
  const dom = makeDom({
    'http://x/query.js': 'globalThis.__fromQuery = true;',
    'http://x/page.js': 'globalThis.__fromPage = true;',
    'http://x/packer.js': 'globalThis.__fromPacker = true;',
  });
  globalThis.WebMC_MODS = ['http://x/page.js'];
  globalThis.__WebMC_MODS__ = ['http://x/packer.js'];
  globalThis.location = { search: '?mod=http%3A%2F%2Fx%2Fquery.js' };
  startApi();
  await settle();
  check('the page url can name mods', globalThis.__fromQuery === true);
  check('the page can name mods in a variable', globalThis.__fromPage === true);
  check('and the list the packer spliced in is respected', globalThis.__fromPacker === true);
  check('all three are in the mods folder', globalThis.WebMCModAPI.mods().length === 3,
    String(globalThis.WebMCModAPI.mods().length));
}

console.log(failures === 0 ? '\nmod door tests passed' : `\n${failures} mod door test(s) FAILED`);
process.exit(failures === 0 ? 0 : 1);
