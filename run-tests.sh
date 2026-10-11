#!/usr/bin/env bash
# Everything that can be checked without Mojang, without a JDK and without a browser.
set -uo pipefail
cd "$(dirname "$0")"
failed=0
echo "== version pipeline"; python3 tests/test_pipeline.py || failed=1
echo; echo "== launcher page"; node tests/test_site.mjs || failed=1
echo; echo "== LWJGL surface (the gap to real Java on 1.13+)"; python3 tests/test_lwjgl_surface.py || failed=1
echo; echo "== catalog, on the fixture"; python3 tools/plan.py --manifest fixtures/manifest.json --kinds release,snapshot,old_beta --limit 20 || failed=1
echo
[ "$failed" = "0" ] && echo "all tests passed" || echo "SOME TESTS FAILED"
exit "$failed"
