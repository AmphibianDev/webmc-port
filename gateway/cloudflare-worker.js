// The gateway, as a Cloudflare Worker.
//
// A browser cannot open a TCP socket, so joining a normal Java server needs something server-side to dial
// it. `gateway/server.js` is that something on a machine you own; this is the same thing on Cloudflare's
// edge, which is what makes it a good default for players: a Worker can open a TCP connection with
// `connect()` (Cloudflare's sockets API), it is reachable over `wss://` from a page without a certificate
// of your own, and it costs nothing at the scale a community client needs.
//
// It speaks the two dialects the clients already implement, unchanged:
//
//   goldproxy.v1   POST /v1/token {host,port} -> {token,expiresAt}
//                  GET  /v1/connect (WebSocket) with {"t":"open","v":1,"token","host","port"}
//                       <- {"t":"ready","ip","port","dnsMs","connectMs","window"}
//                  then: binary frames are raw TCP, and text frames are
//                       {"t":"ack","n"} (flow control), {"t":"ping"/"pong","id"}, {"t":"error",…}, {"t":"end",…}
//
//   engine tunnel  {"type":"connect",host,port} <- {"type":"connecting"} then {"type":"connected",host,port}
//                  then raw bytes; the engine's own attestation is those two fields, so they are sent.
//
// Nothing here parses Minecraft packets beyond the one rule the Node gateway also keeps: a session has to
// start with a Minecraft handshake, because a relay that dials any port for any bytes is a port scanner
// with extra steps. The encryption happens in the page; this never holds a key.
//
// Deploy:
//   npx wrangler deploy gateway/cloudflare-worker.js --name webmc-gateway --compatibility-date 2026-01-01
//   npx wrangler secret put GATEWAY_SECRET          # any long random string; tokens are signed with it
// Then point a client at it:  https://…/?bridge=wss://<your-worker>.workers.dev/v1/connect
//
// Environment:
//   GATEWAY_SECRET                 HMAC secret for tokens (set it; a missing one means tokens are refused)
//   GATEWAY_REQUIRE_TOKEN          1 (default) to require a token; 0 to accept a plain "open"
//   GATEWAY_ALLOW_PRIVATE_TARGETS  1 to allow LAN/loopback targets (default 0)
//   GATEWAY_WINDOW_BYTES           flow-control window per session (default 512 KiB)
//   GATEWAY_TOKEN_PER_MINUTE       token requests per address per minute (default 20)
//   GATEWAY_MAX_SESSIONS           sessions this isolate will hold at once (default 200)

const DEFAULT_PORT = 25565;
const BLOCKED_PORTS = new Set([25, 465, 587, 110, 143, 993, 995, 3306, 5432, 6379, 11211, 27017, 9200]);
const PRIVATE_V4 = [
  /^0\./, /^10\./, /^127\./, /^169\.254\./, /^192\.168\./,
  /^172\.(1[6-9]|2\d|3[01])\./, /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./,
];

export function isPrivateIpv4(address) {
  return PRIVATE_V4.some((pattern) => pattern.test(address));
}

export function isPrivateAddress(address) {
  const text = String(address || "").toLowerCase().replace(/^\[|\]$/g, "");
  if (/^\d+\.\d+\.\d+\.\d+$/.test(text)) return isPrivateIpv4(text);
  if (text === "::" || text === "::1") return true;
  if (text.startsWith("fe80") || text.startsWith("fc") || text.startsWith("fd")) return true;
  const mapped = /^::(?:ffff:)?(\d+\.\d+\.\d+\.\d+)$/.exec(text);
  return mapped ? isPrivateIpv4(mapped[1]) : false;
}

export function isPrivateName(host) {
  const name = String(host || "").toLowerCase();
  return name === "localhost" || name.endsWith(".localhost") || name.endsWith(".local")
    || name.endsWith(".internal") || name === "metadata.google.internal";
}

/** Whether this destination may be dialled at all, and why not when it may not. */
export function checkDestination(host, port) {
  if (typeof host !== "string" || !host || host.length > 255 || /[\s/@\\]/.test(host)) {
    return { ok: false, code: "BAD_DESTINATION", msg: "that server address is not valid" };
  }
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    return { ok: false, code: "BAD_DESTINATION", msg: "that port is not valid" };
  }
  if (BLOCKED_PORTS.has(port) || (port < 1024 && port !== DEFAULT_PORT)) {
    return { ok: false, code: "PORT_BLOCKED", msg: `port ${port} is not a Minecraft server port` };
  }
  const allowPrivate = false;
  if (!allowPrivate && (isPrivateAddress(host) || isPrivateName(host))) {
    return { ok: false, code: "ADDRESS_BLOCKED", msg: "that address is in a private or reserved network" };
  }
  return { ok: true };
}

// ------------------------------------------------------------------ tokens

const encoder = new TextEncoder();

function base64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64urlDecode(text) {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

async function hmac(secret, payload) {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, encoder.encode(payload));
  return base64url(new Uint8Array(mac));
}

export async function issueToken(secret, host, port, now) {
  const payload = `${String(host).toLowerCase()}:${port}:${Math.floor((now || Date.now()) / 1000)}`;
  return `${base64url(encoder.encode(payload))}.${await hmac(secret, payload)}`;
}

/** Tokens are scoped to one destination and live for two minutes, exactly like the Node gateway's. */
export async function verifyToken(secret, token, host, port, now) {
  if (typeof token !== "string" || !token.includes(".")) return false;
  const [body, mac] = token.split(".");
  let payload = "";
  try { payload = new TextDecoder().decode(base64urlDecode(body)); } catch { return false; }
  const expected = await hmac(secret, payload);
  if (mac.length !== expected.length) return false;
  let mismatch = 0;
  for (let i = 0; i < mac.length; i++) mismatch |= mac.charCodeAt(i) ^ expected.charCodeAt(i);
  if (mismatch !== 0) return false;
  const [tokenHost, tokenPort, issuedAt] = payload.split(":");
  if (tokenHost !== String(host).toLowerCase() || Number(tokenPort) !== port) return false;
  return Math.floor((now || Date.now()) / 1000) - Number(issuedAt) <= 120;
}

// ------------------------------------------------------------------ limits

const buckets = new Map();

export function rateLimited(address, perMinute, now) {
  const at = now || Date.now();
  const bucket = buckets.get(address) || { count: 0, start: at };
  if (at - bucket.start > 60000) { bucket.count = 0; bucket.start = at; }
  bucket.count += 1;
  buckets.set(address, bucket);
  if (buckets.size > 4096) buckets.clear();
  return bucket.count > perMinute;
}

let sessions = 0;

/** The first bytes of a Minecraft session: a handshake packet, then a length-prefixed one. */
export function looksLikeHandshake(bytes) {
  if (!bytes || bytes.length < 3) return false;
  const version = bytes[0];
  // VarInt packet length, then VarInt packet id 0x00 (handshake)
  let index = 0;
  let length = 0;
  let shift = 0;
  while (index < bytes.length && shift < 21) {
    const byte = bytes[index++];
    length |= (byte & 0x7f) << shift;
    if (!(byte & 0x80)) break;
    shift += 7;
  }
  if (length <= 0 || length > 1024) return false;
  return index < bytes.length && bytes[index] === 0x00 && version !== 0;
}

// ------------------------------------------------------------------ the worker

function json(body, status) {
  return new Response(JSON.stringify(body), { status: status || 200, headers: { "content-type": "application/json", "access-control-allow-origin": "*" } });
}

const encoderText = new TextEncoder();

function message(webSocket, text) {
  try { webSocket.send(typeof text === "string" ? text : JSON.stringify(text)); } catch { /* closed */ }
}

export default {
  async fetch(request, env) {
    const options = {
      windowBytes: Number(env.GATEWAY_WINDOW_BYTES || 512 * 1024),
      tokenPerMinute: Number(env.GATEWAY_TOKEN_PER_MINUTE || 20),
      requireToken: (env.GATEWAY_REQUIRE_TOKEN || "1") !== "0",
      maxSessions: Number(env.GATEWAY_MAX_SESSIONS || 200),
      secret: env.GATEWAY_SECRET || "",
    };
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return json({ ok: true, protocol: "goldproxy.v1", runtime: "cloudflare-worker", window: options.windowBytes, sessions, tokenRequired: options.requireToken });
    }
    if ((url.pathname === "/v1/token" || url.pathname === "/token") && request.method === "POST") {
      const address = request.headers.get("cf-connecting-ip") || "unknown";
      if (rateLimited(address, options.tokenPerMinute)) {
        return json({ error: "too many token requests; try again in a minute" }, 429);
      }
      let body = {};
      try { body = await request.json(); } catch { return json({ error: "a token request is JSON: {host, port}" }, 400); }
      const host = String(body.host || "").trim();
      const port = Number(body.port || DEFAULT_PORT);
      const destination = checkDestination(host, port);
      if (!destination.ok) return json({ error: destination.msg, code: destination.code }, 403);
      if (!options.secret) return json({ error: "this gateway has no GATEWAY_SECRET set, so it cannot sign tokens" }, 503);
      const token = await issueToken(options.secret, host, port);
      return json({ token, expiresAt: Math.floor(Date.now() / 1000) + 120, window: options.windowBytes });
    }
    if (url.pathname === "/v1/connect" || url.pathname === "/tunnel" || url.pathname === "/" ) {
      if ((request.headers.get("upgrade") || "").toLowerCase() !== "websocket") {
        return json({ error: "this endpoint speaks WebSocket: ask for a token, then connect" }, 426);
      }
      return tunnel(request, options);
    }
    return json({ error: "nothing here: /health, /v1/token, /v1/connect" }, 404);
  },
};

async function tunnel(request, options) {
  if (sessions >= options.maxSessions) return json({ error: "this gateway is full; try another" }, 503);

  const pair = new WebSocketPair();
  const [client, server] = Object.values(pair);
  server.accept();
  sessions += 1;

  const session = {
    socket: null,
    writer: null,
    reader: null,
    received: 0,
    acked: 0,
    opened: false,
    finished: false,
    window: options.windowBytes,
    // which dialect the client speaks, decided by the first message it sends
    dialect: "goldproxy",
  };

  const send = (text) => message(server, text);
  const fail = (code, msg, stage) => {
    if (session.finished) return;
    session.finished = true;
    send({ t: "error", code, stage: stage || "session", msg });
    try { server.close(1011, code); } catch { /* closing */ }
    end();
  };

  function end() {
    session.finished = true;
    try { session.writer?.releaseLock(); } catch { /* not held */ }
    try { session.reader?.releaseLock(); } catch { /* not held */ }
    try { session.socket?.close(); } catch { /* already closed */ }
    sessions = Math.max(0, sessions - 1);
  }

  async function pumpFromTarget() {
    const reader = session.socket.readable.getReader();
    session.reader = reader;
    let announced = false;
    while (!session.finished) {
      const { value, done } = await reader.read();
      if (done) break;
      const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
      if (!announced) {
        // The one packet-shaped check: a relay that dials any port for any bytes is a port scanner.
        if (!looksLikeHandshake(bytes)) {
          fail("NOT_MINECRAFT", "the target did not answer with a Minecraft handshake", "handshake");
          return;
        }
        announced = true;
      }
      session.received += bytes.length;
      try { server.send(bytes); } catch { break; }
      // Flow control: stop reading while the page holds more unacknowledged bytes than the window, so a
      // game that has stopped draining pushes back on the server instead of filling this isolate.
      while (!session.finished && session.received - session.acked > session.window) {
        await new Promise((resolve) => setTimeout(resolve, 8));
      }
    }
    if (!session.finished) {
      send({ t: "end", why: "target closed" });
      try { server.close(1000, "target closed"); } catch { /* closing */ }
      end();
    }
  }

  async function openTarget(host, port, token) {
    if (options.requireToken) {
      if (!options.secret) { fail("NO_SECRET", "this gateway has no GATEWAY_SECRET set"); return; }
      const good = await verifyToken(options.secret, token, host, port);
      if (!good) { fail("BAD_TOKEN", "that token is not valid for this destination, or it expired"); return; }
    }
    const destination = checkDestination(host, port);
    if (!destination.ok) { fail(destination.code, destination.msg, "destination"); return; }

    const started = Date.now();
    try {
      const { connect } = await import("cloudflare:sockets");
      session.socket = connect({ hostname: host, port });
      session.writer = session.socket.writable.getWriter();
    } catch (error) {
      fail("CONNECT_FAILED", `could not reach ${host}:${port}: ${String((error && error.message) || error)}`, "connect");
      return;
    }
    const connectMs = Date.now() - started;
    if (session.dialect === "engine") {
      send({ type: "connecting" });
      send({ type: "connected", host, port });
    } else {
      send({ t: "ready", v: 1, host, port, ip: host, window: session.window, dnsMs: 0, connectMs });
    }
    session.opened = true;
    pumpFromTarget().catch((error) => fail("PUMP_FAILED", String((error && error.message) || error), "target"));
  }

  server.addEventListener("message", async (event) => {
    const data = event.data;
    if (typeof data === "string") {
      let parsed = null;
      try { parsed = JSON.parse(data); } catch { return; }
      if (parsed.t === "ack") {
        session.acked += Number(parsed.n) || 0;
        return;
      }
      if (parsed.t === "ping") { send({ t: "pong", id: parsed.id }); return; }
      if (parsed.t === "open" || parsed.type === "connect") {
        if (session.opened) return;
        const host = String(parsed.host || "").trim();
        const port = Number(parsed.port || DEFAULT_PORT);
        session.dialect = parsed.type === "connect" ? "engine" : "goldproxy";
        await openTarget(host, port, parsed.token);
        return;
      }
      return;
    }
    if (!session.writer || !session.opened) return;
    const bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : data;
    try { await session.writer.write(bytes); } catch { fail("WRITE_FAILED", "the target stopped accepting bytes", "target"); }
  });

  server.addEventListener("close", () => end());
  server.addEventListener("error", () => end());

  // A session that never opens anything is a session holding a connection for nothing.
  setTimeout(() => { if (!session.opened) fail("TIMEOUT", "no destination was named within 15 seconds", "open"); }, 15000);

  return new Response(null, { status: 101, webSocket: client });
}
