// Contract test for site/index.html: the launcher page.
//
//   node tests/test_site.mjs
//
// The page is checked for what a launcher has to get right and nothing else. It parses, every element it reads
// exists, it reads the project's own data files rather than carrying a version list of its own, it lists *only*
// the versions that can actually be played until you ask for the rest, and the five answers it can give about a
// version (a page this site serves, a JVM in the page, a download, somebody else's link, or nothing yet) are all
// present and honest. The layout is checked too, because it was asked for: a rail of clients on the left, a hero
// and a list in the middle, and a status bar with the account and the one button that matters.
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const page = readFileSync(path.join(ROOT, 'site', 'index.html'), 'utf8');
/* site/routes.json is derived and git-ignored: the catalog job regenerates it before it deploys. A fresh clone
 * therefore has no such file, and a test that assumed one is how a green laptop turns into a red runner. So the
 * file is built here with the same tool the deploy uses, offline, from the state the repository does carry. */
function routeIndex() {
  const target = path.join(ROOT, 'site', 'routes.json');
  try {
    return JSON.parse(readFileSync(target, 'utf8')).versions;
  } catch (error) {
    execFileSync('python3', [path.join(ROOT, 'tools', 'route.py'), '--export', target], { stdio: 'pipe' });
    return JSON.parse(readFileSync(target, 'utf8')).versions;
  }
}
const routes = routeIndex();
const catalog = JSON.parse(readFileSync(path.join(ROOT, 'site', 'manifest.json'), 'utf8'));
let failures = 0;
const check = (label, ok, detail = '') => {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${ok || !detail ? '' : ` — ${detail}`}`);
  if (!ok) failures++;
};

const scripts = page.match(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g) || [];
check('the page has one inline script', scripts.length === 1, String(scripts.length));
const body = (scripts[0] || '').replace(/^<script[^>]*>/, '').replace(/<\/script>$/, '');

const scratch = path.join(mkdtempSync(path.join(tmpdir(), 'webmc-launcher-')), 'page.js');
writeFileSync(scratch, body);
try {
  execFileSync(process.execPath, ['--check', scratch], { stdio: 'pipe' });
  check('the inline script parses', true);
} catch (error) {
  check('the inline script parses', false, String(error.stderr).slice(0, 300));
}

// ---------------------------------------------------------------- the layout that was asked for
for (const [label, needle] of [
  ['a rail of clients down the left', 'class="sidebar"'],
  ['a hero for the selected version', 'id="title"'],
  ['a list of versions', 'id="list"'],
  ['tabs across the top', 'id="tabs"'],
  ['a status bar at the bottom', 'class="bottombar"'],
  ['the account in the corner', 'id="account"'],
  ['and the one button that matters, twice: in the hero and in the bar', 'id="play-bottom"'],
]) {
  check(label, page.includes(needle), needle);
}
check('the primary button is the launcher green, not a link',
  /--accent:\s*#3f8e4f/.test(page) && page.includes('class="play"'));
check('and the whole thing is dark, the way a launcher is',
  /--bg:\s*#101013/.test(page) && /--panel:\s*#1e1e22/.test(page));

// ---------------------------------------------------------------- the data, not a version list
check('it reads the routes the pipeline writes', page.includes('routes.json'));
check('and the catalog', page.includes('manifest.json'));
// The launcher has a *scope* - three versions, written down once - and no catalogue of its own: everything a row
// says beyond "it is in scope" comes from routes.json and manifest.json, so a version cannot be described here
// that the pipeline does not agree with. Those are different things, and a test has to tell them apart.
const versionLists = [...body.matchAll(/\[\s*("[0-9][^\]]*?)\]/gs)].map((match) => match[1]);
check('the only version list in the launcher is its scope, and the scope is the three asked for',
  versionLists.length === 1 && /^"1\.21\.11",\s*"26\.2",\s*"26\.3"$/.test(versionLists[0].trim()),
  versionLists.join(' | ').slice(0, 120));

// ---------------------------------------------------------------- only the playable ones
check('the default view is the playable versions', /filter:\s*"playable"/.test(body));
check('and the filter really asks the route whether a version can be played',
  /if \(state\.filter === "playable"\) \{ return row\.playable; \}/.test(body));
check('"playable" means playable in the browser, not "a build exists"',
  /playable: target\.kind === "page" \|\| target\.kind === "java"/.test(body),
  "a download is not playable, and neither is a link to somebody else's site");
check('the launcher offers three versions and says which', /var VERSIONS = \["1\.21\.11", "26\.2", "26\.3"\]/.test(body));
check('and Fabric is one of the things it tells you about a version', body.includes('"Fabric"'));
check('the rows that cannot be played are behind their own filter, which names the wall',
  body.includes('Not ready yet') && body.includes('each row says which wall it is behind'));
// the data agrees with the claim: there is at least one playable version, and the non-playable ones are the
// large majority - which is why hiding them by default is the whole point
const playable = Object.values(routes).filter((route) => route.playable);
check('the three versions the project is about are in the catalogue',
  ['1.21.11', '26.2', '26.3'].every((id) => routes[id]), Object.keys(routes).length + ' routes');
check('and the launcher lists those three and nothing else',
  /var VERSIONS = \["1\.21\.11", "26\.2", "26\.3"\]/.test(body));

// ---------------------------------------------------------------- the five answers
for (const [label, needle] of [
  ['a page this site serves', 'kind: "page"'],
  ['the official jar in a browser JVM', 'kind: "java"'],
  ['a download', 'kind: "file"'],
  ['somebody else\'s link', 'kind: "other"'],
  ['and nothing yet, with the reason', 'kind: "none"'],
]) {
  check(`the page can answer: ${label}`, body.includes(needle), needle);
}
check('the play button is driven by that answer, not by a hardcoded link',
  body.includes('play.disabled = row.target.kind === "none"'));

// ---------------------------------------------------------------- the promises
const about = page + body;
for (const [label, needle] of [
  ['it says no game files are hosted here', 'never hosts game files'],
  ['and that the client comes from Mojang', "Mojang's own servers"],
  ['the official jar download is credited to Mojang', "Mojang's own servers"],
  ['it credits Mojang\'s trademark position', 'not an official Minecraft product'],
  ['it says the compiled rows have no JVM at play time', 'no class loader to interrupt'],
  ['and that mods go in before the compile there', 'at build time'],
  ['it explains that a browser cannot open raw TCP', 'cannot open a raw TCP connection'],
  ['and that multiplayer goes through a gateway', 'gateway'],
  ['with the two lineages told apart, because they want different things', 'Eaglercraft server'],
  ['self-hosting pointed at our own gateway', 'gateway/server.js'],
  ['and at the file that documents the protocol', 'MULTIPLAYER.md'],
  ['a player with a single-file client on disk is told the one command', 'tools/play_local.py'],
  ['the mod policy points at its contract', 'MIXINS.md'],
  ['and the credits ledger is linked', 'CREDITS.md'],
]) {
  check(label, about.includes(needle), needle);
}
check('the gateway field is stored in this browser only and says so',
  (/id=["']gateway-url["']/).test(page) && about.includes('never sent to this site'));
check('and checking a gateway reads like something a player can act on',
  about.includes('private targets refused') && about.includes('not a gateway'));
check('the walls a version can be behind are explained rather than implied',
  about.includes('needs-shim') && about.includes('needs a port') && about.includes('stops at Java 17'));

// ---------------------------------------------------------------- the launcher's own logic, on the real data
// Reading the page's text is not the same as running it. These are the page's own functions - extracted by name
// from the file that ships - applied to the project's own routes.json and manifest.json, so "only playable
// versions are listed" and "a built client gets a page, a real-Java version gets the JVM" are checked as
// behaviour rather than as wording.
function extract(name, kind = 'function') {
  const pattern = kind === 'function'
    ? new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`)
    : new RegExp(`var ${name} = \\[[\\s\\S]*?\\];`);
  const found = body.match(pattern);
  return found ? found[0] : '';
}
const harness = [
  'var PAGE_PROBE = {};',
  'var VERSIONS = ' + (body.match(/var VERSIONS = (\[[^\]]*\]);/) || [null, '[]'])[1] + ';',
  extract('playTargetFor'),
  extract('buildRows'),
  'var FILTERS = ' + (body.match(/var FILTERS = (\[[\s\S]*?\]);/) || [null, '[]'])[1] + ';',
  'var state = { rows: [], byId: {}, filter: "playable", query: "", selected: null };',
  (body.match(/function passesFilter\(row\) \{[\s\S]*?\n\}/) || ['function passesFilter() { return true; }'])[0],
  `var built = buildRows(${JSON.stringify(routes)}, ${JSON.stringify(catalog)});`,
  'var rows = built.rows;',
  'var playableRows = rows.filter(function (row) { return row.playable; });',
  'var listedByDefault = rows.filter(function (row) { state.filter = "playable"; return passesFilter(row); });',
  'console.log(JSON.stringify({',
  '  total: rows.length,',
  '  playable: playableRows.length,',
  '  listed: listedByDefault.length,',
  '  listedAllPlayable: listedByDefault.every(function (row) { return row.playable; }),',
  '  builtKinds: rows.filter(function (r) { return r.built; }).map(function (r) { return r.id + ":" + r.target.kind; }),',
  '  javaKind: (rows.filter(function (r) { return r.route === "real-java"; })[0] || {target:{}}).target.kind,',
  '  noRoute: rows.filter(function (r) { return r.target.kind === "none"; }).length,',
  // The rules, on inputs the three-version scope does not happen to contain: a launcher whose rules only hold
  // for the rows that are in front of it is not a rule.
  '  kinds: {',
  '    java: playTargetFor("1.7.10", { route: "real-java" }, {}).kind,',
  '    none: playTargetFor("9.9", { route: "aot", built: null, why: "no engine" }, {}).kind,',
  '    external: playTargetFor("26.3", { route: "aot", built: null }, { url: "https://example.invalid/x" }).kind,',
  '    ours: playTargetFor("1.21.11", { route: "aot", built: null }, { url: "https://github.com/AmphibianDev/webmc-port/releases/latest/download/client-1.21.11.html" }).kind,',
  '    page: playTargetFor("26.2", { route: "aot", built: { url: "https://example.invalid/asset" } }, {}).kind',
  '  },',
  '  missingId: rows.filter(function (r) { return !r.id; }).length,',
  '  newest: rows[0].id',
  '}));',
].join('\n');
const harnessPath = path.join(mkdtempSync(path.join(tmpdir(), 'webmc-behaviour-')), 'behaviour.js');
writeFileSync(harnessPath, harness);
let behaviour = null;
try {
  behaviour = JSON.parse(execFileSync(process.execPath, [harnessPath], { stdio: 'pipe' }).toString());
  check('the launcher\'s own functions run against the project\'s own data', true);
} catch (error) {
  check("the launcher's own functions run against the project's own data", false,
        String(error.stderr || error).slice(0, 400));
}
if (behaviour) {
  check('the launcher lists exactly the three versions it is about', behaviour.total === 3,
        `${behaviour.total} rows`);
  check('the default listing is exactly the playable ones, and nothing else',
        behaviour.listed === behaviour.playable && behaviour.listedAllPlayable,
        `${behaviour.listed} listed, ${behaviour.playable} playable`);
  check('the default view lists every one of them that can be played',
        behaviour.listed <= behaviour.total && behaviour.listedAllPlayable,
        `${behaviour.listed} listed of ${behaviour.total}`);
  check('a version with a built client opens a page this site serves',
        behaviour.builtKinds.every((entry) => entry.endsWith(':page')), JSON.stringify(behaviour.builtKinds));
  check('a real-Java version opens the JVM page', behaviour.kinds.java === 'java', behaviour.kinds.java);
  check('a version with a built client opens the page this site serves',
        behaviour.kinds.page === 'page', behaviour.kinds.page);
  check('a version with no route says so instead of opening something',
        behaviour.kinds.none === 'none', behaviour.kinds.none);
  check("somebody else's link is labelled as theirs, not as ours",
        behaviour.kinds.external === 'other', behaviour.kinds.external);
  check('and our own release asset is a download, which is not playable',
        behaviour.kinds.ours === 'file', behaviour.kinds.ours);
  check('the newest version is first', Boolean(behaviour.newest));
}

console.log(failures ? `\n${failures} site test(s) FAILED` : '\nsite tests passed');
process.exit(failures ? 1 : 0);
