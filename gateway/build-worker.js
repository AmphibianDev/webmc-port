// The build trigger, as a Cloudflare Worker.
//
// A bake is a compile (30-45 minutes, a real JVM, a real gradle build), so it cannot happen in a browser tab
// and it should not happen on the player's machine. It happens on GitHub Actions. But a page cannot dispatch
// a workflow: that needs a token with `actions:write`, and a token in a page is a token given away. So the
// token lives here, in a Worker secret, and the page talks to this.
//
// This is the same flow `webmc-mods/tools/build-service.mjs` runs on a machine you own, moved to the edge so
// that the deployed launcher can use it without anyone running anything:
//
//   POST /api/build        multipart: version, mods[] (the player's own jars)
//                          -> { id, state, branch, cached? }
//   GET  /api/build?id=…   -> { state, stage, detail, client? }
//   GET  /api/health       -> { ok, repo, workflow, ready }
//
// What it does with a request:
//
//   1. refuses, before anything is dispatched, a mod the bake cannot do - a jar carrying native libraries
//      (Distant Horizons and friends) is not a mod that can be compiled into a browser client, and a player
//      should be told that in a second rather than after an hour.
//   2. keys the build on the *content*: the version plus the sha256 of every jar, so the same mod set asked
//      for twice is answered from the first build instead of starting a second one.
//   3. commits that mod set to a branch of its own (`build/<key>`), so a build is reproducible and does not
//      touch main.
//   4. dispatches the bake workflow on that branch and answers with the id to poll.
//
// Deploy:
//   npx wrangler deploy gateway/build-worker.js --name webmc-build --compatibility-date 2026-01-01
//   npx wrangler secret put GITHUB_TOKEN      # fine-grained: this repo, contents:write + actions:write
//   npx wrangler secret put BUILD_SECRET      # optional; if set, callers must send it as ?key=
//
// Environment:
//   GITHUB_TOKEN     required: contents:write (branches, files) and actions:write (dispatch)
//   REPO             default AmphibianDev/webmc-port
//   WORKFLOW         default build-snapshot.yml
//   BUILD_SECRET     optional shared secret; when set, POST bodies must carry it
//   PAGES_BASE       default https://amphibiandev.github.io/webmc-port
//   MAX_JAR_BYTES    default 64 MB
//   ALLOW_ORIGIN     default * (the launcher is a static page on github.io)

const DEFAULTS = {
  repo: "AmphibianDev/webmc-port",
  workflow: "build-snapshot.yml",
  pages: "https://amphibiandev.github.io/webmc-port",
  maxJar: 64 * 1024 * 1024,
  origin: "*",
};

const json = (body, status = 200, env) => new Response(JSON.stringify(body, null, 1), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": env?.ALLOW_ORIGIN || DEFAULTS.origin,
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "cache-control": "no-store",
  },
});

/* ------------------------------------------------------------------------------ the rules, in the open */

/** A jar with native libraries inside it cannot be compiled into a page: say so before the build. */
export function refusalFor(name, bytes) {
  const lower = name.toLowerCase();
  if (!/\.(jar|zip)$/.test(lower)) {
    return `${name}: only .jar and .zip files can be baked`;
  }
  // The entry names are enough: a native mod ships .dll/.so/.dylib files or a natives classifier, and both
  // are visible in the zip's central directory without reading the whole archive.
  const text = new TextDecoder("latin1").decode(bytes.slice(0, Math.min(bytes.length, 4 * 1024 * 1024)));
  if (/\.(dll|so|dylib)\b/.test(text)) {
    return `${name}: this mod ships native libraries, which a browser client cannot load `
      + "(Distant Horizons is the well-known one)";
  }
  if (/(^|\/)(natives?)(\/|$)/i.test(text) && /-natives-/.test(lower)) {
    return `${name}: this looks like a natives artifact rather than the mod itself`;
  }
  return null;
}

/** The key a build is cached under: the version and the exact bytes of every mod in it. */
export async function cacheKey(version, files) {
  const parts = [`v1\u0000${version}`];
  for (const file of [...files].sort((a, b) => a.name.localeCompare(b.name))) {
    const digest = await crypto.subtle.digest("SHA-256", file.bytes);
    parts.push(`${file.name}\u0000${[...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("")}`);
  }
  const total = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(parts.join("\u0000")));
  return [...new Uint8Array(total)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

/** The id a mod is known by inside the bake: its file name without the version noise. */
export function modId(name) {
  return name.replace(/\.(jar|zip)$/i, "").replace(/[^A-Za-z0-9._-]+/g, "-").toLowerCase();
}

/* ------------------------------------------------------------------------------------ GitHub, minimal */

function github(env, path, init = {}) {
  return fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "user-agent": "webmc-build-worker",
      "x-github-api-version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
}

async function dispatch(env, branch, version, mods) {
  const repo = env.REPO || DEFAULTS.repo;
  const workflow = env.WORKFLOW || DEFAULTS.workflow;
  const response = await github(env, `/repos/${repo}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: branch, inputs: { version: String(version), mods: mods.join(",") } }),
  });
  if (response.status !== 204 && !response.ok) {
    return `could not start the build: ${response.status} ${await response.text()}`;
  }
  return null;
}

async function ensureBranch(env, branch) {
  const repo = env.REPO || DEFAULTS.repo;
  const head = await github(env, `/repos/${repo}/git/ref/heads/main`);
  if (!head.ok) return `could not read main: ${head.status}`;
  const sha = (await head.json()).object.sha;
  const created = await github(env, `/repos/${repo}/git/refs`, {
    method: "POST",
    body: JSON.stringify({ ref: `refs/heads/${branch}`, sha }),
  });
  if (created.ok) return null;
  if (created.status === 422) return null;   // it is already there: a previous attempt, which is fine
  return `could not create ${branch}: ${created.status}`;
}

/** One commit per file, through the contents API: a mod set is a handful of jars, not a repository. */
async function putFile(env, branch, path, bytes, message) {
  const repo = env.REPO || DEFAULTS.repo;
  const existing = await github(env, `/repos/${repo}/contents/${path}?ref=${encodeURIComponent(branch)}`);
  const body = {
    message,
    branch,
    content: base64(bytes),
    ...(existing.ok ? { sha: (await existing.json()).sha } : {}),
  };
  const response = await github(env, `/repos/${repo}/contents/${path}`, { method: "PUT", body: JSON.stringify(body) });
  if (!response.ok) return `${path}: ${response.status} ${await response.text()}`;
  return null;
}

export function base64(bytes) {
  let binary = "";
  const view = new Uint8Array(bytes);
  for (let index = 0; index < view.length; index += 0x8000) {
    binary += String.fromCharCode.apply(null, view.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

/* ------------------------------------------------------------------------------------ the two endpoints */

/**
 * What the run is doing, step by step.
 *
 * GitHub does not stream an in-progress run's log - the logs endpoint hands back a zip of a finished job -
 * but it does say which step is running, which is what a progress bar needs to move, and when a job fails it
 * keeps its *annotations*, which are the `::error::` lines the workflow printed. Those two are the honest
 * version of "stream the build log": where it is now, and what went wrong.
 */
export async function runSteps(env, runId) {
  const repo = env.REPO || DEFAULTS.repo;
  const response = await github(env, `/repos/${repo}/actions/runs/${runId}/jobs`);
  if (!response.ok) return { steps: [], percent: null };
  const jobs = (await response.json()).jobs || [];
  const steps = [];
  for (const job of jobs) {
    for (const step of job.steps || []) {
      steps.push({ name: step.name, status: step.status, conclusion: step.conclusion, number: step.number });
    }
  }
  const finished = steps.filter((step) => step.conclusion).length;
  const problems = [];
  for (const job of jobs) {
    if (job.conclusion && job.conclusion !== "success" && job.check_run_id) {
      const annotations = await github(env, `/repos/${repo}/check-runs/${job.check_run_id}/annotations`);
      if (annotations.ok) {
        for (const annotation of (await annotations.json()) || []) {
          if (annotation.message) problems.push(`${annotation.level || "failure"}: ${annotation.message}`);
        }
      }
    }
  }
  return {
    steps,
    percent: steps.length ? Math.round((finished / steps.length) * 100) : null,
    problems: problems.slice(0, 12),
  };
}

/** What a finished build published: the branch carries the version it was for. */
async function buildInfo(env, id) {
  const repo = env.REPO || DEFAULTS.repo;
  const response = await github(env, `/repos/${repo}/contents/build-info.json?ref=${encodeURIComponent(`build/${id}`)}`);
  if (!response.ok) return null;
  try {
    const body = await response.json();
    return JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(body.content.replace(/\n/g, "")), (c) => c.charCodeAt(0))));
  } catch {
    return null;
  }
}

export async function runStatus(env, id) {
  const repo = env.REPO || DEFAULTS.repo;
  const pages = env.PAGES_BASE || DEFAULTS.pages;
  const runs = await github(env, `/repos/${repo}/actions/runs?branch=${encodeURIComponent(`build/${id}`)}&per_page=5`);
  if (!runs.ok) {
    return { state: "unknown", detail: `the build machine answered ${runs.status}` };
  }
  const list = (await runs.json()).workflow_runs || [];
  if (!list.length) {
    // Nothing has been dispatched for this id. That is *not* the same as "a build is running", which this
    // worker got wrong at first: every new mod set was answered with "cached, already building" and never
    // dispatched at all.
    return { state: "none", detail: "nothing has been started for this mod set" };
  }
  const run = list[0];
  const detail = await runSteps(env, run.id);
  if (run.status === "completed") {
    if (run.conclusion !== "success") {
      // the annotations are the workflow's own ::error:: lines: without them a failed build is one word
      return { state: "failed", stage: "failed", run: run.html_url,
               steps: detail.steps, problems: detail.problems,
               detail: detail.problems.length ? detail.problems[0] : `the build ${run.conclusion}`,
               error: detail.problems.length ? detail.problems[0] : `the build ${run.conclusion}` };
    }
    // The version is on the branch, because a run does not answer with its own inputs: without it there is
    // no way to say which page came out of this build.
    const info = await buildInfo(env, id);
    const client = info?.version ? `${pages}/clients/${info.version}/` : null;
    return {
      state: "done",
      stage: "done",
      steps: detail.steps,
      problems: detail.problems,
      client,
      version: info?.version || null,
      mods: info?.mods || null,
      run: run.html_url,
      detail: client ? "the client is published" : "the build finished; the page could not be named",
    };
  }
  const current = detail.steps.find((step) => step.status === "in_progress");
  return {
    state: "building",
    stage: run.status === "in_progress" ? "compiling" : "queued",
    percent: detail.percent,
    steps: detail.steps,
    problems: detail.problems,
    step: current ? current.name : null,
    detail: current ? current.name : `${run.status} on the build machine`,
    run: run.html_url,
  };
}

export async function startBuild(env, request) {
  const form = await request.formData();
  const version = String(form.get("version") || "").trim();
  if (!/^[0-9][0-9A-Za-z._-]*$/.test(version)) {
    return { status: 400, body: { error: "a version like 26.2 or 1.21.11 is needed" } };
  }
  const maxJar = Number(env.MAX_JAR_BYTES || DEFAULTS.maxJar);
  const files = [];
  for (const value of form.getAll("mods")) {
    if (typeof value === "string") continue;                 // a stray field, not a file
    const bytes = new Uint8Array(await value.arrayBuffer());
    if (bytes.length > maxJar) {
      return { status: 413, body: { error: `${value.name} is larger than ${Math.round(maxJar / 1048576)} MB` } };
    }
    const refusal = refusalFor(value.name, bytes);
    if (refusal) return { status: 422, body: { error: refusal, refused: true } };
    files.push({ name: value.name, bytes });
  }
  if (!files.length) {
    return { status: 400, body: { error: "no mods were sent: a build is for a mod set of your own" } };
  }

  const id = await cacheKey(version, files);
  const branch = `build/${id}`;
  const existing = await runStatus(env, id);
  if (existing.state === "done") {
    return { status: 200, body: { id, branch, state: "done", client: existing.client, cached: true,
                                  detail: "this exact mod set was already built" } };
  }
  if (existing.state === "building") {
    return { status: 202, body: { id, branch, state: "building", detail: existing.detail, cached: true } };
  }

  const branchProblem = await ensureBranch(env, branch);
  if (branchProblem) return { status: 502, body: { error: branchProblem } };

  const mods = [];
  for (const file of files) {
    const path = `mods/vendor/${modId(file.name)}${file.name.slice(file.name.lastIndexOf("."))}`;
    const problem = await putFile(env, branch, path, file.bytes, `build ${id}: add ${file.name}`);
    if (problem) return { status: 502, body: { error: problem } };
    mods.push({ id: modId(file.name), name: file.name, file: path.slice("mods/".length) });  // "vendor/<file>"
  }
  const manifest = {
    note: `the mod set of build ${id}, committed by the build worker from what a player dropped in`,
    mods: mods.map((mod) => ({ id: mod.id, name: mod.name, version: version, file: mod.file, game: [version],
                               loaders: ["fabric", "aot"] })),
  };
  const infoProblem = await putFile(env, branch, "build-info.json",
    new TextEncoder().encode(JSON.stringify({ version, mods: mods.map((mod) => mod.id), at: new Date().toISOString() }, null, 2) + "\n"),
    `build ${id}: what this build is for`);
  if (infoProblem) return { status: 502, body: { error: infoProblem } };

  const manifestProblem = await putFile(env, branch, "mods/mods.json",
    new TextEncoder().encode(JSON.stringify(manifest, null, 2) + "\n"), `build ${id}: the mod set`);
  if (manifestProblem) return { status: 502, body: { error: manifestProblem } };

  const failure = await dispatch(env, branch, version, mods.map((mod) => mod.id));
  if (failure) return { status: 502, body: { error: failure } };
  return { status: 202, body: { id, branch, state: "building", mods: mods.map((mod) => mod.id),
                                detail: "the build machine is starting" } };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: {
        "access-control-allow-origin": env.ALLOW_ORIGIN || DEFAULTS.origin,
        "access-control-allow-headers": "content-type",
        "access-control-allow-methods": "GET,POST,OPTIONS",
      } });
    }

    if (url.pathname === "/api/health") {
      const ready = Boolean(env.GITHUB_TOKEN);
      return json({ ok: ready, repo: env.REPO || DEFAULTS.repo, workflow: env.WORKFLOW || DEFAULTS.workflow,
                    ready, detail: ready ? "the build machine is configured" : "GITHUB_TOKEN is not set" },
                  200, env);
    }

    if (url.pathname === "/api/build" && request.method === "POST") {
      if (env.BUILD_SECRET && url.searchParams.get("key") !== env.BUILD_SECRET) {
        return json({ error: "this build service wants a key" }, 401, env);
      }
      if (!env.GITHUB_TOKEN) {
        return json({ error: "the build service has no GITHUB_TOKEN, so it cannot start anything" }, 503, env);
      }
      try {
        const { status, body } = await startBuild(env, request);
        return json(body, status, env);
      } catch (problem) {
        return json({ error: `the build could not be started: ${problem}` }, 500, env);
      }
    }

    if (url.pathname === "/api/build" && request.method === "GET") {
      const id = url.searchParams.get("id") || "";
      if (!/^[0-9a-f]{8,64}$/.test(id)) {
        return json({ error: "an id from POST /api/build is needed" }, 400, env);
      }
      if (!env.GITHUB_TOKEN) return json({ error: "no GITHUB_TOKEN" }, 503, env);
      const status = await runStatus(env, id);
      return json({ id, ...status }, 200, env);
    }

    return json({ error: "POST a version and mods to /api/build, then poll it with GET" }, 404, env);
  },
};
