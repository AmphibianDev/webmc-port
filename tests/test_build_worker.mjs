/* The build worker's own rules, tested without a Cloudflare account and without spending a build.
 *
 *   node tests/test_build_worker.mjs
 *
 * The half that matters for a player is the half that runs before GitHub is touched at all: what may be
 * baked, what the cache key is, and what the worker refuses to accept. So this drives the exported rules
 * directly, and then drives the two endpoints against a fake GitHub that records what was asked of it.
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

const worker = await import(path.join(ROOT, "gateway/build-worker.js"));

console.log("\nthe source, before anything runs");
{
  const source = readFileSync(path.join(ROOT, "gateway/build-worker.js"), "utf8");
  check("it is a worker module with a fetch handler", /export default \{[\s\S]*fetch\(/.test(source));
  check("the token comes from the environment, never from the file",
    /env\.GITHUB_TOKEN/.test(source) && !/gh[pousr]_[A-Za-z0-9]{20,}/.test(source));
  check("it dispatches Actions rather than doing the build itself",
    source.includes("actions/workflows") && source.includes("dispatches"));
  check("it says how to deploy itself", source.includes("npx wrangler deploy"));
}

console.log("\nwhat may be baked");
{
  const jar = (name) => new TextEncoder().encode(name);
  check("a plain mod jar is allowed", worker.refusalFor("sodium-fabric-0.9.2.jar", jar("META-INF/MANIFEST.MF")) === null);
  const dll = worker.refusalFor("distanthorizons-3.3.3.jar", new TextEncoder().encode("x‌libs/native/libDH.so‌y"));
  check("a mod with native libraries is refused before any build starts", Boolean(dll) && /native/.test(dll), String(dll));
  check("a .dll inside the archive is refused too",
    Boolean(worker.refusalFor("mods.jar", new TextEncoder().encode("native/win32/foo.dll"))));
  check("a non-jar is refused", /only .jar/.test(String(worker.refusalFor("readme.txt", jar("x")))));
  check("a natives-only artifact is refused",
    Boolean(worker.refusalFor("lwjgl-natives-macos.jar", new TextEncoder().encode("natives/\n"))));
}

console.log("\nthe cache key is the content, not the request");
{
  const bytes = (text) => new TextEncoder().encode(text);
  const a = await worker.cacheKey("26.2", [{ name: "one.jar", bytes: bytes("AAA") }, { name: "two.jar", bytes: bytes("BBB") }]);
  const b = await worker.cacheKey("26.2", [{ name: "two.jar", bytes: bytes("BBB") }, { name: "one.jar", bytes: bytes("AAA") }]);
  const c = await worker.cacheKey("26.3", [{ name: "one.jar", bytes: bytes("AAA") }, { name: "two.jar", bytes: bytes("BBB") }]);
  const d = await worker.cacheKey("26.2", [{ name: "one.jar", bytes: bytes("AAA") }, { name: "two.jar", bytes: bytes("CCC") }]);
  check("the same mods in another order are the same build", a === b, `${a} vs ${b}`);
  check("another version is another build", a !== c);
  check("changed bytes are another build", a !== d);
  check("the key is a short hex id", /^[0-9a-f]{32}$/.test(a), a);
}

console.log("\nthe id a mod is known by");
{
  check("a jar name becomes a mod id", worker.modId("Meteor Client 26.2-35.jar") === "meteor-client-26.2-35",
    worker.modId("Meteor Client 26.2-35.jar"));
  check("a path or an odd name cannot escape the folder", !worker.modId("../../evil.jar").includes("/"),
    worker.modId("../../evil.jar"));
}

console.log("\nthe endpoints, against a fake GitHub");
{
  const calls = [];
  const noToken = { REPO: "owner/repo" };
  let response = await worker.default.fetch(new Request("https://build/api/health"), noToken);
  let body = await response.json();
  check("health says the build machine is not configured without a token", body.ok === false && /GITHUB_TOKEN/.test(body.detail), JSON.stringify(body));

  response = await worker.default.fetch(new Request("https://build/api/build", { method: "POST", body: new FormData() }), noToken);
  check("a build without a token is refused rather than half-done", response.status === 503, String(response.status));

  // a fake GitHub: main exists, nothing else does, and dispatch is recorded
  globalThis.fetch = async (url, init = {}) => {
    const target = String(url);
    calls.push({ target, method: init.method || "GET", body: init.body });
    if (target.includes("/git/ref/heads/main")) {
      return new Response(JSON.stringify({ object: { sha: "abc123" } }), { status: 200 });
    }
    if (target.includes("/actions/runs?")) {
      return new Response(JSON.stringify({ workflow_runs: [] }), { status: 200 });
    }
    if (target.includes("/git/refs")) return new Response("{}", { status: 201 });
    if (target.includes("/contents/")) {
      return target.includes("?ref=") && init.method !== "PUT"
        ? new Response("{}", { status: 404 })
        : new Response("{}", { status: 201 });
    }
    if (target.includes("/dispatches")) return new Response(null, { status: 204 });
    return new Response("{}", { status: 200 });
  };

  const form = new FormData();
  form.set("version", "26.2");
  form.append("mods", new File([new TextEncoder().encode("mod bytes")], "my-mod.jar"));
  response = await worker.default.fetch(new Request("https://build/api/build", { method: "POST", body: form }),
    { GITHUB_TOKEN: "test", REPO: "owner/repo" });
  body = await response.json();
  check("a build with a token is accepted", response.status === 202, `${response.status} ${JSON.stringify(body)}`);
  check("and answers with an id to poll", /^[0-9a-f]{32}$/.test(body.id || ""), JSON.stringify(body));
  check("the mod set is committed to a branch of its own",
    calls.some((call) => call.method === "PUT" && String(call.target).includes("/contents/mods/vendor/my-mod.jar")),
    JSON.stringify(calls.map((c) => `${c.method} ${c.target}`)));
  // the ref travels in the body of POST /git/refs, not in its url
  check("the branch is named after the content",
    calls.some((call) => String(call.body || "").includes(`refs/heads/build/${body.id}`)),
    JSON.stringify(calls.filter((c) => c.body && String(c.body).includes("refs/heads"))));
  check("what the build is for is written with it", calls.some((call) => String(call.target).includes("build-info.json")));
  check("the workflow is dispatched on that branch",
    calls.some((call) => String(call.target).includes("/dispatches") && String(call.body).includes(`build/${body.id}`)),
    JSON.stringify(calls.filter((c) => String(c.target).includes("dispatches"))));
  check("and the mod list is sent with it",
    calls.some((call) => String(call.body).includes("my-mod")), "the ids travel with the dispatch");

  const empty = new FormData();
  empty.set("version", "26.2");
  response = await worker.default.fetch(new Request("https://build/api/build", { method: "POST", body: empty }),
    { GITHUB_TOKEN: "test", REPO: "owner/repo" });
  check("a build with no mods is refused, with the reason", response.status === 400, String(response.status));

  const native = new FormData();
  native.set("version", "26.2");
  native.append("mods", new File([new TextEncoder().encode("libDH.so")], "distanthorizons.jar"));
  response = await worker.default.fetch(new Request("https://build/api/build", { method: "POST", body: native }),
    { GITHUB_TOKEN: "test", REPO: "owner/repo" });
  body = await response.json();
  check("a native mod is refused before anything is dispatched",
    response.status === 422 && body.refused === true, `${response.status} ${JSON.stringify(body)}`);

  const badVersion = new FormData();
  badVersion.set("version", "../../etc");
  badVersion.append("mods", new File([new TextEncoder().encode("x")], "a.jar"));
  response = await worker.default.fetch(new Request("https://build/api/build", { method: "POST", body: badVersion }),
    { GITHUB_TOKEN: "test", REPO: "owner/repo" });
  check("a version that is not a version is refused", response.status === 400, String(response.status));

  response = await worker.default.fetch(new Request("https://build/api/build?id=not-hex"), { GITHUB_TOKEN: "test" });
  check("polling needs an id that could have come from a build", response.status === 400, String(response.status));

  // a finished run, with the version on the branch: without it the worker cannot name the page
  globalThis.fetch = async (url) => {
    const target = String(url);
    if (target.includes("/actions/runs?")) {
      return new Response(JSON.stringify({ workflow_runs: [
        { status: "completed", conclusion: "success", html_url: "https://github.com/run/1" }] }), { status: 200 });
    }
    if (target.includes("build-info.json")) {
      return new Response(JSON.stringify({ content: Buffer.from(JSON.stringify({ version: "26.2", mods: ["my-mod"] })).toString("base64") }), { status: 200 });
    }
    if (target.includes("/actions/runs/1")) return new Response("{}", { status: 200 });
    return new Response("{}", { status: 200 });
  };
  response = await worker.default.fetch(new Request("https://build/api/build?id=0123456789abcdef0123456789abcdef"), { GITHUB_TOKEN: "test" });
  body = await response.json();
  check("a finished build reports the page it published",
    body.state === "done" && body.client.endsWith("/clients/26.2/"), JSON.stringify(body));

  globalThis.fetch = async (url) => String(url).includes("/actions/runs?")
    ? new Response(JSON.stringify({ workflow_runs: [
        { status: "in_progress", conclusion: null, html_url: "https://github.com/run/2" }] }), { status: 200 })
    : new Response("{}", { status: 200 });
  response = await worker.default.fetch(new Request("https://build/api/build?id=0123456789abcdef0123456789abcdef"), { GITHUB_TOKEN: "test" });
  body = await response.json();
  check("a running build says it is compiling", body.state === "building" && body.stage === "compiling", JSON.stringify(body));

  globalThis.fetch = async (url) => String(url).includes("/actions/runs?")
    ? new Response(JSON.stringify({ workflow_runs: [
        { status: "completed", conclusion: "failure", html_url: "https://github.com/run/3" }] }), { status: 200 })
    : new Response("{}", { status: 200 });
  response = await worker.default.fetch(new Request("https://build/api/build?id=0123456789abcdef0123456789abcdef"), { GITHUB_TOKEN: "test" });
  body = await response.json();
  check("a failed build is reported as failed, with its run", body.state === "failed" && body.run.includes("run/3"), JSON.stringify(body));

  response = await worker.default.fetch(new Request("https://build/anything", { method: "OPTIONS" }), {});
  check("a browser preflight is answered", response.status === 204
    && response.headers.get("access-control-allow-origin") === "*", String(response.status));
}

summary("build worker checks");
