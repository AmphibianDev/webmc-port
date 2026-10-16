// The join test's own logic, run against a real gateway and a real socket.
//
//   node tests/test_join_test.mjs
//
// tests/test_gateway.mjs proves the gateway speaks the protocol. This proves the *page* does - the same functions
// that ship inside site/join-test.html, driven from node against the gateway and a fake Minecraft server that
// answers a status ping and accepts an offline login. It is the closest thing to "does a cracked server accept a
// browser client" that can run without a browser, and it is exactly the path the game's own client takes:
//
//   page -> tunnel -> gateway -> server: handshake, status, login with the offline UUID
//
// The UUID check is against Python's md5, not against itself: an offline server derives
// UUID.nameUUIDFromBytes("OfflinePlayer:" + name), and a client that sends anything else gets refused.

import { spawn } from 'node:child_process';
import net from 'node:net';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
let failures = 0;
let checks = 0;
function check(label, ok, detail = '') {
  checks++;
  if (ok) { console.log(`  PASS  ${label}`); return true; }
  failures++;
  console.log(`  FAIL  ${label}${detail ? `\n        ${detail}` : ''}`);
  return false;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---- the page's own functions, extracted from the file that ships ------------------------------------------ */
const page = readFileSync(path.join(ROOT, 'site', 'join-test.html'), 'utf8');
const script = (page.match(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/) || [null, ''])[1];
function extract(name) {
  const pattern = new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`);
  const found = script.match(pattern);
  if (!found) { throw new Error(`site/join-test.html has no function ${name}`); }
  return found[0];
}
const harnessDir = mkdtempSync(path.join(tmpdir(), 'webmc-join-'));
const harness = path.join(harnessDir, 'page.mjs');
// Wrapped in one closure, so the page's own names cannot collide with this file's helpers - or with each other
// if a future version of the page happens to declare something twice.
writeFileSync(harness, [
  'const page = (() => {',
  [extract('normaliseGateway'), extract('md5'), extract('offlineUuid'), extract('varint'), extract('frame'),
   extract('string'), extract('tunnel'), extract('reader'), extract('packetId'), extract('readString')].join('\n'),
  'return { normaliseGateway, md5, offlineUuid, varint, frame, string, tunnel, reader, packetId, readString };',
  '})();',
  'export default page;',
].join('\n'));
const join = (await import(harness)).default;

/* ---- a gateway, and a server that behaves like an offline-mode Minecraft server ---------------------------- */
async function freePort() {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => { const { port } = server.address(); server.close(() => resolve(port)); });
  });
}

const varint = (value) => { const out = []; let v = value >>> 0; do { let b = v & 0x7f; v >>>= 7; if (v) b |= 0x80; out.push(b); } while (v); return out; };
const frame = (bytes) => new Uint8Array(varint(bytes.length).concat(bytes));
const mcString = (text) => Buffer.from(varint(Buffer.byteLength(text)).concat(Buffer.from(text, 'utf8')));

/** A server that answers a status ping and accepts an offline login, the way a cracked server does. */
function fakeServer() {
  const state = { logins: [], status: 0 };
  const server = net.createServer((socket) => {
    let buffer = Buffer.alloc(0);
    let phase = "handshake";        // handshake -> status | login
    const readVarInt = (bytes, at) => {
      let value = 0, shift = 0, position = at;
      for (;;) {
        const byte = bytes[position++];
        value |= (byte & 0x7f) << shift;
        shift += 7;
        if (!(byte & 0x80)) { return [value, position]; }
      }
    };
    socket.on('data', (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      for (;;) {
        const [length, afterLength] = buffer.length ? readVarInt(buffer, 0) : [null, null];
        if (length === null || buffer.length < afterLength + length) { return; }
        const body = buffer.subarray(afterLength, afterLength + length);
        buffer = buffer.subarray(afterLength + length);
        const [id, afterId] = readVarInt(body, 0);

        if (phase === "handshake" && id === 0x00) {
          // protocol (VarInt), address (String), port (u16), next state (VarInt)
          const [, afterProtocol] = readVarInt(body, afterId);
          const [nameLength, afterNameLength] = readVarInt(body, afterProtocol);
          const afterPort = afterNameLength + nameLength + 2;
          const [nextState] = readVarInt(body, afterPort);
          phase = nextState === 2 ? "login" : "status";
          continue;
        }
        if (phase === "status" && id === 0x00) {
          state.status += 1;
          const status = JSON.stringify({
            version: { name: "FakeCracked 1.21", protocol: 770 },
            players: { online: 3, max: 20 },
            description: { text: "a cracked server" },
          });
          const encoded = Buffer.from(status, "utf8");
          socket.write(frame([0x00].concat(varint(encoded.length), Array.from(encoded))));
          continue;
        }
        if (phase === "login" && id === 0x00) {
          // name (String), then the UUID (16 bytes) - what a 1.20.2+ client sends
          const [nameLength, afterNameLength] = readVarInt(body, afterId);
          const name = body.subarray(afterNameLength, afterNameLength + nameLength).toString("utf8");
          const uuid = Buffer.from(body.subarray(afterNameLength + nameLength, afterNameLength + nameLength + 16));
          state.logins.push({ name, uuid: uuid.toString("hex") });
          // login success: uuid, name, properties (VarInt 0)
          socket.write(frame([0x02].concat(Array.from(uuid), varint(Buffer.byteLength(name)),
                                          Array.from(Buffer.from(name, "utf8")), varint(0))));
          continue;
        }
      }
    });
    socket.on('error', () => {});
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port, state })));
}

const gatewayPort = await freePort();
const target = await fakeServer();
const gateway = spawn(process.execPath, [path.join(ROOT, 'gateway', 'server.js')], {
  env: { ...process.env, GATEWAY_PORT: String(gatewayPort), GATEWAY_HOST: '127.0.0.1', GATEWAY_SECRET: 'join-test',
         GATEWAY_ALLOW_PRIVATE_TARGETS: '1' },
  stdio: ['ignore', 'pipe', 'pipe'],
});
// wait until the gateway answers, not a fixed guess
for (let attempt = 0; attempt < 60; attempt++) {
  try {
    const response = await fetch(`http://127.0.0.1:${gatewayPort}/health`);
    if (response.ok) break;
  } catch { /* not up yet */ }
  await sleep(150);
}

try {
  const url = `ws://127.0.0.1:${gatewayPort}/v1/connect`;   // the other dialect; the page points itself at /tunnel

  console.log('the page\'s own functions');
  const uuid = join.offlineUuid('Notch');
  // what a server computes: UUID.nameUUIDFromBytes("OfflinePlayer:" + name), i.e. MD5 with version 3 and the
  // RFC-4122 variant bits set - written out here from the digest, not copied from the page
  const digest = createHash('md5').update('OfflinePlayer:Notch').digest();
  const reference = Buffer.from(digest);
  reference[6] = (reference[6] & 0x0f) | 0x30;
  reference[8] = (reference[8] & 0x3f) | 0x80;
  const versioned = reference.toString('hex');
  check('the offline UUID the page computes is the one a server computes', uuid === versioned,
    `${uuid} vs ${versioned}`);
  check('and it is a version-3 UUID, which is what makes an offline server accept it',
    uuid[12] === '3' && '89ab'.includes(uuid[16]), uuid);

  console.log('\ndialling a cracked server through the gateway');
  const notes = [];
  const socket = await join.tunnel(url, '127.0.0.1', target.port, notes);
  check('the gateway opens the tunnel and reports the target', Boolean(socket) && notes.length > 0, notes.join(' | '));

  const status = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), 6000);
    join.reader(socket, (body) => {
      const [id, at] = join.packetId(body);
      if (id === 0x00) { clearTimeout(timer); try { resolve(JSON.parse(join.readString(body, at))); } catch { resolve(null); } }
    });
    socket.send(join.frame([0x00].concat(join.varint(770), join.string('127.0.0.1'), [0, 25565], join.varint(1))));
    socket.send(join.frame([0x00]));
  });
  check('a status ping comes back through the tunnel', Boolean(status) && status.players && status.players.max === 20,
    JSON.stringify(status).slice(0, 120));
  check('and it is the server that answered, not the gateway', status && String(status.description.text).includes('cracked'),
    JSON.stringify(status && status.description));
  socket.close();

  console.log('\nlogging in as an offline player');
  const loginNotes = [];
  const loginSocket = await join.tunnel(url, '127.0.0.1', target.port, loginNotes);
  const uuidBytes = [];
  for (let i = 0; i < 32; i += 2) { uuidBytes.push(parseInt(uuid.substr(i, 2), 16)); }
  const reply = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ id: null, why: 'no reply' }), 6000);
    join.reader(loginSocket, (body) => { clearTimeout(timer); resolve({ id: join.packetId(body)[0] }); });
    loginSocket.send(join.frame([0x00].concat(join.varint(770), join.string('127.0.0.1'), [0, 25565], join.varint(2))));
    loginSocket.send(join.frame([0x00].concat(join.string('Notch'), uuidBytes)));
  });
  check('an offline-mode server accepts the login (packet 0x02)', reply.id === 0x02, `id=${reply.id} ${reply.why || ''}`);
  check('and the name and UUID it received are the ones the page sent',
    target.state.logins.length === 1 && target.state.logins[0].name === 'Notch' && target.state.logins[0].uuid === uuid,
    JSON.stringify(target.state.logins));
  loginSocket.close();

  console.log('\nand a refusal is legible');
  const badNotes = [];
  let refused = null;
  try {
    await join.tunnel(url, '127.0.0.1', 3306, badNotes);
  } catch (error) {
    refused = String(error.message);
  }
  check('a blocked port is refused with its own words, not a hang',
    Boolean(refused) && refused.includes('PORT_BLOCKED'), String(refused));
  let missing = null;
  try {
    await join.tunnel(url, 'no-such-host.invalid', 25565, []);
  } catch (error) {
    missing = String(error.message);
  }
  check('and a name that cannot be resolved says so', Boolean(missing) && /DNS_FAILED|gateway refused/.test(missing),
    String(missing));
} finally {
  gateway.kill();
  target.server.close();
}

console.log(`\n${checks - failures}/${checks} join-test checks passed`);
process.exit(failures ? 1 : 0);
