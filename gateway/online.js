'use strict';
// Online-mode ("premium") login, terminated at the gateway.
//
// A browser client cannot join an online-mode server on its own: joining one means generating a shared secret,
// RSA-encrypting it with the key the server just sent, proving the account to Mojang's session server with the
// hash of that secret, and then speaking AES/CFB8 for the rest of the connection. A page has none of that -
// WebCrypto has no PKCS#1 v1.5 encryption and no CFB8 - and the compiled clients deliberately do not implement
// it, because the encryption they *do* have (for offline servers, none) is not what an online server asks for.
//
// So this module does it here instead, in the one place that already speaks TCP and already sits between the
// page and the server. The browser side is unchanged: it sends its handshake and its login start as always,
// never sees the EncryptionRequest (we answer it), and from the moment the session is encrypted it is talking
// through a pipe that happens to be transcoding AES for it. That is what makes premium servers work with a
// client nobody had to modify - including the prebuilt clients the catalog already points at.
//
// What is deliberately *not* here: the 1.19+ chat-signing key pair. A client proves its identity for chat by
// sending a Mojang-signed profile key; we authenticate the account but cannot sign as it, so servers running
// `enforce-secure-profile=true` (the vanilla default since 1.19.1) will refuse the session. The gateway says so
// in its status and the launcher warns about it, rather than pretending the join will work.

const crypto = require('node:crypto');

// Clientbound login packet ids. Stable from 1.7 through 26.x; the phases after login are not parsed at all.
const LOGIN_DISCONNECT = 0x00;
const LOGIN_ENCRYPTION_REQUEST = 0x01;
const LOGIN_SUCCESS = 0x02;
const LOGIN_COMPRESSION = 0x03;
const LOGIN_PLUGIN_REQUEST = 0x04;

// 1.19 (protocol 759) is the one version whose response carried a has-verify-token boolean between the secret and
// the token; 1.19.1 (760) took it back out, and that is the shape every version since writes. Older servers want
// the old shape, so the handshake's protocol number decides which one is written.
const HAS_VERIFY_TOKEN_PROTOCOL = 759;

// How many login packets to look at before giving up on finding an EncryptionRequest. Vanilla sends one packet
// (or two, behind a proxy that asks a plugin question first); a server that talks longer than this is not
// something this module understands, and the session degrades to the plain byte pipe it was before.
const MAX_LOGIN_FRAMES = 32;

// ----------------------------------------------------------------------------------------------- varints

/** A Minecraft VarInt, or null when the buffer does not hold all of it yet. */
function readVarInt(buffer, offset) {
  let value = 0;
  let shift = 0;
  let index = offset;
  while (index < buffer.length && shift < 35) {
    const byte = buffer[index++];
    value |= (byte & 0x7f) << shift;
    shift += 7;
    if (!(byte & 0x80)) {
      if (value > 0x7fffffff) value -= 0x100000000; // packet ids are positive; lengths never are
      return { value, size: index - offset };
    }
  }
  return null;
}

function writeVarInt(value) {
  const bytes = [];
  let remaining = value >>> 0;
  do {
    let byte = remaining & 0x7f;
    remaining >>>= 7;
    if (remaining !== 0) byte |= 0x80;
    bytes.push(byte);
  } while (remaining !== 0);
  return Buffer.from(bytes);
}

/** A length-prefixed UTF-8 string, or null when it is not all there. */
function readString(buffer, offset) {
  const length = readVarInt(buffer, offset);
  if (!length || length.value < 0) return null;
  const start = offset + length.size;
  const end = start + length.value;
  if (end > buffer.length) return null;
  return { value: buffer.toString('utf8', start, end), size: length.size + length.value };
}

/** A length-prefixed byte array, or null when it is not all there. */
function readByteArray(buffer, offset) {
  const length = readVarInt(buffer, offset);
  if (!length || length.value < 0) return null;
  const start = offset + length.size;
  const end = start + length.value;
  if (end > buffer.length) return null;
  return { value: buffer.subarray(start, end), size: length.size + length.value };
}

// ----------------------------------------------------------------------------------------------- protocol

/**
 * The digest a Minecraft server expects the client to prove at the session server.
 *
 * The shape is not incidental. Minecraft formats it with `new BigInteger(sha1(...)).toString(16)`, which reads
 * the digest as a *signed* two's-complement number: a digest whose first byte has the high bit set comes out
 * with a leading "-" and its leading zero bytes are dropped. Reproducing that exactly is the difference between
 * a join that succeeds and one the server rejects without saying why.
 */
function minecraftDigest(serverId, secret, publicKey) {
  const digest = crypto.createHash('sha1')
    .update(Buffer.from(String(serverId ?? ''), 'utf8'))
    .update(secret)
    .update(publicKey)
    .digest();
  let value = BigInt(`0x${digest.toString('hex')}`);
  if (digest.length && (digest[0] & 0x80)) value -= 1n << BigInt(8 * digest.length);
  return value.toString(16);
}

/** RSA/PKCS#1 v1.5, which is what `publicEncrypt` with a DER SubjectPublicKeyInfo and PKCS1 padding is. */
function encryptForServer(publicKeyDer, data) {
  return crypto.publicEncrypt(
    { key: publicKeyDer, format: 'der', type: 'spki', padding: crypto.constants.RSA_PKCS1_PADDING },
    data,
  );
}

/** Parses the body of an EncryptionRequest: server id, public key, verify token, and the 1.19+ flag. */
function parseEncryptionRequest(payload, offset) {
  const serverId = readString(payload, offset);
  if (!serverId) return null;
  const publicKey = readByteArray(payload, offset + serverId.size);
  if (!publicKey) return null;
  const verifyToken = readByteArray(payload, offset + serverId.size + publicKey.size);
  if (!verifyToken) return null;
  const after = offset + serverId.size + publicKey.size + verifyToken.size;
  const shouldAuthenticate = after < payload.length ? payload[after] !== 0 : true;
  return {
    serverId: serverId.value,
    publicKey: publicKey.value,
    verifyToken: verifyToken.value,
    shouldAuthenticate,
    size: serverId.size + publicKey.size + verifyToken.size + (after < payload.length ? 1 : 0),
  };
}

/** Builds the EncryptionResponse that answers it, in the shape the server's protocol version expects. */
function buildEncryptionResponse(secret, encryptedToken, protocolVersion) {
  const body = [writeVarInt(secret.length), secret];
  // 1.19 only: secret, "has verify token", token. Everything else is secret, token - and 26.x's own
  // ServerboundKeyPacket reads exactly two byte arrays, which is the shape a current server expects.
  if (protocolVersion === HAS_VERIFY_TOKEN_PROTOCOL) body.push(Buffer.from([1]));
  body.push(writeVarInt(encryptedToken.length), encryptedToken);
  const payload = Buffer.concat([writeVarInt(LOGIN_ENCRYPTION_REQUEST), ...body]);
  return Buffer.concat([writeVarInt(payload.length), payload]);
}

/**
 * The first packet a client sends after the handshake, with a different player name in it.
 *
 * An online-mode server asks the session server to verify the *name the client sent*, so a client that logs in
 * as "Steve" and signs into "Alex" is refused as an invalid session. Rewriting the name here is what lets a
 * signed-in player join whatever the client's own name field happened to be. The frame is rebuilt only because
 * a name of a different length needs a new frame length; everything after the name - a profile key, a UUID, the
 * rest of the packet - is copied byte for byte, because this must not become a packet rewriter.
 */
function rewriteLoginName(frame, name) {
  const id = readVarInt(frame, 0);
  if (!id || id.value !== 0x00) return frame;
  const current = readString(frame, id.size);
  if (!current) return frame;
  if (current.value === name) return frame;
  const encoded = Buffer.from(name, 'utf8');
  const payload = Buffer.concat([
    writeVarInt(id.value),
    writeVarInt(encoded.length),
    encoded,
    frame.subarray(id.size + current.size),
  ]);
  return Buffer.concat([writeVarInt(payload.length), payload]);
}

// --------------------------------------------------------------------------------------------- session server

/**
 * Tells Mojang "this account is joining this server with this hash".
 *
 * The client normally makes this call itself; it is proxied here for two reasons: the browser cannot (no CORS
 * on the session server, and the request is a POST with a bearer-ish body), and this way the access token never
 * leaves the machine that is already terminating the account's encryption.
 */
async function joinSession({ baseUrl, accessToken, uuid, serverId, timeoutMs = 8000, fetchImpl = fetch }) {
  const url = `${String(baseUrl).replace(/\/+$/, '')}/session/minecraft/join`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ accessToken, selectedProfile: String(uuid).replace(/-/g, ''), serverId }),
      signal: controller.signal,
    });
    if (response.status === 204 || response.ok) return { ok: true, status: response.status };
    let reason = '';
    try { reason = String((await response.json()).error || ''); } catch { /* not JSON */ }
    return { ok: false, status: response.status, reason: reason || `HTTP ${response.status}` };
  } catch (error) {
    return { ok: false, status: 0, reason: error.name === 'AbortError' ? 'timeout' : String(error.message || error) };
  } finally {
    clearTimeout(timer);
  }
}

// ------------------------------------------------------------------------------------------------ the gate

/**
 * The login-phase state machine for one session.
 *
 * It is fed the server's bytes while the session is still logging in and returns what to hand each side. Once it
 * answers an EncryptionRequest it owns both directions' ciphers, and from then on it is a transcoder: no packet
 * is parsed after login, because after login a packet can be compressed, and there is nothing left to look at.
 */
class LoginGate {
  constructor(options) {
    this.protocolVersion = Number(options.protocolVersion) || 0;
    this.getName = options.getAuth || (() => null);
    this.join = options.join || (() => ({ ok: false, status: 0, reason: 'no session server configured' }));
    this.rename = options.rename !== false;
    this.state = 'login';
    this.buffer = Buffer.alloc(0);
    this.clientBuffer = Buffer.alloc(0);
    this.frames = 0;
    this.sawHandshake = false;
    this.rewritten = false;
    this.decrypt = null;
    this.encrypt = null;
    this.digest = null;
    this.name = null;
  }

  get encrypted() { return this.state === 'encrypted'; }

  /** True while the gate is still watching the login harvest - what the session uses to pick its data path. */
  get watching() { return this.state === 'login'; }

  /**
   * Client bytes on their way to the server.
   *
   * While the session is logging in this is a frame parser, not a pipe, for two reasons: the handshake carries
   * the protocol version that decides the shape of the encryption response, and the login start carries the name
   * an online-mode server will verify. Frames are only held until they are complete, so an ordinary session sees
   * no added latency - the client writes the handshake and the login start together, and they leave together.
   * Once a secret exists, every byte from the client is encrypted the way the server now expects.
   */
  clientToServer(data) {
    if (this.encrypt) return this.encrypt.update(data);
    if (this.state !== 'login') return data;

    // Nobody to authenticate as and nothing already held: stay the byte pipe this gateway was before, rather than
    // framing bytes that may not be frames at all. A session that offers an account later (an `auth` frame right
    // after `ready`) is still in time, because this is called before its first binary frame arrives.
    const auth = this.getName();
    if (!auth && !this.clientBuffer.length && !this.sawHandshake) return data;

    this.clientBuffer = Buffer.concat([this.clientBuffer, data]);
    const out = [];
    for (;;) {
      const length = readVarInt(this.clientBuffer, 0);
      if (!length) break;
      if (length.value < 0 || length.value > 2 * 1024 * 1024) {
        // Not a frame this module understands: stop interpreting and let the bytes through untouched.
        out.push(this.clientBuffer);
        this.clientBuffer = Buffer.alloc(0);
        break;
      }
      if (this.clientBuffer.length < length.size + length.value) break;
      const payload = this.clientBuffer.subarray(length.size, length.size + length.value);
      this.clientBuffer = this.clientBuffer.subarray(length.size + length.value);

      if (!this.sawHandshake) {
        this.sawHandshake = true;
        const id = readVarInt(payload, 0);
        if (id && id.value === 0x00) {
          const version = readVarInt(payload, id.size);
          if (version) this.protocolVersion = version.value;
        }
        out.push(Buffer.concat([writeVarInt(payload.length), payload]));
        continue;
      }

      const auth = this.getName();
      if (!this.rewritten && this.rename && auth && auth.name) {        this.rewritten = true;
        this.name = auth.name;
        const rewritten = rewriteLoginName(payload, auth.name);
        if (rewritten !== payload) { out.push(rewritten); continue; }
      }
      out.push(Buffer.concat([writeVarInt(payload.length), payload]));
    }
    // A client that never completes a frame is not one to hold bytes for: hand them back rather than growing.
    if (this.clientBuffer.length > 64 * 1024) {
      out.push(this.clientBuffer);
      this.clientBuffer = Buffer.alloc(0);
    }
    return Buffer.concat(out);
  }

  /**
   * Server bytes on their way to the client. Returns what to send where; `error` means the session cannot
   * continue and the caller should report it and close.
   */
  async serverToClient(data) {
    if (this.state === 'done') return { toClient: data };
    if (this.encrypted) return { toClient: this.decrypt.update(data) };

    this.buffer = Buffer.concat([this.buffer, data]);
    const toClient = [];
    for (;;) {
      const length = readVarInt(this.buffer, 0);
      if (!length) break;
      if (length.value < 0 || length.value > 2 * 1024 * 1024) {
        return { error: { code: 'PROTOCOL_ERROR', msg: 'the server sent a login packet that cannot be parsed' } };
      }
      if (this.buffer.length < length.size + length.value) break;
      const payload = this.buffer.subarray(length.size, length.size + length.value);
      this.buffer = this.buffer.subarray(length.size + length.value);

      const id = readVarInt(payload, 0);
      if (!id) {
        toClient.push(Buffer.concat([writeVarInt(payload.length), payload]));
        break;
      }

      if (id.value === LOGIN_ENCRYPTION_REQUEST) {
        const request = parseEncryptionRequest(payload, id.size);
        if (!request) return { error: { code: 'PROTOCOL_ERROR', msg: 'the server sent a malformed encryption request' } };
        const answered = await this.answerEncryption(request);
        if (answered.error) return answered;
        // Everything after this point in the buffer arrives encrypted; hand it to the cipher rather than parsing
        // it, and say so by leaving the state machine.
        this.state = 'encrypted';
        return { toClient: Buffer.concat(toClient), toServer: answered.response };
      }

      // A disconnect, a login success or the compression switch all mean the login harvest is over. The packet
      // is forwarded untouched: whatever the server said, the human is supposed to read it.
      toClient.push(Buffer.concat([writeVarInt(payload.length), payload]));
      if (id.value === LOGIN_DISCONNECT || id.value === LOGIN_SUCCESS || id.value === LOGIN_COMPRESSION) {
        this.state = 'done';
        this.buffer = Buffer.alloc(0);
        break;
      }
      if (id.value === LOGIN_PLUGIN_REQUEST) {
        // Proxies ask a question here and wait for the client's answer before deciding on encryption.
        if (++this.frames > MAX_LOGIN_FRAMES) this.state = 'done';
        continue;
      }
      if (++this.frames > MAX_LOGIN_FRAMES) {
        this.state = 'done';
        this.buffer = Buffer.alloc(0);
        break;
      }
    }
    const leftover = this.buffer;
    this.buffer = Buffer.alloc(0);
    return { toClient: Buffer.concat(leftover.length ? [...toClient, leftover] : toClient) };
  }

  async answerEncryption(request) {
    const auth = this.getName();
    if (!auth || !auth.accessToken || !auth.uuid) {
      return { error: { code: 'AUTH_REQUIRED', msg: 'this server is online-mode: sign in with a Microsoft account to join it' } };
    }
    if (auth.name) this.name = auth.name;
    const secret = crypto.randomBytes(16);
    let encryptedSecret;
    let encryptedToken;
    try {
      encryptedSecret = encryptForServer(request.publicKey, secret);
      encryptedToken = encryptForServer(request.publicKey, request.verifyToken);
    } catch (error) {
      return { error: { code: 'PROTOCOL_ERROR', msg: `the server's public key could not be used: ${error.message}` } };
    }

    const digest = minecraftDigest(request.serverId, secret, request.publicKey);
    const joined = await this.join({ accessToken: auth.accessToken, uuid: auth.uuid, serverId: digest });
    if (!joined.ok) {
      return { error: {
        code: joined.status === 403 || joined.status === 400 ? 'AUTH_FAILED' : 'AUTH_UNAVAILABLE',
        msg: joined.status === 403 || joined.status === 400
          ? 'the sign-in has expired or does not belong to this account: sign in again'
          : `the session server could not be reached (${joined.reason})`,
      } };
    }

    this.digest = digest;
    this.encrypt = crypto.createCipheriv('aes-128-cfb8', secret, secret);
    this.decrypt = crypto.createDecipheriv('aes-128-cfb8', secret, secret);
    // Any client bytes still waiting to be framed are part of the encrypted stream too: they were written before
    // the server's request arrived, and drop after it goes out in the same write.
    const pending = this.clientBuffer.length ? this.encrypt.update(this.clientBuffer) : Buffer.alloc(0);
    this.clientBuffer = Buffer.alloc(0);
    return { response: Buffer.concat([buildEncryptionResponse(encryptedSecret, encryptedToken, this.protocolVersion), pending]) };
  }
}

module.exports = {
  LOGIN_DISCONNECT,
  LOGIN_ENCRYPTION_REQUEST,
  LOGIN_SUCCESS,
  LOGIN_COMPRESSION,
  LOGIN_PLUGIN_REQUEST,
  HAS_VERIFY_TOKEN_PROTOCOL,
  MODERN_ENCRYPTION_PROTOCOL: HAS_VERIFY_TOKEN_PROTOCOL,
  LoginGate,
  buildEncryptionResponse,
  encryptForServer,
  joinSession,
  minecraftDigest,
  parseEncryptionRequest,
  readByteArray,
  readString,
  readVarInt,
  rewriteLoginName,
  writeVarInt,
};
