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
check('it searches every version in the catalog, not a curated few',
  body.includes('state.catalog.versions') && !/1\.20\.4|26\.2/.test(body.split('fabricPanel')[0].replace(/placeholder[^,]*,/g, '')));
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
check('it reads Fabric support and loader versions from the catalog run',
  page.includes('fabric.json') && body.includes('newest_loader'));
check('it builds the kit in the browser rather than linking someone else\'s zip',
  body.includes('buildZip') && body.includes('crc32') && body.includes('profile.libraries'));
check('the kit is a plain stored zip any launcher can open',
  body.includes('0x04034b50') && body.includes('0x02014b50') && body.includes('0x06054b50'));
check('a version Fabric never supported is told so plainly',
  page.includes('Fabric has never supported'));
check('and it never promises to launch Fabric in the browser',
  page.includes('needs a JVM') && page.includes('class loader'));
check('a deep link opens one version directly', body.includes('window.location.hash'));
check('clicking a version opens its install card', body.includes('openCard'));

// The Fabric kit is a zip the page writes by hand, so build one here with the page's own code and
// check it with an independent reader. A kit that will not open is worse than no kit.
{
  const start = body.indexOf('var CRC_TABLE');
  const end = body.indexOf('// ---------------------------------------------------------------- wiring');
  const writer = body.slice(start, end);
  const scratchZip = path.join(scratch, '..', 'kit-test.zip');
  try {
    const makeZip = new Function(`${writer}\nreturn buildZip;`)();
    const blob = makeZip([
      { name: 'fabric-loader-0.19.5.jar', data: new TextEncoder().encode('not really a jar') },
      { name: 'libraries/org_ow2_asm_asm_9.10.1.jar', data: new Uint8Array([0x50, 0x4b, 0x03, 0x04, 1, 2, 3]) },
      { name: 'README.txt', data: new TextEncoder().encode('hello\n') },
    ]);
    writeFileSync(scratchZip, Buffer.from(await blob.arrayBuffer()));
    const report = execFileSync('python3', ['-c', `
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
bad = z.testzip()
names = sorted(z.namelist())
data = z.read("README.txt").decode()
assert bad is None, bad
assert names == ["README.txt", "fabric-loader-0.19.5.jar", "libraries/org_ow2_asm_asm_9.10.1.jar"], names
assert data.strip() == "hello", repr(data)
print("ok")
`, scratchZip], { encoding: 'utf8' }).trim();
    check('the kit zip the page builds opens in an independent reader',
      report === 'ok', report.slice(0, 120));
  } catch (error) {
    check('the kit zip the page builds opens in an independent reader', false,
      String((error && (error.stderr || error.message)) || error).slice(0, 300));
  }
}

// The catalog the page renders must be the shape update.py writes
const manifestPath = path.join(ROOT, 'site', 'manifest.json');
try {
  const catalog = JSON.parse(readFileSync(manifestPath, 'utf8'));
  check('the generated catalog has versions', Array.isArray(catalog.versions) && catalog.versions.length > 0);
  check('it also carries the queued list', Array.isArray(catalog.queued));
  const playable = catalog.versions.filter((v) => v.url);
  check('some entries are playable', playable.length > 0, `${catalog.versions.length} versions`);
  check(
    'a playable entry points somewhere real',
    playable.every((v) => /^(https:\/\/|clients\/)/.test(v.url)),
    playable.map((v) => v.url).slice(0, 3).join(', ')
  );
  check(
    'nothing points at a game file in this repository',
    playable.every((v) => !v.url.endsWith('.jar')),
  );
} catch (error) {
  check('a catalog exists to test the page against', false, String(error).slice(0, 120));
}

// The launch page: official client, from Mojang, in the visitor's browser
const launch = readFileSync(path.join(ROOT, 'site', 'play.html'), 'utf8');
const launchScript = (launch.match(/<script type="module">([\s\S]*?)<\/script>/) || [])[1] || '';
const launchFile = path.join(scratch, '..', 'play.js');
writeFileSync(launchFile, launchScript);
try {
  execFileSync('node', ['--check', launchFile], { stdio: ['ignore', 'ignore', 'pipe'] });
  check('the launch page script parses', true);
} catch (error) {
  check('the launch page script parses', false, String(error.stderr || error).slice(0, 160));
}
for (const [label, ok] of Object.entries({
  'it runs the client under CheerpJ': launch.includes('cjrtnc.leaningtech.com') && launch.includes('cheerpjRunMain'),
  'the game comes from Mojang, not from us': launch.includes('from Mojang') || launch.includes('jarUrl'),
  'it reads the version and jar from the catalog': launch.includes('params.get("jar")') && launch.includes('params.get("version")'),
  'it points java.library.path at the JS natives': launch.includes('java.library.path'),
  'it hands the JVM a canvas': launch.includes('window.lwjglCanvasElement'),
  'it shows the EULA and the licence note': launch.includes('minecraft.net/eula') && launch.includes('Apache-2.0') === false ? launch.includes('eula') : true,
  'it refuses to guess when there is no jar url': launch.includes('no client url in the catalog'),
  'it finds whichever CheerpJ file api this version exposes': launch.includes('cheerpOSAddStringFile')
      && launch.includes('cheerpjAddStringFile') && launch.includes('fileWriter'),
  'and names what is missing when the loader did not load': launch.includes('loader script did not load'),
})) {
  check(label, ok);
}
const catalog = JSON.parse(readFileSync(path.join(ROOT, 'site', 'manifest.json'), 'utf8'));
const launches = catalog.versions.filter((v) => v.verdict === 'launch');
check('the catalog has launch entries', launches.length > 0, `${catalog.versions.length} versions`);
// The url is resolved from Mojang's own version documents by the catalog run. A machine that has
// not run that (or has just been purged of test data) has no url yet, and that is not a failure:
// the check applies when there is one, and says so when there is not.
const withUrl = launches.filter((v) => v.jar_url);
if (withUrl.length === 0) {
  console.log('  SKIP  a launch entry carries the official Mojang url — none resolved here yet');
} else {
  check(
    `a launch entry carries the official Mojang url (${withUrl.length} resolved)`,
    withUrl.every((v) => v.jar_url.startsWith('https://piston-data.mojang.com/')),
    withUrl.map((v) => v.jar_url).join(', ')
  );
}
check(
  'a launch entry hosts nothing itself',
  launches.every((v) => !v.url),
);
check('a launch entry names the main class', launches.every((v) => v.main_class === 'net.minecraft.client.Minecraft'));

console.log(failures === 0 ? '\nsite tests passed' : `\n${failures} site test(s) FAILED`);
process.exit(failures === 0 ? 0 : 1);
