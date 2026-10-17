# Multiplayer: how a typed server address actually works

A browser page cannot open a TCP socket. Not "is not allowed to" in the way a permission prompt is annoying -
the API does not exist: there is `WebSocket`, `WebTransport` and `fetch`, and none of them can dial
`mc.example.com:25565` and speak the Minecraft protocol. That is why every browser client that joins a normal
Java server has a **gateway** behind it: a small server that accepts a WebSocket from the page and pipes the
bytes to a real socket on the other side.

So the honest summary of "type a server IP and connect":

| where the address is typed | what happens |
| --- | --- |
| **Our gateway** (`gateway/server.js`) | the page asks for a token for that address, opens one WebSocket, and the gateway dials the address for it. SRV records are honoured, so a bare domain works like it does in the real client. This is what `?gateway=` in the Eagler26 client uses |
| **The Gaius relay** (its own protocol, `relay-nodes.json`) | the same idea with a different protocol. The default node is public, needs no token, and answered `ok:true, availableConnections 64/64` when this was written |
| **Nothing** | the address is only dialled if something that can open a socket dials it. There is no fourth option, and any project claiming to connect a browser straight to a Java server has a gateway it is not talking about |

The one place the user *does* see `wss://` is when they are pointed at a gateway by hand. The clients here
take a bare `host:port` (or just a host) and add the scheme themselves.

## What is in this repository

`gateway/server.js` is a complete gateway in one file with no dependencies - `node:net`, `node:dns`,
`node:crypto` and a hand-written WebSocket frame layer - so it can be read in one sitting and run with one
command. It speaks the same protocol as the client in `~/Downloads` (subprotocol `goldproxy.v1`), which is
what makes it useful immediately rather than after a rewrite of the client.

### The protocol, as that client implements it

Read out of `browser-goldproxy.js` in the Eagler26 build (extracted and hash-verified with
`tools/eagler_offline.py`), and implemented here:

```
POST /v1/token            {"host":"mc.example.com","port":25565}
   -> 200 {"token":"…","expiresAt":"…"}          short-lived, scoped to that one destination
   -> 400 {"error":"ADDRESS_BLOCKED"|"PORT_BLOCKED"|"BAD_DESTINATION", "message":"…"}
   -> 429 {"error":"RATE_LIMITED"}               too many token requests from this address

GET /v1/connect           Upgrade: websocket, Sec-WebSocket-Protocol: goldproxy.v1
   client -> text  {"t":"open","v":1,"token":"…","host":"mc.example.com","port":25565,"srv":true}
   server -> text  {"t":"ready","ip":"…","port":25565,"dnsMs":3,"connectMs":42,"window":524288}
   client -> binary   raw TCP bytes         server -> binary   raw TCP bytes
   client -> text  {"t":"ack","n":123456}   flow control: what the page has handed to the game
   client -> text  {"t":"ping","id":7}      server -> text  {"t":"pong","id":7}
   server -> text  {"t":"error","code":"CONNECT_FAILED","stage":"…","msg":"…"}
   server -> text  {"t":"end","reason":"server-closed","rx":…,"tx":…}
```

The page owns Minecraft's framing and its AES/CFB8 encryption, so the gateway is a pipe that never holds a key
and never parses a packet. The single exception is one check at the start of a session: the first bytes must
be a Minecraft handshake (VarInt length, packet id `0x00`), because a gateway that will dial any port for any
byte stream is a port scanner with a nice protocol in front of it. Anything else is refused with `NOT_MINECRAFT`
(close 4300) before the target ever sees it.

Close codes are the client's own names, so its error screen can explain them: `4000` protocol error, `4001`
unsupported version, `4003` unauthorized, `4029` rate limited, `4030` overloaded, `4100` bad destination,
`4101` destination not allowed, `4102` port blocked, `4103` address blocked, `4104` DNS failed, `4200` connect
failed, `4201` connect timeout, `4008` open timeout, `4300` not Minecraft, `4011` idle timeout, `4012` session
limit, `4500` internal.

### Running it

```bash
node gateway/server.js                     # a development gateway on http://localhost:8090
GATEWAY_PUBLIC_URL=wss://relay.example/v1/connect \
GATEWAY_SECRET=$(openssl rand -hex 32) \
GATEWAY_ALLOWED_ORIGINS=https://your.site \
node gateway/server.js                     # what a public one should look like
```

| variable | default | why it exists |
| --- | --- | --- |
| `GATEWAY_PORT`, `GATEWAY_HOST` | `8090`, `0.0.0.0` | where it listens |
| `GATEWAY_PUBLIC_URL` | none | the `wss://` address clients are given; it only appears in status output, because TLS is the reverse proxy's job |
| `GATEWAY_SECRET` | random per boot | signs tokens; without it, a restart invalidates every outstanding token, which is the safe default |
| `GATEWAY_ALLOW_PRIVATE_TARGETS` | `0` | keep it off on anything public: a gateway that will dial `10.0.0.5` or `127.0.0.1` is a way into whatever network it is on. It exists for LAN play on your own machine |
| `GATEWAY_ALLOWED_ORIGINS` | empty (any) | set it to your site so another page cannot use your gateway as a free proxy |
| `GATEWAY_MAX_SESSIONS` | `64` | one session is one player's connection |
| `GATEWAY_TOKEN_PER_MINUTE` | `20` | per source address |
| `GATEWAY_CONNECT_TIMEOUT_MS`, `GATEWAY_IDLE_TIMEOUT_MS` | `10000`, `300000` | the two ways a session can be dead without anyone noticing |
| `GATEWAY_WINDOW_BYTES` | `524288` | how much unacknowledged data may sit in the gateway before it stops reading from the server: this is what stops a stalled game thread from filling the machine's memory |

Behind TLS, a reverse proxy needs to pass the upgrade through - in nginx:

```nginx
location /v1/ {
    proxy_pass http://127.0.0.1:8090;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 1h;
}
```

`/health` answers with the same facts the launcher shows: sessions in use, whether private targets are
allowed, the blocked ports, and the window size. `/` renders the same thing as a page.

### The one command, for a client you already have

```sh
python3 tools/play_local.py                              # newest single-file client in ~/Downloads
python3 tools/play_local.py --server mc.example.com      # and join that server on load
python3 tools/play_local.py --allow-lan                  # LAN play: private targets allowed
python3 tools/play_local.py --gateway wss://relay.example/v1/connect   # use a gateway somewhere else
```

It starts the gateway on a free loopback port, serves the client as a page with the gateway set inside it, prints
the address to open and opens it, and stops both when you press Ctrl-C. `http://127.0.0.1:<port>/health` answers
the gateway's status through that page, and `/gateway` says which gateway the page is using.

### Pointing a client at it

The Eagler26 client already knows this protocol and takes the gateway as a parameter:

```
Eagler26-26.3-Full.html?gateway=wss://relay.example/v1/connect&server=mc.example.com&port=25565
```

On a `file://` page a `ws://` gateway is fine; a page served over `https:` requires `wss:` (the client
enforces that itself, and so does every browser). The other two ways in are the client's own storage key and
its build config:

```js
localStorage.setItem('eagler26.goldproxy', JSON.stringify({gateway: 'wss://relay.example/v1/connect'}))
```

For the Gaius client, multiplayer is its relay model instead: `relay-nodes.json` in the client lists nodes, a
session is leased from the chosen node, and the node dials the target. The default node in that file is
public and needs no token.

### Online-mode (premium) servers

A cracked server never asks a question a browser cannot answer, which is why the gateway used to be only a pipe.
An online-mode server does ask. It sends an EncryptionRequest, and answering it means generating a shared secret,
RSA-encrypting that secret with the server's key, proving the account to Mojang's session server with the SHA-1 of
it, and then speaking AES/CFB8 for the rest of the connection. WebCrypto has neither PKCS#1 v1.5 encryption nor
CFB8, and the compiled clients deliberately do not implement it, so the gateway does it instead - in the one place
that already speaks TCP and already sits between the page and the server:

* the page offers the account once, as `auth` on the open (or connect) request: `{accessToken, uuid, name}`. On the
  `goldproxy.v1` dialect an `{"t":"auth"}` frame before the first game byte does the same thing;
* the gateway reads the login phase - three packets at most - and when the server asks for encryption it generates
  the secret, answers with an EncryptionResponse, calls `sessionserver.mojang.com/session/minecraft/join` with the
  hash the server will check, and switches both directions to AES/CFB8;
* the client never sees the request, so nothing about it changes. The login name is rewritten to the account's
  name, because an online-mode server verifies the name the client sent and refuses a mismatch as an invalid
  session.

Both dialects do this - `/v1/connect` and the engine's `/tunnel` - and an offline server never notices, because it
never asks: the session stays exactly the byte pipe it was. `GET /health` reports `online.supported` and the session
server in use, and `GATEWAY_ALLOW_ONLINE=0` turns the whole thing off for an operator who wants a cracked-only
relay. The refusals have their own close codes so a page can tell them apart: **4400 `AUTH_REQUIRED`** (that server
is online-mode and nobody is signed in), **4401 `AUTH_FAILED`** (the sign-in is expired - sign in again), **4402
`AUTH_UNAVAILABLE`** (the session server could not be reached - try later). `tests/test_gateway_online.mjs` runs a
real handshake against a fake online-mode server and a fake session server: 29 checks, including the digest the
server computes being the one the session server was told.

What it does not do yet, and the launcher must say so rather than quietly failing at the last step:

* **No chat signing.** A 1.19+ client also proves itself for chat with a Mojang-issued profile key. The gateway can
  prove the account for the join but cannot sign as it, so a server with `enforce-secure-profile=true` (the vanilla
  default since 1.19.1) still refuses the session. 1.8 - 1.18.2, and any 1.19+ server that turned it off, are
  unaffected.
* **No sign-in flow in the page yet.** The gateway accepts a token; nothing in `site/` obtains one. That is the next
  piece: the Microsoft device-code flow (open a link, type a code) and the Xbox/XSTS/Minecraft chain, with the XBL,
  XSTS and Minecraft calls proxied by the gateway because those hosts do not answer CORS. The join-test page takes a
  token pasted from another launcher in the meantime, which is enough to walk this path in a real browser.
* **No modern-forwarding proxies.** A server behind Velocity or BungeeCord in "modern forwarding" mode wants the
  proxy's own secret in the handshake, and a player cannot supply it. That is the server's decision, not a gap to
  work around.

## What is not here

- **We do not run a public gateway for you.** Running one is a server, and this project is a static site:
  it cannot host one and does not pretend to. The launcher shows the gateway you configured, and the
  address field is yours to fill.
- **A gateway is not a bypass.** It dials what your server allows. A server that bans proxies, or a LAN
  address that only exists on your own network, will not work through a public gateway - and should not.
- **Nothing here is a tunnel to somebody's private network.** Private and reserved targets are refused by
  rule, by name, and again after DNS, because a name is only as private as the address it resolves to.

## The gateway as a Cloudflare Worker (cracked servers, no machine of your own)

`gateway/server.js` needs a machine that is always on. A Cloudflare Worker is the same gateway without
that: a Worker can open a TCP connection with Cloudflare's sockets API, it is reachable over `wss://`
without a certificate of your own, and the free tier is enough for a community client.

```sh
npx wrangler deploy gateway/cloudflare-worker.js --name webmc-gateway --compatibility-date 2026-01-01
npx wrangler secret put GATEWAY_SECRET      # any long random string: tokens are signed with it
curl https://webmc-gateway.<you>.workers.dev/health
```

It speaks the same two dialects as the Node gateway (`goldproxy.v1` and the engine's tunnel), so a client
points at it unchanged:

```
https://amphibiandev.github.io/webmc-port/clients/26.2/?bridge=wss://webmc-gateway.<you>.workers.dev/v1/connect
```

What it refuses is the same list: private, loopback and link-local addresses (by literal and by name),
database and mail ports, a low port that is not Minecraft, a token for another destination, a token older
than two minutes, and a session whose first bytes are not a Minecraft handshake - because a relay that
dials any port for any bytes is a port scanner with a WebSocket in front of it.

The socket half of the Worker is Cloudflare's runtime (there is no way to run it on a laptop), so what is
tested here is the half that decides what may be dialled: `node tests/test_gateway_worker.mjs`.
