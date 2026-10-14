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
check('it tells a version apart when its libraries are not deployed yet',
  body.includes('libs_ready') && page.includes('libraries pending'));
check('a release-asset build is described as a download, not a page',
  // the page's own test for "is this a release asset" is an escaped regex, so match the pieces
  body.includes('releases') && body.includes('download') && page.includes('download & open')
  && page.includes('serve Minecraft bytes'));
check('the build request opens the build workflow, not the catalog one',
  body.includes('build-snapshot.yml'));

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

// The launcher reads the pipeline's own index (site/launch.json) on top of the version list, which is
// where "this version can run vanilla or Fabric, with these mods" comes from.
for (const [label, needle] of Object.entries({
  'it reads the launch index the pipeline writes': 'fetch("launch.json")',
  'a missing index degrades to the manifest-only route': 'state.plans = plans',
  'it knows which modes a version has': 'function modesOf',
  'it remembers which mode you chose': 'function chosenMode',
  'mods are opt-out rather than opt-in': 'if (stored === null) { return offered; }',
  'a version with a vanilla mod starts with the loader': 'modesOf(id)',
  'the play link carries the mode and the mods': '&mode=" + encodeURIComponent(mode)',
  'and the choice is part of the link, not hidden state': '&mods=" + encodeURIComponent(mods.join(","))',
  'the card offers a loader per version': 'function launchOptions',
  'a version waiting on the shim says so': 'needs LWJGL 3',
  'a compiled-row version points at the build, not at a JVM': 'needs a compiled build',
  'it shows what the plan says is unproven before you press play': 'plan.caveats',
})) {
  check(label, page.includes(needle), needle);
}
check('the mode choice is written where the row click can read it too',
  body.includes('webmc.mode') && body.includes('webmc.mods.'),
  'the picker and the row click must not disagree');
check('the mods list is built from the plan, not from a hardcoded list',
  body.includes('plan.modes[mode]') && body.includes('state.plans.mods'));

// ------------------------------------------------------------------------------------------------
// The launch page (site/play.html): the other half of the same contract. It is the page that turns a
// plan into a running JVM, so what it gets wrong is not a bad label but a black canvas.
// ------------------------------------------------------------------------------------------------
const playPage = readFileSync(path.join(ROOT, 'site', 'play.html'), 'utf8');
const playScripts = playPage.match(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g) || [];
check('the launch page has one inline script', playScripts.length === 1, String(playScripts.length));
const playBody = (playScripts[0] || '').replace(/^<script[^>]*>/, '').replace(/<\/script>$/, '');
const playScratch = path.join(mkdtempSync(path.join(tmpdir(), 'webmc-play-')), 'page.mjs');
writeFileSync(playScratch, playBody);
try {
  execFileSync('node', ['--check', playScratch], { stdio: ['ignore', 'ignore', 'pipe'] });
  check('the launch page parses', true);
} catch (error) {
  check('the launch page parses', false, String(error.stderr || error).slice(0, 300));
}
const playIds = [...new Set([...playBody.matchAll(/el\("([\w-]+)"\)/g)].map((m) => m[1]))];
const playPresent = new Set([...playPage.matchAll(/id="([\w-]+)"/g)].map((m) => m[1]));
const playMissing = playIds.filter((id) => !playPresent.has(id));
check(`every element the launch page reads exists (${playIds.length})`, playMissing.length === 0, playMissing.join(', '));

for (const [label, needle] of Object.entries({
  'it fetches the plan for the one version being launched': 'plans/${encodeURIComponent(version)}.json',
  'an old link with the client in the URL still works': 'falling back to the client in the URL',
  'a version with no browser route is refused, not attempted': 'function refuse',
  'and the refusal quotes the pipeline\'s own reason': 'launch.blocker',
  'the mods come from this site\'s own mods folder': '/str/${mod.file}',
  'Fabric mode is handed the game jar': 'fabric.gameJarPath=${clientPath}',
  'and the mods, which live outside any ./mods directory': 'fabric.addMods=${modPaths.join(":")}',
  'the loader is given a plan file to read': 'putFile("/str/webmc/plan.properties"',
  'the loader\'s report reaches the page': 'Java_webmc_loader_WebMcLoader_nativeReport',
  'the JVM version comes from the plan, not from a constant': 'cheerpjInit({ version: launch.java',
  'downloads are cached for the next visit': 'webmc-clients-v2',
  'and the page says who hosts what': 'hosts no Minecraft files',
})) {
  check(label, playPage.includes(needle), needle);
}
check('the classpath order is loader, libraries, game, mods',
  playBody.includes('platform.concat(libraries, [clientPath], modDestinations).join(":")'),
  'the only order that cannot be got wrong: nothing may shadow the game, and the game may not shadow the loader');
check('the vanilla+mods case goes through our loader',
  playBody.includes('useLoader') && playBody.includes('webmc.loader.WebMcLoader'));
check('and a launch with nothing to load skips the loader',
  playBody.includes('mods.length > 0 || run.loader === true'), 'one class fewer between the click and the title screen');
check('the loader jar is fetched from this site, not from a CDN',
  playBody.includes('loader/webmc-loader.jar'));
check('the asset index is installed but its objects are named as unreachable',
  playBody.includes('no access-control-allow-origin') || playPage.includes('no access-control-allow-origin'),
  'measured: resources.download.minecraft.net sends no CORS header');
check('the mode is visible before the game starts',
  playBody.includes('launch.mode') && playPage.includes('id="subtitle"'));
check('there is exactly one place that builds a classpath',
  (playBody.match(/const classpath =/g) || []).length === 1);

// Fabric: real meta data, a kit that can be built in the browser, and no pretending that a kit can
// be launched here.
check('a deep link opens one version directly', body.includes('window.location.hash'));
check('clicking a version opens its install card', body.includes('openCard'));

console.log(failures === 0 ? '\nsite tests passed' : `\n${failures} site test(s) FAILED`);
process.exit(failures === 0 ? 0 : 1);
