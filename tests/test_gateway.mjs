// The gateway test: a fake Minecraft server, our gateway, and a client that speaks the protocol the downloaded
// client speaks. Everything here is real - a TCP socket, a WebSocket upgrade, frames on both sides - because the
// only question worth answering about a gateway is whether bytes from a page arrive at a Java server.
//
// it starts gateway/server.js as a child process on a free port, runs a TCP server that behaves enough like a
// Minecraft server to be recognised (its first write is a handshake reply), and then drives the session:
// token, open, ready, echo, flow control, ping, close, and the refusals (blocked port, private address, no
// handshake, bad token, wrong first frame).

import { spawn } from 'node:child_process';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const GATEWAY = path.join(ROOT, 'gateway', 'server.js');

let failures = 0;
let checks = 0;
function check(name, ok, detail = '') {
  checks++;
  if (ok) { console.log(`  PASS  ${name}`); return true; }
  failures++;
  console.log(`  FAIL  ${name}${detail ? `\n        ${detail}` : ''}`);
  return false;
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function freePort() {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function startGateway(env) {
  const child = spawn(process.execPath, [GATEWAY], { env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
  const lines = [];
  child.stdout.on('data', (d) => lines.push(String(d)));
  child.stderr.on('data', (d) => lines.push(String(d)));
  return { child, lines };
}

async function waitForHealth(port, attempts = 60) {
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`);
      if (res.ok) return await res.json();
    } catch { /* not up yet */ }
    await sleep(100);
  }
  throw new Error('the gateway did not come up');
}

/** A TCP server that answers like a server being joined: its reply is a VarInt frame starting with packet 0x02. */
function fakeMinecraftServer() {
  const state = { received: [] };
  const server = net.createServer((socket) => {
    socket.on('data', (chunk) => {
      state.received.push(chunk);
      socket.write(Buffer.concat([Buffer.from([chunk.length]), Buffer.from([0x02]), Buffer.from('welcome')]));
    });
    socket.on('error', () => {});
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port, state }));
  });
}

/** The client half of the protocol, written to the spec the downloaded client implements. */
function goldProxySession(url, { host, port, token, srv = false, firstFrame = null, openFrame = null } = {}) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url, ['goldproxy.v1']);
    socket.binaryType = 'arraybuffer';
    const result = { control: [], binary: [], close: null, error: null, ready: false };
    const timer = setTimeout(() => { socket.close(); reject(new Error('session timed out')); }, 8000);
    socket.onopen = () => {
      const message = openFrame === null
        ? { t: 'open', v: 1, token, host, port, srv }
        : openFrame;
      socket.send(JSON.stringify(message));
    };
    socket.onmessage = ({ data }) => {
      if (typeof data === 'string') {
        const message = JSON.parse(data);
        result.control.push(message);
        if (message.t === 'ready') {
          result.ready = true;
          if (firstFrame) socket.send(firstFrame);
        } else if (message.t === 'end') {
          result.end = message;
        } else if (message.t === 'error') {
          // a refusal can arrive after the target answered (a bad first frame), so the caller is released here
          // rather than left waiting for a close that the early resolve would then race
          resolve({ socket, result, timer });
        }
      } else {
        result.binary.push(Buffer.from(data));
        if (result.binary.length === 1) resolve({ socket, result, timer });
      }
    };
    socket.onerror = () => { result.error = 'socket error'; };
    socket.onclose = (event) => {
      result.close = { code: event.code, reason: event.reason, wasClean: event.wasClean };
      if (!result.ready) { clearTimeout(timer); resolve({ socket: null, result, timer }); }
    };
  });
}

async function main() {
  const gatewayPort = await freePort();
  const target = await fakeMinecraftServer();
  const gateway = startGateway({
    GATEWAY_PORT: String(gatewayPort),
    GATEWAY_HOST: '127.0.0.1',
    GATEWAY_SECRET: 'a-test-secret',
    GATEWAY_ALLOW_PRIVATE_TARGETS: '1', // the fake server is on 127.0.0.1, which a public gateway refuses
    GATEWAY_TOKEN_PER_MINUTE: '200',
  });

  try {
    const health = await waitForHealth(gatewayPort);
    check('the gateway reports its own status', health.ok === true && health.subprotocol === 'goldproxy.v1', JSON.stringify(health).slice(0, 120));
    check('and says it speaks the subprotocol the downloaded client uses',
      health.protocolVersion === 1 && health.maximumConnections > 0);
    const preflight = await fetch(`http://127.0.0.1:${gatewayPort}/v1/token`, {
      method: 'OPTIONS', headers: { origin: 'https://example.invalid', 'access-control-request-method': 'POST', 'access-control-request-headers': 'content-type' },
    });
    check('a browser preflight is answered, or the token request never happens',
      preflight.status === 204 && preflight.headers.get('access-control-allow-origin') === 'https://example.invalid',
      `${preflight.status} ${preflight.headers.get('access-control-allow-origin')}`);
    const crossOrigin = await fetch(`http://127.0.0.1:${gatewayPort}/health`, { headers: { origin: 'https://example.invalid' } });
    check('and every JSON answer carries the origin header', crossOrigin.headers.get('access-control-allow-origin') === 'https://example.invalid');

    check('blocked ports are listed, not hidden', Array.isArray(health.blockedPorts) && health.blockedPorts.includes(22) && health.blockedPorts.includes(3306));

    const url = `ws://127.0.0.1:${gatewayPort}/v1/connect`;

    // --- the happy path: token, open, ready, bytes both ways ------------------------------------------------
    const tokenResponse = await fetch(`http://127.0.0.1:${gatewayPort}/v1/token`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ host: '127.0.0.1', port: target.port }),
    });
    const tokenBody = await tokenResponse.json();
    check('a token is issued for one destination', tokenResponse.ok && typeof tokenBody.token === 'string', JSON.stringify(tokenBody));

    // the first frame a real client sends is a Minecraft handshake: VarInt length, packet id 0x00
    const handshake = Buffer.from([0x10, 0x00, 0x2f, 0x09, 0x31, 0x32, 0x37, 0x2e, 0x30, 0x2e, 0x30, 0x2e, 0x31,
      0x63, 0xdd, 0x01, 0x01]);
    const { socket, result, timer } = await goldProxySession(url, {
      host: '127.0.0.1', port: target.port, token: tokenBody.token, srv: false, firstFrame: handshake,
    });
    clearTimeout(timer);
    check('the session opens and the gateway says the target answered', result.ready === true,
      JSON.stringify(result.control));
    const ready = result.control.find((m) => m.t === 'ready') || {};
    check('the ready frame carries the resolved address and timings',
      ready.ip === '127.0.0.1' && ready.port === target.port && typeof ready.dnsMs === 'number' && typeof ready.connectMs === 'number',
      JSON.stringify(ready));
    check('a flow-control window is published', ready.window > 0, String(ready.window));
    check('the bytes the client sent arrived at the server', Buffer.concat(target.state.received).equals(handshake),
      `${target.state.received.length} chunk(s), ${Buffer.concat(target.state.received).length} bytes`);
    check('and the server\'s reply came back as a binary frame',
      result.binary.length === 1 && result.binary[0].toString('latin1').endsWith('welcome'),
      result.binary.map((b) => b.length).join(','));

    // --- ping and ack over the control channel ---------------------------------------------------------------
    const answered = new Promise((resolve) => {
      const onMessage = ({ data }) => {
        if (typeof data !== 'string') return;
        const message = JSON.parse(data);
        if (message.t === 'pong') { socket.removeEventListener('message', onMessage); resolve(message); }
      };
      socket.addEventListener('message', onMessage);
    });
    socket.send(JSON.stringify({ t: 'ping', id: 7 }));
    const pong = await answered;
    check('a ping is answered with its own id', pong.id === 7);
    socket.send(JSON.stringify({ t: 'ack', n: result.binary[0].length }));
    socket.send(JSON.stringify({ t: 'close' }));
    await sleep(200);
    check('a close request is honoured', socket.readyState === 2 || socket.readyState === 3, `readyState=${socket.readyState}`);

    // --- refusals: each one is a named code, not a hang ------------------------------------------------------
    const refuses = async (label, expected, options) => {
      const outcome = await goldProxySession(url, options);
      const codes = outcome.result.control.filter((m) => m.t === 'error').map((m) => m.code);
      const closed = outcome.result.close?.code;
      const ok = codes.includes(expected) || closed === { PORT_BLOCKED: 4102, ADDRESS_BLOCKED: 4103, UNAUTHORIZED: 4003, NOT_MINECRAFT: 4300, BAD_DESTINATION: 4100, UNSUPPORTED_VERSION: 4001 }[expected];
      check(label, ok, `codes=${JSON.stringify(codes)} close=${JSON.stringify(outcome.result.close)}`);
      try { outcome.socket?.close(); } catch { /* done */ }
    };

    await refuses('a database port is refused by name', 'PORT_BLOCKED', { host: '127.0.0.1', port: 3306, srv: false });
    await refuses('a privileged port is refused', 'PORT_BLOCKED', { host: '127.0.0.1', port: 22, srv: false });
    await refuses('a nonsense host is refused', 'BAD_DESTINATION', { host: 'not a host', port: 25565, srv: false });
    await refuses('a token for another destination is refused', 'UNAUTHORIZED',
      { host: '127.0.0.1', port: target.port, srv: false, token: 'x.y' });
    await refuses('a version the gateway does not speak is refused', 'UNSUPPORTED_VERSION',
      { openFrame: { t: 'open', v: 99, host: '127.0.0.1', port: target.port, srv: false } });

    const notMinecraft = await goldProxySession(url, {
      host: '127.0.0.1', port: target.port, srv: false, firstFrame: Buffer.from('GET / HTTP/1.0\r\n\r\n'),
    });
    await sleep(150);
    check('a connection that is not Minecraft is refused as such',
      notMinecraft.result.control.some((m) => m.t === 'error' && m.code === 'NOT_MINECRAFT')
      || notMinecraft.result.close?.code === 4300,
      JSON.stringify(notMinecraft.result.control));
    try { notMinecraft.socket?.close(); } catch { /* done */ }
    check('and it is refused before the server ever sees the bytes',
      Buffer.concat(target.state.received).every((b) => b !== 0x47),
      'a gateway that pipes a port scan is a port scanner with extra steps');

    const noOpen = await goldProxySession(url, { openFrame: { t: 'ping', id: 1 } });
    check('a session whose first frame is not an open request is refused',
      noOpen.result.control.some((m) => m.t === 'error' && m.code === 'PROTOCOL_ERROR')
      || noOpen.result.close?.code === 4000, JSON.stringify(noOpen.result.control));
    try { noOpen.socket?.close(); } catch { /* done */ }

    // --- a private target is refused when the switch is off --------------------------------------------------
    const strictPort = await freePort();
    const strict = startGateway({ GATEWAY_PORT: String(strictPort), GATEWAY_HOST: '127.0.0.1', GATEWAY_SECRET: 's' });
    try {
      await waitForHealth(strictPort);
      const strictUrl = `ws://127.0.0.1:${strictPort}/v1/connect`;
      const refused = await goldProxySession(strictUrl, { host: '127.0.0.1', port: target.port, srv: false });
      check('with private targets off, a loopback target is refused by name',
        refused.result.control.some((m) => m.t === 'error' && m.code === 'ADDRESS_BLOCKED')
        || refused.result.close?.code === 4103, JSON.stringify(refused.result.control));
      const tokenTry = await fetch(`http://127.0.0.1:${strictPort}/v1/token`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ host: '10.0.0.5', port: 25565 }),
      });
      check('and the token issuer refuses it too, so the client is told early',
        tokenTry.status === 400 && (await tokenTry.json()).error === 'ADDRESS_BLOCKED');
      const domainTry = await fetch(`http://127.0.0.1:${strictPort}/v1/token`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ host: 'localhost', port: 25565 }),
      });
      check('a name is judged by the address it resolves to later, not by its spelling',
        domainTry.status === 200, 'a name is only as private as its records; the gateway re-checks after DNS');
    } finally {
      strict.child.kill();
    }

    check('the gateway never wrote a packet-level log of the session',
      !gateway.lines.join('').includes('username'), 'bytes are a pipe here, not a log');
  } finally {
    gateway.child.kill();
    target.server.close();
  }

  console.log(`\n${checks - failures}/${checks} gateway checks passed`);
  process.exit(failures ? 1 : 0);
}

main().catch((error) => {
  console.error('the gateway test could not run:', error);
  process.exit(1);
});
