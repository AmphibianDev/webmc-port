// Contract test for site/index.html: the page's inline script must parse, every element it
// reads must exist, and it must handle the catalog it will actually be given.
//
//   node tests/test_site.mjs
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
const scratch = path.join(mkdtempSync(path.join(tmpdir(), 'webmc-site-')), 'page.js');
writeFileSync(scratch, body);
try {
  execFileSync('node', ['--check', scratch], { stdio: ['ignore', 'ignore', 'pipe'] });
  check('the inline script parses', true);
} catch (error) {
  check('the inline script parses', false, String(error.stderr || error).slice(0, 200));
}

const ids = [...new Set([...body.matchAll(/el\("([\w-]+)"\)/g)].map((m) => m[1]))];
const present = new Set([...page.matchAll(/id="([\w-]+)"/g)].map((m) => m[1]));
const missing = ids.filter((id) => !present.has(id));
check(`every id the script reads exists (${ids.length})`, missing.length === 0, missing.join(', '));

for (const [label, needle] of Object.entries({
  'it reads the generated catalog': 'fetch("manifest.json"',
  'it says when there is no catalog yet': 'Could not read the catalog',
  'it claims nothing about hosting game files': page.includes('hosts no game files'),
  'it names the tool that explains each verdict': page.includes('tools/plan.py'),
})) {
  check(label, Boolean(needle));
}

// What the launcher was asked to be: empty until you ask for something, any version findable, and a
// route for the one you pick.
check('it starts empty, with a note instead of a list',
  page.includes('data-state="empty"') && page.includes('the launcher starts empty'));
check('and does not load the catalog until asked',
  !/^\s*loadCatalog\(\);\s*$/m.test(body), 'a top-level loadCatalog() would fill the page on sight');
const codeOnly = body.replace(/\/\/[^\n]*/g, '');
check('it searches every version in the catalog, not a curated few',
  codeOnly.includes('state.catalog.versions')
  // a curated list would be an array literal holding several version-looking strings
  && !/\[[^\]]*("[0-9]+\.[0-9]+[^"]*"[^\]]*){2,}\]/.test(codeOnly),
  'a hardcoded version list found in the script');
check('with type filters for releases, snapshots, betas and alphas',
  (page.match(/class="kind"/g) || []).length === 4 && body.includes('selectedKinds'));
check('it ranks an exact match first', body.includes('id === query') && body.includes('indexOf(query) === 0'));
check('it caps the list instead of dumping 857 rows', body.includes('MAX_ROWS'));

// The four routes, each with its own tag and its own honest one-liner.
for (const [label, needle] of Object.entries({
  'it routes real Java through the launch page': '▶ real Java',
  'it links a prebuilt port where it lives': '▶ prebuilt port',
  'it links a client we compiled': '▶ our build',
  'it says queued when there is no route': 'queued',
  'and offers to request that build': 'request a build',
  'the play link carries version, jar and main class': 'play.html?version=',
  'the official jar is credited to Mojang': "Mojang's own servers",
})) {
  check(label, page.includes(needle));
}
check('opening a card warms the cache the launch page reads',
  body.includes('function warmUp') && body.includes('webmc-clients-v1') && body.includes('rel = "prefetch"'));
check('the launch url carries the asset index too', body.includes('&assetIndex='));
check('the build request opens the build workflow, not the catalog one',
  body.includes('build-client.yml'));

// Fewest clicks to be in the game: a row starts a version, Enter starts the top result, and a saved
// session starts again with one click and no catalog fetch at all.
check('one function starts a version, so the paths cannot drift',
  (body.match(/function play\(/g) || []).length === 1
  && body.includes('play(version);') && body.includes('play(result.matches[0])'));
check('clicking a row starts the version', /row\.addEventListener\("click", function \(\) \{ play\(version\); \}\)/.test(body));
check('details is one click away and does not start anything',
  body.includes('stopPropagation') && body.includes('openCard(version.id)'));
check('Enter starts the top result', body.includes('play(result.matches[0])'));
check('a saved session is one click, from localStorage alone',
  body.includes('webmc.last') && body.includes('showContinue()'));
check('the continue chip needs no catalog', !/showContinue[\s\S]{0,400}loadCatalog/.test(
  body.slice(body.indexOf('function showContinue'), body.indexOf('function nearestPlayable'))));
check('the version you can start outranks the one you cannot',
  body.includes('playable') && body.includes('routeOf(b).play ? 1 : 0'));

// A queued version must explain itself in plain words and point somewhere that runs.
check('it says what a port actually is',
  page.includes('A port is what makes a version run in a browser'));
check('it says what is missing for the middle versions',
  page.includes('LWJGL 3') && page.includes('1.13 and newer'));
check('each blocked version\'s reason comes from its own metadata',
  body.includes('version.jvm_why') && page.includes('verdict from the release date'));
check('a queued version offers the nearest version that does run',
  body.includes('nearestPlayable') && body.includes('instead'));
check('the nearest one prefers the same game line', body.includes('sameFamily'));
check('no build button for a version with no engine',
  body.includes('version.verdict === "build" ? DISPATCH : null'));
check('and it says why there is no build button',
  page.includes('no engine has a profile for'));
check('the launch url passes the jar and the main class',
  body.includes('jar=" + encodeURIComponent(version.jar_url)') && body.includes('main=" + encodeURIComponent'));

// "download whatever version we want": every catalog entry knows its Mojang metadata document, so
// the official jar can be resolved for any version, not only the ones that already have a route.
check('it can resolve any version\'s official jar from Mojang metadata',
  body.includes('piston-meta') && body.includes('downloads.client'));
check('it downloads with a real progress bar and a saved file',
  body.includes('getReader') && body.includes('createObjectURL') && body.includes('a.download'));

// Fabric: real meta data, a kit that can be built in the browser, and no pretending that a kit can
// be launched here.
// Fabric is parked by request and asserted absent in tests/test_fabric.py.
check('a deep link opens one version directly', body.includes('window.location.hash'));
check('clicking a version opens its install card', body.includes('openCard'));

console.log(failures === 0 ? '\nsite tests passed' : `\n${failures} site test(s) FAILED`);
process.exit(failures === 0 ? 0 : 1);
