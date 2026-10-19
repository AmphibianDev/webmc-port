/* The Cloudflare Worker gateway's own rules, tested where they can be tested without an account.
 *
 *   node tests/test_gateway_worker.mjs
 *
 * The socket half needs Cloudflare's runtime, but the half that decides *what may be dialled* and *who
 * is allowed to dial it* is plain JavaScript, and that is the half that matters for safety: a relay that
 * will connect to anything is a port scanner with a WebSocket in front of it. So this drives the
 * exported rules directly: destination validation, token signing and expiry, the rate limit, and the
 * handshake check that keeps a session from being "any bytes to any port".
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

let failures = 0;
let checks = 0;
function check(name, ok, detail = "") {
  checks++;
  if (ok) { console.log(`  PASS  ${name}`); return true; }
  failures++;
  console.log(`  FAIL  ${name}${detail ? `\n        ${detail}` : ""}`);
  return false;
}
function summary(name) {
  console.log(failures ? `\n${checks} checks, ${failures} failed` : `${checks}/${checks} ${name} passed`);
  process.exit(failures ? 1 : 0);
}
const worker = await import(path.join(ROOT, "gateway/cloudflare-worker.js"));

console.log("\nthe source, before anything runs");
{
  const source = readFileSync(path.join(ROOT, "gateway/cloudflare-worker.js"), "utf8");
  check("it is a worker module with a fetch handler", /export default \{[\s\S]*fetch\(/.test(source));
  check("it speaks both dialects the clients implement", source.includes("goldproxy") && source.includes("\"connecting\""));
  check("it does not hold a Minecraft key or parse packets", !/cipher|decrypt|AES/i.test(source));
  check("it takes its secret from the environment, never from the file", /env\.GATEWAY_SECRET/.test(source) && !/secret\s*=\s*["'][A-Za-z0-9]{8,}["']/.test(source));
}

console.log("\nwhat may be dialled");
{
  check("a normal server and port is allowed", worker.checkDestination("mc.example.com", 25565).ok);
  check("another port a server may really use is allowed", worker.checkDestination("mc.example.com", 25566).ok);
  for (const [host, port, why] of [
    ["127.0.0.1", 25565, "loopback"],
    ["10.0.0.5", 25565, "a private network"],
    ["192.168.1.10", 25565, "a home network"],
    ["169.254.169.254", 25565, "link-local metadata"],
    ["[::1]", 25565, "IPv6 loopback"],
    ["localhost", 25565, "the name of loopback"],
    ["redis.internal", 25565, "an internal name"],
  ]) {
    const result = worker.checkDestination(host, port);
    check(`${why} is refused (${host})`, !result.ok && /BLOCKED/.test(result.code), JSON.stringify(result));
  }
  check("a database port is refused even on a public host",
        !worker.checkDestination("db.example.com", 3306).ok, JSON.stringify(worker.checkDestination("db.example.com", 3306)));
  check("a low port that is not Minecraft is refused",
        !worker.checkDestination("example.com", 22).ok, JSON.stringify(worker.checkDestination("example.com", 22)));
  check("the Minecraft port itself is allowed even though it is low",
        worker.checkDestination("example.com", 25565).ok);
  check("a host with a space or a slash is refused", !worker.checkDestination("mc example.com", 25565).ok && !worker.checkDestination("a/b", 25565).ok);
  check("a nonsense port is refused", !worker.checkDestination("mc.example.com", 0).ok && !worker.checkDestination("mc.example.com", 70000).ok);
}

console.log("\ntokens");
{
  const secret = "a-test-secret";
  const now = Date.now();
  const token = await worker.issueToken(secret, "mc.example.com", 25565, now);
  check("a token is signed for one destination", await worker.verifyToken(secret, token, "mc.example.com", 25565, now));
  check("it does not work for another host", !(await worker.verifyToken(secret, token, "other.example.com", 25565, now)));
  check("it does not work for another port", !(await worker.verifyToken(secret, token, "mc.example.com", 25566, now)));
  check("it does not work with another secret", !(await worker.verifyToken("another-secret", token, "mc.example.com", 25565, now)));
  check("it expires after two minutes", !(await worker.verifyToken(secret, token, "mc.example.com", 25565, now + 121000)));
  check("and it does not last forever", !(await worker.verifyToken(secret, token, "mc.example.com", 25565, now + 3600000)));
  const forged = token.replace(/.$/, token.endsWith("A") ? "B" : "A");
  check("a tampered signature is refused", !(await worker.verifyToken(secret, forged, "mc.example.com", 25565, now)));
  check("a token that is not a token is refused", !(await worker.verifyToken(secret, "not-a-token", "mc.example.com", 25565, now)));
}

console.log("\nthe rate limit");
{
  const perMinute = 3;
  const start = 1700000000000;
  const results = [0, 1, 2, 3].map((i) => worker.rateLimited("1.2.3.4", perMinute, start + i));
  check("the first requests are allowed and the one over the limit is not", results.join(",") === "false,false,false,true", results.join(","));
  check("another address has its own bucket", worker.rateLimited("5.6.7.8", perMinute, start) === false);
  check("a minute later it is allowed again", worker.rateLimited("1.2.3.4", perMinute, start + 61000) === false);
}

console.log("\nthe handshake check (what keeps this from being a port scanner)");
{
  const handshake = new Uint8Array([0x10, 0x00, 0xff, 0x05, 0x09, 0x31, 0x32, 0x37, 0x2e, 0x30]);
  check("a Minecraft handshake passes", worker.looksLikeHandshake(handshake));
  check("plain HTTP does not", !worker.looksLikeHandshake(new TextEncoder().encode("GET / HTTP/1.1\r\n\r\n")));
  check("an SSH banner does not", !worker.looksLikeHandshake(new TextEncoder().encode("SSH-2.0-OpenSSH_9.6\r\n")));
  check("an empty greeting does not", !worker.looksLikeHandshake(new Uint8Array([0])));
}

console.log("");
summary("gateway worker tests");
