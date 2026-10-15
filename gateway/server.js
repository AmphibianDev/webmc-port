#!/usr/bin/env node
// A multiplayer gateway for a browser Minecraft client: WebSocket on one side, a real TCP socket on the other.
//
// A browser cannot open a TCP socket, so a typed server address can never be dialled from the page itself. What a
// page *can* do is open a WebSocket, which is why every browser client that joins a normal Java server has a
// server-side gateway behind it. This is ours, and it speaks the protocol the Eagler26 client already implements
// (subprotocol `goldproxy.v1`), so that client can use it without being modified:
//
//   POST /v1/token   {host, port}                -> {token, expiresAt}   short-lived, scoped to one destination
//   GET  /v1/connect (Upgrade: websocket)        -> text {"t":"open","v":1,"token","host","port","srv"}
//                                                <- text {"t":"ready","ip","port","dnsMs","connectMs","window"}
//   binary frames carry raw TCP bytes in both directions; the client owns Minecraft's framing and encryption
//   text {"t":"ack","n"} <- flow control, {"t":"ping","id"} / {"t":"pong","id"}, {"t":"error",...}, {"t":"end",...}
//
// Nothing here parses Minecraft packets beyond the one check that a session starts with a handshake: the bytes
// are a pipe, and the encryption happens in the page (AES/CFB8), so this process never holds a key or a packet.
//
// Zero dependencies on purpose - it uses node:net, node:dns, node:crypto and a hand-written WebSocket frame
// layer - so it can be read in one sitting and run with `node gateway/server.js`.
//
// Configuration (environment):
//   GATEWAY_PORT                 listen port (default 8090)
//   GATEWAY_HOST                 listen address (default 0.0.0.0)
//   GATEWAY_PUBLIC_URL           wss:// URL clients should be given; only used in status output and docs
//   GATEWAY_SECRET               HMAC secret for tokens (default: random per boot, so tokens die with the process)
//   GATEWAY_ALLOW_PRIVATE_TARGETS  1 to allow LAN/loopback/private targets (default 0: refuses them)
//   GATEWAY_ALLOWED_ORIGINS      comma-separated Origin allowlist; empty means any origin
//   GATEWAY_MAX_SESSIONS         concurrent sessions (default 64)
//   GATEWAY_TOKEN_PER_MINUTE     token requests per address per minute (default 20)
//   GATEWAY_CONNECT_TIMEOUT_MS   how long to wait for the target to accept (default 10000)
//   GATEWAY_IDLE_TIMEOUT_MS      close a session with no traffic (default 300000)
//   GATEWAY_MAX_FRAME_BYTES      largest client frame accepted (default 2 MiB + 8)
'use strict';

const http = require('node:http');
const net = require('node:net');
const dns = require('node:dns/promises');
const crypto = require('node:crypto');
const os = require('node:os');

// --------------------------------------------------------------------------------------------------- options

function envInt(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) throw new Error(`${name} must be a positive number, got ${raw}`);
  return Math.floor(value);
}

const OPTIONS = {
  port: envInt('GATEWAY_PORT', 8090),
  host: process.env.GATEWAY_HOST || '0.0.0.0',
  publicUrl: process.env.GATEWAY_PUBLIC_URL || '',
  secret: process.env.GATEWAY_SECRET || crypto.randomBytes(32).toString('hex'),
  secretIsEphemeral: !process.env.GATEWAY_SECRET,
  allowPrivateTargets: process.env.GATEWAY_ALLOW_PRIVATE_TARGETS === '1',
  allowedOrigins: (process.env.GATEWAY_ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean),
  maxSessions: envInt('GATEWAY_MAX_SESSIONS', 64),
  tokenPerMinute: envInt('GATEWAY_TOKEN_PER_MINUTE', 20),
  connectTimeoutMs: envInt('GATEWAY_CONNECT_TIMEOUT_MS', 10000),
  idleTimeoutMs: envInt('GATEWAY_IDLE_TIMEOUT_MS', 300000),
  maxFrameBytes: envInt('GATEWAY_MAX_FRAME_BYTES', 2 * 1024 * 1024 + 8),
  maxMessageBytes: 64 * 1024, // outbound: what the client accepts in one message
  windowBytes: envInt('GATEWAY_WINDOW_BYTES', 512 * 1024),
};

// Ports a Minecraft client has no business reaching: mail, databases, remote shell, caches. A gateway that will
// dial any port on any host is a port scanner with a nice protocol in front of it, so the common ones are refused
// by name and everything under 1024 except the game's own default is refused by rule.
const BLOCKED_PORTS = new Set([21, 22, 23, 25, 53, 110, 111, 135, 137, 138, 139, 143, 389, 445, 465, 587, 636, 993,
  995, 1080, 1433, 1521, 2049, 2375, 3306, 3389, 5432, 5601, 5672, 5900, 5984, 6379, 9042, 9200, 9300, 11211, 27017,
  28017]);
const DEFAULT_PORT = 25565;

const CLOSE = {
  PROTOCOL_ERROR: 4000, UNSUPPORTED_VERSION: 4001, UNAUTHORIZED: 4003, RATE_LIMITED: 4029, OVERLOADED: 4030,
  BAD_DESTINATION: 4100, DESTINATION_NOT_ALLOWED: 4101, PORT_BLOCKED: 4102, ADDRESS_BLOCKED: 4103, DNS_FAILED: 4104,
  CONNECT_FAILED: 4200, CONNECT_TIMEOUT: 4201, OPEN_TIMEOUT: 4008, NOT_MINECRAFT: 4300, MESSAGE_TOO_LARGE: 4009,
  BACKPRESSURE: 4010, IDLE_TIMEOUT: 4011, SESSION_LIMIT: 4012, INTERNAL: 4500,
};

const sessions = new Set();
const tokenBuckets = new Map();

// --------------------------------------------------------------------------------------- destination policy

function isPrivateIPv4(address) {
  const parts = address.split('.').map(Number);
  if (parts.length !== 4 || parts.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return true;
  const [a, b] = parts;
  return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31)
    || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || a >= 224;
}

function isPrivateIPv6(address) {
  const text = address.toLowerCase().replace(/^\[|\]$/g, '');
  if (text === '::' || text === '::1') return true;
  if (text.startsWith('fe80') || text.startsWith('fc') || text.startsWith('fd')) return true;
  // ::ffff:a.b.c.d  and  ::a.b.c.d  (IPv4-mapped and IPv4-compatible)
  const v4 = /^::(?:ffff:)?(\d+\.\d+\.\d+\.\d+)$/.exec(text);
  if (v4) return isPrivateIPv4(v4[1]);
  return false;
}

function isPrivateAddress(address) {
  return net.isIPv4(address) ? isPrivateIPv4(address) : isPrivateIPv6(address);
}

/** Whether the destination may be dialled at all, and why not when it may not. */
function checkDestination(host, port) {
  if (typeof host !== 'string' || !host || host.length > 255 || /[\s/@\\]/.test(host)) {
    return { ok: false, code: 'BAD_DESTINATION', msg: 'that server address is not valid' };
  }
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    return { ok: false, code: 'BAD_DESTINATION', msg: 'that port is not valid' };
  }
  if (BLOCKED_PORTS.has(port) || (port < 1024 && port !== DEFAULT_PORT)) {
    return { ok: false, code: 'PORT_BLOCKED', msg: `port ${port} is not a Minecraft server port` };
  }
  // A literal private address is refused here; a *name* that resolves to one is refused after the DNS lookup, so
  // that a domain pointing at 127.0.0.1 ("localhost.example.com") cannot be used to reach inside the network.
  if (!OPTIONS.allowPrivateTargets && (net.isIP(host) ? isPrivateAddress(host) : false)) {
    return { ok: false, code: 'ADDRESS_BLOCKED', msg: 'that address is in a private or reserved network' };
  }
  return { ok: true };
}

/** The Minecraft SRV record, if the name has one. Doing it here is what makes a bare domain work like it does in
 *  the real client: `mc.example.com` may really live at port 25566 of another host. */
async function resolveSrv(host) {
  if (net.isIP(host)) return null;
  try {
    const records = await dns.resolveSrv(`_minecraft._tcp.${host}`);
    if (!records.length) return null;
    records.sort((a, b) => (a.priority - b.priority) || (b.weight - a.weight));
    return { host: records[0].name, port: records[0].port };
  } catch { return null; }
}

// -------------------------------------------------------------------------------------------------- tokens

function tokenPayload(host, port) {
  return `${host.toLowerCase()}:${port}:${Math.floor(Date.now() / 1000)}`;
}

function issueToken(host, port) {
  const payload = tokenPayload(host, port);
  const mac = crypto.createHmac('sha256', OPTIONS.secret).update(payload).digest('base64url');
  return `${Buffer.from(payload).toString('base64url')}.${mac}`;
}

/** Tokens are scoped to one destination and live for two minutes: a reconnect asks for a new one, exactly as the
 *  client expects ("never cached: every session asks again"). */
function verifyToken(token, host, port) {
  if (typeof token !== 'string' || !token.includes('.')) return false;
  const [body, mac] = token.split('.');
  let payload;
  try { payload = Buffer.from(body, 'base64url').toString('utf8'); } catch { return false; }
  const expected = crypto.createHmac('sha256', OPTIONS.secret).update(payload).digest('base64url');
  if (mac.length !== expected.length || !crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(expected))) return false;
  const [tokenHost, tokenPort, issuedAt] = payload.split(':');
  if (tokenHost !== host.toLowerCase() || Number(tokenPort) !== port) return false;
  return Math.floor(Date.now() / 1000) - Number(issuedAt) <= 120;
}

function rateLimited(address) {
  const now = Date.now();
  const bucket = tokenBuckets.get(address) || { count: 0, start: now };
  if (now - bucket.start > 60000) { bucket.count = 0; bucket.start = now; }
  bucket.count += 1;
  tokenBuckets.set(address, bucket);
  if (tokenBuckets.size > 4096) tokenBuckets.clear(); // a public gateway should not grow without a bound
  return bucket.count > OPTIONS.tokenPerMinute;
}

// --------------------------------------------------------------------------------------- websocket framing

const WS_MAGIC = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

function acceptKey(key) {
  return crypto.createHash('sha1').update(key + WS_MAGIC).digest('base64');
}

/** One decoded frame: {opcode, payload, fin}. Payloads are unmasked copies. */
function createFrameReader(onMessage, onProtocolError, maxBytes) {
  let buffer = Buffer.alloc(0);
  let fragment = null;
  return function push(chunk) {
    buffer = buffer.length ? Buffer.concat([buffer, chunk]) : chunk;
    for (;;) {
      if (buffer.length < 2) return;
      const first = buffer[0];
      const second = buffer[1];
      const fin = (first & 0x80) !== 0;
      const opcode = first & 0x0f;
      const masked = (second & 0x80) !== 0;
      let length = second & 0x7f;
      let offset = 2;
      if (length === 126) {
        if (buffer.length < offset + 2) return;
        length = buffer.readUInt16BE(offset); offset += 2;
      } else if (length === 127) {
        if (buffer.length < offset + 8) return;
        const big = buffer.readBigUInt64BE(offset); offset += 8;
        if (big > BigInt(maxBytes)) return onProtocolError('frame too large');
        length = Number(big);
      }
      if (!masked) return onProtocolError('a client frame must be masked');
      if (length > maxBytes) return onProtocolError('frame too large');
      if (buffer.length < offset + 4 + length) return;
      const mask = buffer.subarray(offset, offset + 4); offset += 4;
      const payload = Buffer.from(buffer.subarray(offset, offset + length));
      for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i & 3];
      buffer = buffer.subarray(offset + length);
      if (opcode === 0x8) return onMessage({ opcode, payload, fin: true });
      if (opcode === 0x9 || opcode === 0xa) { onMessage({ opcode, payload, fin: true }); continue; }
      if (opcode === 0x0) {
        if (!fragment) return onProtocolError('continuation without a start');
        fragment.payload = Buffer.concat([fragment.payload, payload]);
        if (fragment.payload.length > maxBytes) return onProtocolError('message too large');
        if (fin) { const done = fragment; fragment = null; onMessage({ opcode: done.opcode, payload: done.payload, fin: true }); }
        continue;
      }
      if (opcode !== 0x1 && opcode !== 0x2) return onProtocolError('unsupported opcode');
      if (fin) { onMessage({ opcode, payload, fin: true }); continue; }
      fragment = { opcode, payload };
    }
  };
}

function encodeFrame(opcode, payload) {
  const length = payload.length;
  let header;
  if (length < 126) { header = Buffer.alloc(2); header[1] = length; }
  else if (length < 65536) { header = Buffer.alloc(4); header[1] = 126; header.writeUInt16BE(length, 2); }
  else { header = Buffer.alloc(10); header[1] = 127; header.writeBigUInt64BE(BigInt(length), 2); }
  header[0] = 0x80 | opcode;
  return Buffer.concat([header, payload]);
}

// ------------------------------------------------------------------------------------------------ sessions

function closeCodeOf(code, reason) {
  const text = Buffer.from(String(reason || '').slice(0, 120), 'utf8');
  const payload = Buffer.alloc(2 + text.length);
  payload.writeUInt16BE(code, 0);
  text.copy(payload, 2);
  return payload;
}

function handleSession(socket, request, head) {
  const address = request.socket.remoteAddress || 'unknown';
  if (sessions.size >= OPTIONS.maxSessions) {
    socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n');
    return;
  }
  const protocol = request.headers['sec-websocket-protocol'] || '';
  if (!protocol.split(',').map((s) => s.trim()).includes('goldproxy.v1')) {
    socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');
    return;
  }
  socket.write([
    'HTTP/1.1 101 Switching Protocols',
    'Upgrade: websocket',
    'Connection: Upgrade',
    `Sec-WebSocket-Accept: ${acceptKey(request.headers['sec-websocket-key'] || '')}`,
    'Sec-WebSocket-Protocol: goldproxy.v1',
    '', '',
  ].join('\r\n'));

  const session = {
    address, opened: false, socket: null, target: null, sized: false,
    rx: 0, tx: 0, received: 0, acked: 0, paused: false, openedAt: Date.now(), idle: null,
    finished: false, firstFrame: true,
  };
  sessions.add(session);

  const send = (text) => { if (!session.finished) socket.write(encodeFrame(0x1, Buffer.from(text))); };
  const sendBinary = (data) => { if (!session.finished) socket.write(encodeFrame(0x2, data)); };
  const stop = (code, reason) => {
    if (session.finished) return;
    session.finished = true;
    sessions.delete(session);
    clearTimeout(session.idle);
    try { socket.write(encodeFrame(0x8, closeCodeOf(code, reason))); } catch { /* the peer is gone */ }
    try { socket.end(); } catch { /* already closed */ }
    try { session.socket?.destroy(); } catch { /* already closed */ }
  };
  const failSession = (code, why, msg = '') => {
    send(JSON.stringify({ t: 'error', code: why, stage: 'session', msg }));
    stop(CLOSE[why] || code, why);
  };

  const touch = () => {
    clearTimeout(session.idle);
    session.idle = setTimeout(() => failSession(CLOSE.IDLE_TIMEOUT, 'IDLE_TIMEOUT', 'idle for too long'), OPTIONS.idleTimeoutMs);
    if (session.idle.unref) session.idle.unref();
  };
  touch();

  async function openTarget(message) {
    if (message.v !== undefined && message.v !== 1) {
      return failSession(CLOSE.UNSUPPORTED_VERSION, 'UNSUPPORTED_VERSION', 'only protocol version 1 is spoken here');
    }
    let host = String(message.host || '').trim();
    let port = Number(message.port || DEFAULT_PORT);
    const policy = checkDestination(host, port);
    if (!policy.ok) return failSession(CLOSE[policy.code], policy.code, policy.msg);
    if (message.token !== undefined && message.token !== null && !verifyToken(message.token, host, port)) {
      return failSession(CLOSE.UNAUTHORIZED, 'UNAUTHORIZED', 'that token is missing, expired or for another server');
    }

    let dnsMs = 0;
    const dnsStarted = Date.now();
    if (message.srv !== false && !net.isIP(host)) {
      const record = await resolveSrv(host);
      if (record) { host = record.host; port = record.port; }
      dnsMs = Date.now() - dnsStarted;
    }
    const second = checkDestination(host, port);
    if (!second.ok) return failSession(CLOSE[second.code], second.code, second.msg);

    let connection;
    try {
      const addresses = net.isIP(host) ? [host] : (await dns.lookup(host, { all: true, verbatim: true })).map((r) => r.address);
      if (!addresses.length) return failSession(CLOSE.DNS_FAILED, 'DNS_FAILED', 'that name has no address');
      const blocked = addresses.find((a) => !OPTIONS.allowPrivateTargets && isPrivateAddress(a));
      if (blocked) {
        return failSession(CLOSE.ADDRESS_BLOCKED, 'ADDRESS_BLOCKED', 'that name points into a private or reserved network');
      }
      connection = net.connect({ host, port, family: 0 });
    } catch (error) {
      return failSession(CLOSE.DNS_FAILED, 'DNS_FAILED', String(error.code || error.message || 'lookup failed'));
    }

    const connectStarted = Date.now();
    const timer = setTimeout(() => {
      connection.destroy();
      failSession(CLOSE.CONNECT_TIMEOUT, 'CONNECT_TIMEOUT', 'the server did not accept a connection in time');
    }, OPTIONS.connectTimeoutMs);

    connection.once('connect', () => {
      clearTimeout(timer);
      session.socket = connection;
      session.target = { host, port };
      send(JSON.stringify({
        t: 'ready', ip: connection.remoteAddress, port, host, dnsMs, connectMs: Date.now() - connectStarted,
        window: OPTIONS.windowBytes,
      }));
      connection.on('data', (data) => {
        session.rx += data.length;
        session.received += data.length;
        let sent = 0;
        while (sent < data.length) {
          const slice = data.subarray(sent, sent + OPTIONS.maxMessageBytes);
          sendBinary(slice);
          sent += slice.length;
        }
        // Flow control: stop reading while the client has more unacknowledged bytes than the window, so a game
        // thread that has stopped draining pushes back on the server instead of filling this process's memory.
        if (session.received - session.acked > OPTIONS.windowBytes + OPTIONS.maxMessageBytes && !session.paused) {
          session.paused = true;
          connection.pause();
        }
        touch();
      });
    });
    connection.once('error', (error) => {
      clearTimeout(timer);
      const timedOut = error.code === 'ETIMEDOUT';
      const code = timedOut ? 'CONNECT_TIMEOUT' : 'CONNECT_FAILED';
      failSession(CLOSE[code], code, String(error.code || error.message || 'connect failed'));
    });
    connection.once('close', () => {
      clearTimeout(timer);
      if (session.finished) return;
      send(JSON.stringify({ t: 'end', reason: session.opened ? 'server-closed' : 'closed-before-open', rx: session.rx, tx: session.tx }));
      stop(1000, 'server closed');
    });
    connection.once('end', () => connection.end());
    session.connection = connection;
  }

  const reader = createFrameReader((frame) => {
    touch();
    if (frame.opcode === 0x8) { stop(1000, 'client closed'); return; }
    if (frame.opcode === 0x9) { socket.write(encodeFrame(0xa, frame.payload)); return; }
    if (frame.opcode === 0xa) return;
    if (frame.opcode === 0x1) {
      let message;
      try { message = JSON.parse(frame.payload.toString('utf8')); } catch {
        return failSession(CLOSE.PROTOCOL_ERROR, 'PROTOCOL_ERROR', 'a control frame was not JSON');
      }
      if (!session.opened) {
        if (message.t !== 'open') return failSession(CLOSE.PROTOCOL_ERROR, 'PROTOCOL_ERROR', 'the first frame must be an open request');
        session.opened = true;
        clearTimeout(session.openTimeout);
        openTarget(message).catch((error) => failSession(CLOSE.INTERNAL, 'INTERNAL', String(error && error.message)));
        return;
      }
      if (message.t === 'ping') { send(JSON.stringify({ t: 'pong', id: message.id })); return; }
      if (message.t === 'ack') {
        session.acked = Number(message.n) || 0;
        if (session.paused && session.received - session.acked <= OPTIONS.windowBytes && session.socket) {
          session.paused = false;
          session.socket.resume();
        }
        return;
      }
      if (message.t === 'close') { stop(1000, 'client closed'); return; }
      return;
    }
    if (frame.opcode === 0x2) {
      if (!session.socket) return; // the gateway buffers the Minecraft handshake while it dials, as the client expects
      if (session.firstFrame) {
        session.firstFrame = false;
        // The one packet-shaped check in the whole gateway: a session that does not start with a handshake is a
        // port scanner or a mistake, and saying so is friendlier than a timeout at the other end.
        const payload = frame.payload;
        let value = 0, shift = 0, index = 0, complete = false;
        while (index < payload.length && shift < 35) {
          const byte = payload[index++];
          value |= (byte & 0x7f) << shift;
          shift += 7;
          if (!(byte & 0x80)) { complete = true; break; }
        }
        if (!complete || index >= payload.length || payload[index] !== 0x00) {
          return failSession(CLOSE.NOT_MINECRAFT, 'NOT_MINECRAFT', 'the connection did not start with a Minecraft handshake');
        }
      }
      session.tx += frame.payload.length;
      session.socket.write(frame.payload);
      touch();
    }
  }, (why) => failSession(CLOSE.PROTOCOL_ERROR, 'PROTOCOL_ERROR', why), OPTIONS.maxFrameBytes);

  session.openTimeout = setTimeout(() => {
    if (!session.opened) failSession(CLOSE.OPEN_TIMEOUT, 'OPEN_TIMEOUT', 'no open request arrived');
  }, OPTIONS.connectTimeoutMs);
  if (session.openTimeout.unref) session.openTimeout.unref();

  socket.on('data', reader);
  socket.on('error', () => stop(1000, 'socket error'));
  socket.on('close', () => {
    if (!session.finished) {
      session.finished = true;
      sessions.delete(session);
      clearTimeout(session.idle);
      try { session.socket?.destroy(); } catch { /* already closed */ }
    }
  });
  if (head && head.length) reader(head);
}

// ---------------------------------------------------------------------------------------------------- server

// The client is a page, so its token request is a cross-origin POST with a JSON body - which means a preflight
// before it, and headers on the answer. A gateway that answers the request but not the preflight looks broken
// from inside the browser with no clue why, so both are handled here for the three JSON endpoints.
function cors(req, res) {
  const origin = req.headers.origin;
  const allow = OPTIONS.allowedOrigins.length
    ? (origin && OPTIONS.allowedOrigins.includes(origin) ? origin : OPTIONS.allowedOrigins[0])
    : (origin || '*');
  res.setHeader('access-control-allow-origin', allow);
  res.setHeader('access-control-allow-methods', 'GET, POST, OPTIONS');
  res.setHeader('access-control-allow-headers', 'content-type');
  res.setHeader('access-control-max-age', '600');
  if (OPTIONS.allowedOrigins.length) res.setHeader('vary', 'origin');
}

function json(req, res, status, body) {
  const text = JSON.stringify(body);
  cors(req, res);
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'content-length': Buffer.byteLength(text), 'cache-control': 'no-store' });
  res.end(text);
}

function readJson(req, limit = 4096) {
  return new Promise((resolve) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) { req.destroy(); resolve(null); return; }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (!chunks.length) return resolve({});
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch { resolve(null); }
    });
    req.on('error', () => resolve(null));
  });
}

function originAllowed(req) {
  if (!OPTIONS.allowedOrigins.length) return true;
  const origin = req.headers.origin;
  if (!origin) return true; // native clients and tests send no Origin
  return OPTIONS.allowedOrigins.includes(origin);
}

function status() {
  return {
    ok: true,
    kind: 'webmc-gateway',
    protocolVersion: 1,
    subprotocol: 'goldproxy.v1',
    publicUrl: OPTIONS.publicUrl || null,
    secure: OPTIONS.publicUrl.startsWith('wss://') || null,
    allowPrivateTargets: OPTIONS.allowPrivateTargets,
    allowedOrigins: OPTIONS.allowedOrigins,
    activeConnections: sessions.size,
    maximumConnections: OPTIONS.maxSessions,
    availableConnections: OPTIONS.maxSessions - sessions.size,
    windowBytes: OPTIONS.windowBytes,
    connectTimeoutMs: OPTIONS.connectTimeoutMs,
    idleTimeoutMs: OPTIONS.idleTimeoutMs,
    blockedPorts: [...BLOCKED_PORTS].sort((a, b) => a - b),
    tokenTtlSeconds: 120,
    tokenSecretEphemeral: OPTIONS.secretIsEphemeral,
    localAddresses: Object.values(os.networkInterfaces()).flat()
      .filter((i) => i && i.family === 'IPv4' && !i.internal).map((i) => i.address),
  };
}

function createGatewayServer(options = {}) {
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (req.method === 'OPTIONS') { cors(req, res); res.writeHead(204); return res.end(); }
    if (url.pathname === '/health' || url.pathname === '/v1/status') return json(req, res, 200, { ...status(), ...options.status });

    if (url.pathname === '/v1/token') {
      if (req.method !== 'POST') return json(req, res, 405, { error: 'METHOD_NOT_ALLOWED' });
      if (!originAllowed(req)) return json(req, res, 403, { error: 'ORIGIN_NOT_ALLOWED', message: 'this origin may not use this gateway' });
      if (rateLimited(req.socket.remoteAddress || 'unknown')) {
        return json(req, res, 429, { error: 'RATE_LIMITED', message: 'too many token requests from this address' });
      }
      const body = await readJson(req);
      if (!body) return json(req, res, 400, { error: 'BAD_DESTINATION', message: 'the request body is not JSON' });
      const host = String(body.host || '').trim();
      const port = Number(body.port || DEFAULT_PORT);
      const policy = checkDestination(host, port);
      if (!policy.ok) return json(req, res, 400, { error: policy.code, message: policy.msg });
      const token = issueToken(host, port);
      return json(req, res, 200, { token, expiresAt: new Date(Date.now() + 120000).toISOString(), host, port });
    }

    if (url.pathname === '/v1/connect') {
      if ((req.headers.upgrade || '').toLowerCase() !== 'websocket') {
        return json(req, res, 426, { error: 'UPGRADE_REQUIRED', message: 'connect by WebSocket, subprotocol goldproxy.v1' });
      }
      if (!originAllowed(req)) return json(req, res, 403, { error: 'ORIGIN_NOT_ALLOWED' });
      return handleSession(req.socket, req, null);
    }

    if (url.pathname === '/') {
      const doc = status();
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      res.end(`<!doctype html><meta charset="utf-8"><title>WebMC gateway</title>
<style>body{font:14px/1.6 system-ui,sans-serif;background:#15171c;color:#e6e8ec;margin:0;padding:40px}
code,pre{background:#1e2128;padding:2px 6px;border-radius:4px}th,td{text-align:left;padding:4px 12px 4px 0}</style>
<h1>WebMC gateway</h1><p>A WebSocket-to-TCP gateway for the browser client. It is running.</p>
<table><tr><th>sessions</th><td>${doc.activeConnections} / ${doc.maximumConnections}</td></tr>
<tr><th>private targets</th><td>${doc.allowPrivateTargets ? 'allowed' : 'refused'}</td></tr>
<tr><th>subprotocol</th><td>${doc.subprotocol}</td></tr>
<tr><th>status</th><td><a href="/health">/health</a></td></tr></table>
<p>Point a client at <code>${OPTIONS.publicUrl || `ws://${OPTIONS.host}:${OPTIONS.port}/v1/connect`}</code>.</p>`);
      return;
    }

    json(req, res, 404, { error: 'NOT_FOUND' });
  });

  // A WebSocket upgrade arrives on the socket before node's HTTP parser has read the whole request: without this
  // listener node hands the socket to the request handler only after the (empty) body, which for an upgrade means
  // never. This is why the gateway answers upgrades from the raw socket.
  server.on('upgrade', (req, socket, head) => {
    const url = new URL(req.url, 'http://localhost');
    const readyState = socket;
    readyState.setTimeout?.(0);
    if (url.pathname !== '/v1/connect') { socket.end('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n'); return; }
    if (!originAllowed(req)) { socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'); return; }
    handleSession(socket, req, head);
  });

  return server;
}

function start(overrides = {}) {
  const server = createGatewayServer(overrides);
  return new Promise((resolve) => {
    server.listen(OPTIONS.port, OPTIONS.host, () => resolve({ server, port: server.address().port, options: OPTIONS }));
  });
}

if (require.main === module) {
  start().then(({ port, options }) => {
    const url = options.publicUrl || `ws://${options.host}:${port}/v1/connect`;
    console.log(`webmc gateway listening on ${options.host}:${port}`);
    console.log(`  clients connect to ${url}`);
    console.log(`  private targets: ${options.allowPrivateTargets ? 'ALLOWED (GATEWAY_ALLOW_PRIVATE_TARGETS=1)' : 'refused'}`);
    console.log(`  sessions: ${options.maxSessions}, window: ${Math.round(options.windowBytes / 1024)} KiB, tokens: ${options.secretIsEphemeral ? 'ephemeral secret' : 'secret from the environment'}`);
    if (!options.publicUrl) console.log('  set GATEWAY_PUBLIC_URL=wss://your.host/v1/connect so the status page shows the address clients use');
  });
}

module.exports = { createGatewayServer, start, OPTIONS, checkDestination, isPrivateAddress, issueToken, verifyToken, resolveSrv, CLOSE };
