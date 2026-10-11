#!/usr/bin/env bash
# Everything that can be checked without Mojang, without a JDK and without a browser.
set -uo pipefail
cd "$(dirname "$0")"
failed=0
echo "== pipeline"
python3 tests/test_pipeline.py || failed=1
echo
echo "== launcher page"
node tests/test_site.mjs || failed=1
echo
echo "== catalog renders against the real fixture"
python3 tools/plan.py --manifest fixtures/manifest.json --kinds release,snapshot,old_beta --limit 20 || failed=1
echo
[ "$failed" = "0" ] && echo "all tests passed" || echo "SOME TESTS FAILED"
exit "$failed"
