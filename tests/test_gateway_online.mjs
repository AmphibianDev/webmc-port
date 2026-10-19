// The online-mode ("premium") gateway test.
//
// What it proves is the one thing that cannot be reasoned about from the outside: that a client which was never
// modified - the compiled clients in the catalogue - can join an online-mode server through the gateway. So
// everything here is real except the game: a fake Minecraft server that runs a genuine RSA/AES handshake with its
// own key pair, a fake session server that records the join call, and our gateway in the middle. The client side
// is a WebSocket that speaks exactly what the downloaded client speaks.
//
// The checks are the handshake itself: the server's challenge comes back decrypted, the shared secret decrypts to
// 16 bytes, the digest the server computes is the one the session server was told (which is what hasJoined checks
// on the real one), the profile id is the account's, the login name was rewritten to the account's name (an
// online-mode server verifies the name the client sent), and the traffic on both sides of the encryption is
// readable in the clear. Then the two refusals that matter: no credentials on an online-mode server, and an
// offline server that must not be disturbed by any of this.

import { spawn } from 'node:child_process';
import net from 'node:net';
import http from 'node:http';
import crypto from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import online from '../gateway/online.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const GATEWAY = path.join(ROOT, 'gateway', 'server.js');
const UUID = '3f9b1e2c4d5a4b6c8d7e6f5a4b3c2d1e';

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

// --------------------------------------------------------------------------------------------- the two fakes

/** The real session server, reduced to the one endpoint and the one status code that matter. */
function fakeSessionServer() {
  const state = { calls: [], respond: 204 };
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      if (req.method === 'POST' && req.url === '/session/minecraft/join') {
        let parsed = null;
        try { parsed = JSON.parse(body); } catch { /* recorded as null */ }
        state.calls.push({ body: parsed, path: req.url, contentType: req.headers['content-type'] });
        res.writeHead(state.respond);
        if (state.respond !== 204) res.end(JSON.stringify({ error: 'ForbiddenOperationException', errorMessage: 'Invalid token.' }));
        else res.end();
        return;
      }
      res.writeHead(404);
      res.end();
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port, state }));
  });
}

/**
 * A server that joins like an online-mode one: it sends a real EncryptionRequest with its own key pair, checks the
 * response the way ServerLoginPacketListenerImpl does, and then speaks AES/CFB8 - echoing whatever it receives in
 * the clear, encrypted, so a byte that makes the round trip proves the cipher is in step in both directions.
 */
function fakeOnlineServer() {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 1024 });
  const publicDer = publicKey.export({ type: 'spki', format: 'der' });
  const challenge = crypto.randomBytes(4);
  const state = {
    protocol: null, names: [], nameRewriteAt: null, secret: null, digest: null, tokenOk: false,
    echo: [], sentAfterEncryption: null, encryptionRequestsSent: 0,
  };
  const frame = (payload) => Buffer.concat([online.writeVarInt(payload.length), payload]);
  const server = net.createServer((socket) => {
    let buffer = Buffer.alloc(0);
    let phase = 'login';
    let decrypt = null;
    let encrypt = null;
    const sendPlain = (payload) => socket.write(frame(payload));
    socket.on('error', () => {});
    socket.on('data', (chunk) => {
      if (phase === 'encrypted') {
        const plain = decrypt.update(chunk);
        state.echo.push(plain.toString('utf8'));
        socket.write(encrypt.update(plain));
        return;
      }
      buffer = Buffer.concat([buffer, chunk]);
      for (;;) {
        const length = online.readVarInt(buffer, 0);
        if (!length || buffer.length < length.size + length.value) break;
        const payload = buffer.subarray(length.size, length.size + length.value);
        buffer = buffer.subarray(length.size + length.value);
        const id = online.readVarInt(payload, 0);
        if (!id) continue;

        if (phase === 'login' && id.value === 0x00 && state.protocol === null) {
          const version = online.readVarInt(payload, id.size);
          state.protocol = version ? version.value : 0;
          continue;
        }
        if (phase === 'login' && id.value === 0x00) {
          const name = online.readString(payload, id.size);
          state.names.push(name ? name.value : null);
          state.nameRewriteAt = payload.length;
          // The encryption request, in the shape ClientboundHelloPacket reads: server id, key, challenge, flag.
          state.encryptionRequestsSent++;
          sendPlain(Buffer.concat([
            online.writeVarInt(0x01),
            online.writeVarInt(0), // server id, ""
            online.writeVarInt(publicDer.length), publicDer,
            online.writeVarInt(challenge.length), challenge,
            Buffer.from([1]), // shouldAuthenticate
          ]));
          phase = 'awaiting-response';
          continue;
        }
        if (phase === 'awaiting-response' && id.value === 0x01) {
          // ServerboundKeyPacket in a current client: two byte arrays, no boolean.
          const secret = online.readByteArray(payload, id.size);
          const token = online.readByteArray(payload, id.size + secret.size);
          state.secret = crypto.privateDecrypt({ key: privateKey, padding: crypto.constants.RSA_PKCS1_PADDING }, secret.value);
          const tokenPlain = crypto.privateDecrypt({ key: privateKey, padding: crypto.constants.RSA_PKCS1_PADDING }, token.value);
          state.tokenOk = tokenPlain.equals(challenge);
          state.digest = online.minecraftDigest('', state.secret, publicDer);
          decrypt = crypto.createDecipheriv('aes-128-cfb8', state.secret, state.secret);
          encrypt = crypto.createCipheriv('aes-128-cfb8', state.secret, state.secret);
          phase = 'encrypted';
          // SetCompression then LoginFinished, encrypted - which is the order vanilla sends them in.
          const compression = frame(Buffer.concat([online.writeVarInt(0x03), online.writeVarInt(256)]));
          const success = frame(Buffer.concat([online.writeVarInt(0x02), Buffer.from('logged-in-payload')]));
          state.sentAfterEncryption = Buffer.concat([compression, success]);
          socket.write(encrypt.update(state.sentAfterEncryption));
          if (buffer.length) {
            const plain = decrypt.update(buffer);
            state.echo.push(plain.toString('utf8'));
            socket.write(encrypt.update(plain));
            buffer = Buffer.alloc(0);
          }
          continue;
        }
      }
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port, state, publicDer }));
  });
}

/** A server that behaves like an offline ("cracked") one: it answers as soon as the handshake arrives. */
function fakeOfflineServer() {
  const state = { received: Buffer.alloc(0) };
  const server = net.createServer((socket) => {
    socket.on('data', (chunk) => {
      state.received = Buffer.concat([state.received, chunk]);
      socket.write(Buffer.concat([Buffer.from([9]), Buffer.from([0x02]), Buffer.from('welcome')]));
    });
    socket.on('error', () => {});
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port, state }));
  });
}

// ----------------------------------------------------------------------------------------------- the client

const HELLO_NAME = 'Steve';
const HANDSHAKE = Buffer.concat([
  online.writeVarInt(0x00),
  online.writeVarInt(767),
  online.writeVarInt(9), Buffer.from('127.0.0.1'),
  Buffer.from([0x63, 0xdd]), // 25565
  online.writeVarInt(2), // next state: login
]);
const LOGIN_START = Buffer.concat([online.writeVarInt(0x00), online.writeVarInt(HELLO_NAME.length), Buffer.from(HELLO_NAME)]);
const handshakeBytes = Buffer.concat([online.writeVarInt(HANDSHAKE.length), HANDSHAKE]);
const loginBytes = Buffer.concat([online.writeVarInt(LOGIN_START.length), LOGIN_START]);

function goldProxySession(url, openFrame) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url, ['goldproxy.v1']);
    socket.binaryType = 'arraybuffer';
    const result = { control: [], binary: [], close: null, error: null, ready: false };
    const timer = setTimeout(() => { try { socket.close(); } catch { /* gone */ } reject(new Error('session timed out')); }, 10000);
    socket.onopen = () => socket.send(JSON.stringify(openFrame));
    socket.onmessage = ({ data }) => {
      if (typeof data === 'string') {
        const message = JSON.parse(data);
        result.control.push(message);
        if (message.t === 'ready') { result.ready = true; resolve({ socket, result, timer }); }
        else if (message.t === 'error') resolve({ socket, result, timer });
      } else {
        result.binary.push(Buffer.from(data));
      }
    };
    socket.onerror = () => { result.error = 'socket error'; };
    socket.onclose = (event) => {
      result.close = { code: event.code, reason: event.reason };
      if (!result.ready) { clearTimeout(timer); resolve({ socket: null, result, timer }); }
    };
  });
}

/** The profile engine's tunnel dialect, which is the one the game's own client speaks (`/tunnel`). */
function tunnelSession(url, connectFrame) {
  return new Promise((resolve) => {
    const socket = new WebSocket(url, ['gaius-tunnel']);
    socket.binaryType = 'arraybuffer';
    const result = { control: [], binary: [], close: null, ready: false };
    socket.onopen = () => socket.send(JSON.stringify(connectFrame));
    socket.onmessage = ({ data }) => {
      if (typeof data === 'string') {
        const message = JSON.parse(data);
        result.control.push(message);
        if (message.type === 'connected') { result.ready = true; resolve({ socket, result }); }
        else if (message.type === 'error') resolve({ socket, result });
      } else {
        result.binary.push(Buffer.from(data));
      }
    };
    socket.onclose = (event) => { result.close = { code: event.code, reason: event.reason }; if (!result.ready) resolve({ socket: null, result }); };
  });
}

/** Waits for a condition on the collected state, so the test never races the network. */
async function until(predicate, timeoutMs = 6000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return true;
    await sleep(25);
  }
  return false;
}

function binaryOf(result) { return Buffer.concat(result.binary); }

// --------------------------------------------------------------------------------------------------- the test

async function main() {
  const gatewayPort = await freePort();
  const sessions = await fakeSessionServer();
  const onlineTarget = await fakeOnlineServer();
  const offlineTarget = await fakeOfflineServer();

  const gateway = startGateway({
    GATEWAY_PORT: String(gatewayPort),
    GATEWAY_HOST: '127.0.0.1',
    GATEWAY_SECRET: 'an-online-test-secret',
    GATEWAY_ALLOW_PRIVATE_TARGETS: '1',
    GATEWAY_SESSION_SERVER: `http://127.0.0.1:${sessions.port}`,
    GATEWAY_TOKEN_PER_MINUTE: '200',
  });

  try {
    const health = await waitForHealth(gatewayPort);
    check('the gateway says it can do online-mode joins, and where it asks',
      health.online && health.online.supported === true && health.online.sessionServer.includes('127.0.0.1'),
      JSON.stringify(health.online));
    check('and says out loud that it does not hold a chat-signing key',
      health.online.secureProfileKey === false);

    // ---- a premium join, end to end ----------------------------------------------------------------------
    const premium = await goldProxySession(`ws://127.0.0.1:${gatewayPort}/v1/connect`, {
      t: 'open', v: 1, host: '127.0.0.1', port: onlineTarget.port, srv: false,
      auth: { accessToken: 'mc-access-token', uuid: UUID, name: 'PremiumPlayer' },
    });
    check('a session that offers an account opens', premium.result.ready === true, JSON.stringify(premium.result.control));
    check('and says the join is online', premium.result.control.find((m) => m.t === 'ready')?.online === true);
    premium.socket.send(handshakeBytes);
    premium.socket.send(loginBytes);

    const serverSaw = await until(() => onlineTarget.state.digest !== null);
    check('the fake server completed the handshake it started', serverSaw, JSON.stringify(onlineTarget.state).slice(0, 200));
    check('the login name was rewritten to the account name, not the one the client typed',
      onlineTarget.state.names[0] === 'PremiumPlayer', `server saw ${JSON.stringify(onlineTarget.state.names)}`);
    check('the challenge came back encrypted with the account\'s RSA key and decrypts to the same bytes',
      onlineTarget.state.tokenOk === true);
    check('the shared secret decrypts to a 16-byte AES key',
      Buffer.isBuffer(onlineTarget.state.secret) && onlineTarget.state.secret.length === 16);

    const joinCall = sessions.state.calls[0];
    check('the gateway told the session server about the join, once',
      sessions.state.calls.length === 1 && joinCall?.contentType === 'application/json',
      JSON.stringify(sessions.state.calls));
    check('with the account\'s profile id, undashed',
      joinCall?.body?.selectedProfile === UUID, joinCall?.body?.selectedProfile);
    check('and with the same digest the server computed for hasJoined',
      joinCall?.body?.serverId === onlineTarget.state.digest,
      `session server got ${joinCall?.body?.serverId}, server computed ${onlineTarget.state.digest}`);
    check('the join carried the access token and nothing else of the client',
      joinCall?.body?.accessToken === 'mc-access-token' && Object.keys(joinCall.body).length === 3,
      JSON.stringify(Object.keys(joinCall?.body || {})));

    const gotLoginSuccess = await until(() => binaryOf(premium.result).length >= 0
      && binaryOf(premium.result).includes(Buffer.from('logged-in-payload')));
    check('the client is handed the login success in the clear, decrypted by the gateway',
      gotLoginSuccess, `client got ${binaryOf(premium.result).length} bytes`);
    const clientBytes = binaryOf(premium.result);
    check('and the compression switch before it, in the frame the server sent',
      clientBytes.length > 0 && online.readVarInt(clientBytes, online.readVarInt(clientBytes, 0).size).value === 0x03,
      clientBytes.subarray(0, 8).toString('hex'));

    // ---- the other direction: client bytes are encrypted on the way out ------------------------------------
    premium.socket.send(Buffer.from('hello-from-the-page'));
    const echoed = await until(() => onlineTarget.state.echo.join('').includes('hello-from-the-page'));
    check('bytes the client sends after login arrive at the server decrypted (the gateway encrypts them)',
      echoed, JSON.stringify(onlineTarget.state.echo));
    check('and the server\'s answer comes back to the client decrypted',
      await until(() => binaryOf(premium.result).toString('utf8').includes('hello-from-the-page')));
    try { premium.socket.close(); } catch { /* already closed */ }

    // ---- an online-mode server, a client with no account ---------------------------------------------------
    const anonymous = await goldProxySession(`ws://127.0.0.1:${gatewayPort}/v1/connect`, {
      t: 'open', v: 1, host: '127.0.0.1', port: onlineTarget.port, srv: false,
    });
    anonymous.socket.send(handshakeBytes);
    anonymous.socket.send(loginBytes);
    const refused = await until(() => anonymous.result.control.some((m) => m.t === 'error'));
    const refusal = anonymous.result.control.find((m) => m.t === 'error');
    check('an anonymous client is refused by an online-mode server in words a page can act on',
      refused && refusal.code === 'AUTH_REQUIRED' && /sign in/i.test(refusal.msg), JSON.stringify(refusal));
    check('and the refusal is a close code in the application range, not a network error',
      await until(() => anonymous.result.close !== null) && anonymous.result.close.code === 4400,
      JSON.stringify(anonymous.result.close));
    await sleep(100);
    check('and nothing was asked of the session server for it', sessions.state.calls.length === 1);
    check('and it never saw an encryption request', !anonymous.result.binary.length, `${anonymous.result.binary.length} bytes`);

    // ---- a bad sign-in is reported as one ---------------------------------------------------------------
    sessions.state.respond = 403;
    onlineTarget.state.digest = null;
    const expired = await goldProxySession(`ws://127.0.0.1:${gatewayPort}/v1/connect`, {
      t: 'open', v: 1, host: '127.0.0.1', port: onlineTarget.port, srv: false,
      auth: { accessToken: 'expired-token', uuid: UUID, name: 'PremiumPlayer' },
    });
    expired.socket.send(handshakeBytes);
    expired.socket.send(loginBytes);
    const expiredRefusal = await until(() => expired.result.control.some((m) => m.t === 'error'));
    const expiredError = expired.result.control.find((m) => m.t === 'error');
    check('an expired sign-in is refused as a sign-in problem, not as a server problem',
      expiredRefusal && expiredError.code === 'AUTH_FAILED', JSON.stringify(expiredError));
    check('and it is told to sign in again', /sign in again/i.test(expiredError?.msg || ''));
    sessions.state.respond = 204;
    try { expired.socket.close(); } catch { /* already closed */ }

    // ---- an offline server must not be disturbed by any of this ------------------------------------------
    const cracked = await goldProxySession(`ws://127.0.0.1:${gatewayPort}/v1/connect`, {
      t: 'open', v: 1, host: '127.0.0.1', port: offlineTarget.port, srv: false,
    });
    cracked.socket.send(handshakeBytes);
    cracked.socket.send(loginBytes);
    const welcome = await until(() => binaryOf(cracked.result).length > 0);
    check('a cracked server still gets the plain pipe it always got', welcome && binaryOf(cracked.result).includes(Buffer.from('welcome')),
      binaryOf(cracked.result).toString('hex'));
    check('its own login name is the one the client typed, because there is nothing to verify',
      offlineTarget.state.received.includes(Buffer.from(HELLO_NAME)));
    check('and no session server call was made for it', sessions.state.calls.length === 2);
    try { cracked.socket.close(); } catch { /* already closed */ }

    // ---- the same join, over the dialect the game's own client speaks ------------------------------------
    sessions.state.respond = 204;
    const tunnelTarget = await fakeOnlineServer();
    try {
      const tunnel = await tunnelSession(`ws://127.0.0.1:${gatewayPort}/tunnel`, {
        type: 'connect', host: '127.0.0.1', port: tunnelTarget.port,
        auth: { accessToken: 'mc-access-token', uuid: UUID, name: 'PremiumPlayer' },
      });
      check('the tunnel dialect says the join is online',
        tunnel.result.control.find((m) => m.type === 'connected')?.online === true,
        JSON.stringify(tunnel.result.control));
      tunnel.socket.send(handshakeBytes);
      tunnel.socket.send(loginBytes);
      const joined = await until(() => tunnelTarget.state.digest !== null);
      check('a premium join works through the tunnel dialect too', joined);
      check('with the same name rewrite and the same digest the server computed',
        tunnelTarget.state.names[0] === 'PremiumPlayer'
        && sessions.state.calls[2]?.body?.serverId === tunnelTarget.state.digest,
        `${tunnelTarget.state.names[0]} / ${sessions.state.calls[2]?.body?.serverId} vs ${tunnelTarget.state.digest}`);
      check('and the client is handed the decrypted login success',
        await until(() => binaryOf(tunnel.result).toString('utf8').includes('logged-in-payload')),
        binaryOf(tunnel.result).toString('hex').slice(0, 80));
      try { tunnel.socket.close(); } catch { /* already closed */ }
    } finally {
      tunnelTarget.server.close();
    }
  } finally {
    gateway.child.kill('SIGKILL');
    sessions.server.close();
    onlineTarget.server.close();
    offlineTarget.server.close();
  }

  console.log(`\n${checks - failures}/${checks} online-mode gateway checks passed`);
  process.exit(failures ? 1 : 0);
}

main().catch((error) => {
  console.error('the online-mode gateway test crashed:', error);
  process.exit(1);
});
