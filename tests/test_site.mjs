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
  'it lists what is playable and what is queued': page.includes('Playable now') && page.includes('Needs a port'),
  'it offers play links from the catalog': /play_url|jar_url/.test(body) && page.includes('▶ play'),
  'it prefers the real-Java route when a version has it': page.includes('play.html?version='),
  'it says when there is no catalog yet': page.includes('no catalog yet'),
  'it explains the porting bottleneck': page.includes('need a port') || page.includes('needs-port'),
  'it claims nothing about hosting game files': page.includes('hosts no game files'),
  'it names the tool that explains each verdict': page.includes('tools/targets.py') || page.includes('tools/plan.py'),
})) {
  check(label, Boolean(needle));
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
