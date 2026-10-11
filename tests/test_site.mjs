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
  'it has tabs and panes': page.includes('showTab("play")') && page.includes('data-tab="play"') && page.includes('data-pane="help"'),
  'it offers play links from the catalog': /entry\.url/.test(body),
  'it says when there is no catalog yet': page.includes('no catalog yet'),
  'it explains the porting bottleneck': page.includes('needs-port') && page.includes('queued'),
  'it claims nothing about hosting game files': page.includes('hosts no Minecraft files'),
  'it shows the commands that drive the pipeline': page.includes('tools/update.py --write'),
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

console.log(failures === 0 ? '\nsite tests passed' : `\n${failures} site test(s) FAILED`);
process.exit(failures === 0 ? 0 : 1);
