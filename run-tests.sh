#!/usr/bin/env bash
# Everything that can be checked without Mojang, without a JDK and without a browser.
set -uo pipefail
cd "$(dirname "$0")"
failed=0
echo "== version pipeline"; python3 tests/test_pipeline.py || failed=1
echo; echo "== launcher page"; node tests/test_site.mjs || failed=1
echo; echo "== what Fabric supports"; python3 tests/test_fabric.py || failed=1
echo; echo "== what a browser JVM can run (the whole Java family)"; python3 tests/test_jvm_route.py || failed=1
echo; echo "== the build downloader (GitHub only, verified on arrival)"
python3 tests/test_fetch_builds.py || failed=1
echo; echo "== the local launcher page"
python3 tests/test_serve_builds.py || failed=1
echo; echo "== the mod api behaviour (events, mods, overlay, keybinds)"
node mods/tests/test_api.mjs || failed=1
echo; echo "== the injector (JavaScript mods, any build)"
python3 mods/tests/test_injector.py || failed=1
echo; echo "== the mod loader (build-time Fabric, for workspaces)"
python3 mods/tests/test_workspace_loader.py || failed=1
echo; echo "== the mod planner (jar in, build plan out)"
python3 mods/tests/test_loader.py || failed=1
echo; echo "== LWJGL surface (the gap to real Java on 1.13+)"; python3 tests/test_lwjgl_surface.py || failed=1
echo; echo "== verdicts for the target versions"; python3 tools/targets.py >/dev/null || failed=1
echo; echo "== catalog, on the fixture"; python3 tools/plan.py --manifest fixtures/manifest.json --kinds release,snapshot,old_beta --limit 20 || failed=1
echo
[ "$failed" = "0" ] && echo "all tests passed" || echo "SOME TESTS FAILED"
exit "$failed"
