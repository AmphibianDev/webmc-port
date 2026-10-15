// Contract test for site/index.html: the launcher page.
//
//   node tests/test_site.mjs
//
// The page is checked for the things a launcher has to get right and nothing else: it parses, every
// element it reads exists, it reads the three files the pipeline writes, and the five answers it can give
// about a version (a page this site serves, a JVM in the page, a download, someone else's link, or
// nothing yet) are all present and honest. The list is curated by default and complete on demand - a
// launcher that dumps 900 versions at someone is a catalogue, not a launcher.
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const page = readFileSync(path.join(ROOT, 'site', 'index.html'), 'utf8');
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
  execFileSync('node', ['--check', scratch], { stdio: ['ignore', 'ignore', 'pipe'] });
  check('the inline script parses', true);
} catch (error) {
  check('the inline script parses', false, String(error.stderr || error).slice(0, 300));
}

const ids = [...new Set([...body.matchAll(/el\("([\w-]+)"\)/g)].map((m) => m[1]))];
const present = new Set([...page.matchAll(/id="([\w-]+)"/g)].map((m) => m[1]));
const missing = ids.filter((id) => !present.has(id));
check(`every element the script reads exists (${ids.length})`, missing.length === 0, missing.join(', '));

// ---------------------------------------------------------------- the data it reads
for (const [label, needle] of Object.entries({
  'it reads the catalogue the catalog job writes': 'fetch("manifest.json"',
  'it reads the launch plans': 'fetch("launch.json"',
  'it reads how each version runs': 'fetch("routes.json"',
  'and says so when the catalogue is missing': 'The catalogue is not here',
})) {
  check(label, page.includes(needle), needle);
}

// ---------------------------------------------------------------- the five answers
for (const [label, needle] of Object.entries({
  'a client this site serves is a play link': 'kind: "page"',
  'the real-Java row launches a JVM in the page': 'play.html?version=',
  'a release asset is described as a download': 'kind: "file"',
  'someone else\'s build is linked where it lives': 'kind: "other"',
  'and a version with no route says so instead of pretending': 'kind: "none"',
  'the play link carries the mode and the mods': '&mode=" + encodeURIComponent(mode)',
  'the choice is remembered per version and mode': 'webmc.mods.',
  'a dead end offers the nearest version that runs': 'function nearest(',
  'and a way to ask for a build': 'request a build',
})) {
  check(label, body.includes(needle), needle);
}

// ---------------------------------------------------------------- launcher behaviour
check('the list is curated by default, not the whole catalogue',
  body.includes('MAX_ROWS') && body.includes('state.all || state.query ? MAX_ROWS : 12'),
  'a launcher that opens with 900 rows is a catalogue');
check('and the whole catalogue is one click away', page.includes('show every version in the catalogue'));
check('startable versions sort to the top', body.includes('(startable(b) ? 1 : 0) - (startable(a) ? 1 : 0)'));
check('it can filter to only what starts here',
  page.includes('data-kind="playable"') && body.includes('wanted.playable'));
check('it keeps the software kind filters', (page.match(/class="chip/g) || []).length >= 4);
check('a search box with a clear button', body.includes('el("clear")') && page.includes('placeholder="Search versions"'));
check('a version row says whether it starts', body.includes('target.url ? "▶" : "·"'));
check('it warms nothing it does not need: no catalog fetch until the page asks',
  !/^\s*loadCatalog\(\);\s*$/m.test(body), 'a top-level loadCatalog() would fill the page on sight');
check('the header says how many versions there are, and how many start',
  body.includes('startable here') && body.includes('versions.length'));
check('a deep link opens one version', body.includes('window.location.hash'));

// ---------------------------------------------------------------- what a card says
for (const [label, needle] of Object.entries({
  'the route comes from the pipeline, not from the page': 'route.route',
  'the engine and its licence are shown': 'route.engine_license',
  'and the mod policy': 'route.mods || "—"',
  'the mods picker exists where a plan offers mods': 'function chosenMods(',
  'and an engine that can bake says how to add one': 'This engine can bake a mod set',
  'the official jar download is credited to Mojang': "Mojang's servers",
})) {
  check(label, body.includes(needle), needle);
}

// ---------------------------------------------------------------- multiplayer, honestly
check('the launcher explains that a browser cannot open raw TCP',
  page.includes('cannot open a raw TCP connection') || page.includes('cannot open a raw TCP'));
check('and that multiplayer goes through a relay node', page.includes('relay node') && page.includes('wss://'));
check('with the two ways to fix a failed connection named',
  page.includes('relay being') && page.includes('Refusing') === false);
check('and self-hosting pointed at', page.includes('apps/bridge') && page.includes('GAIUS_RELAY_PUBLIC_URL'));

// ---------------------------------------------------------------- the promises that matter
check('it claims nothing about hosting game files', page.includes('No game files are hosted here'));
check('it says the compiled rows have no JVM at play time',
  page.includes('no JVM at play time') && page.includes('baked into the client'));
check('it credits Mojang\'s trademark position',
  page.toLowerCase().includes('not an official minecraft product'));
check('it does not hardcode a version list',
  !/\[[^\]]*("[0-9]+\.[0-9]+[^"]*"[^\]]*){2,}\]/.test(body.replace(/\/\/[^\n]*/g, '')),
  'a hardcoded version list found in the script');
check('the footer names the tools that decide the routes',
  page.includes('tools/route.py') && page.includes('tools/launchplan.py'));

console.log(failures === 0 ? '\nsite tests passed' : `\n${failures} site test(s) FAILED`);
process.exit(failures === 0 ? 0 : 1);
