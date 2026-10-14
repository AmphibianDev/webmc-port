#!/usr/bin/env bash
# Everything that can be checked without Mojang, without a JDK and without a browser.
set -uo pipefail
cd "$(dirname "$0")"
failed=0
echo "== version pipeline"; python3 tests/test_pipeline.py || failed=1
echo; echo "== launcher page"; node tests/test_site.mjs || failed=1
echo; echo "== the boot screen (vanilla-styled, injected into a client)"; python3 tests/test_aot_shell.py || failed=1
echo; echo "== a compiled client, packed into a page a player can open"; python3 tests/test_pack_client.py || failed=1
echo; echo "== one version in, one route out"; python3 tests/test_workflows.py || failed=1
echo; echo "== version profiles for any version, newest snapshot included"; python3 tests/test_aot_profile.py || failed=1
echo; echo "== preparing the newest snapshot inside an engine"; python3 tests/test_aot_snapshot.py || failed=1
echo; echo "== the workflows (a duplicate key cost two days of builds)"; python3 tests/test_workflows.py || failed=1
echo; echo "== reading a snapshot build's outcome out of git"; python3 tests/test_snapshot_status.py || failed=1
echo; echo "== the launch plan (rows, modes, mods)"; python3 tests/test_launchplan.py || failed=1
echo; echo "== our own loader (java 8 jar: contract always, behaviour when a JDK is present)"; python3 tests/test_loader.py || failed=1
echo; echo "== what Fabric supports"; python3 tests/test_fabric.py || failed=1
echo; echo "== what a browser JVM can run (the whole Java family)"; python3 tests/test_jvm_route.py || failed=1
echo; echo "== vendored libraries and live links"; python3 tests/test_vendor_libs.py || failed=1
echo; echo "== the loader boot test (java coherent, js backed)"; python3 lwjgl3-webgl/test/test_boot_assets.py || failed=1
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
