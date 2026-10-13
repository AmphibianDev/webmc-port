#!/usr/bin/env bash
# One command to stream Minecraft from this machine to a browser.
#
#   ./stream/host.sh                     # local network only
#   ./stream/host.sh --tailscale         # also print the tailnet address for friends elsewhere
#
# Nothing is installed: the server is Python's standard library and the video is WebRTC between two
# browsers. What it will ask for, once: Screen Recording (for the browser capturing the window) and
# Accessibility (for this process to press keys and move the mouse).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${WEBMC_STREAM_PORT:-8770}"
BIND="127.0.0.1"
WANT_TAILSCALE=0
for arg in "$@"; do
  case "$arg" in
    --tailscale) WANT_TAILSCALE=1; BIND="0.0.0.0" ;;
    --port=*) PORT="${arg#*=}" ;;
    --lan) BIND="0.0.0.0" ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

echo "starting the stream server on port $PORT (input injection on)"
python3 server.py --port "$PORT" --host "$BIND" "$@" &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  sleep 0.25
done

open "http://127.0.0.1:$PORT/host" 2>/dev/null || true
echo
echo "  host page opened in your browser - press 'Share a window' and pick Minecraft"
echo "  friends on this network: $(python3 - <<'PY'
import socket
found = []
try:
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        found.append(info[4][0])
except Exception:
    pass
print(('http://' + found[0] + ':8770/') if found else 'run ifconfig to find this machine')
PY
)"
if [ "$WANT_TAILSCALE" = "1" ]; then
  if command -v tailscale >/dev/null; then
    echo "  friends anywhere (tailnet): http://$(tailscale ip -4 | head -1):$PORT/"
    echo "    share it with up to two others on the free plan: tailscale.com/pricing"
  else
    echo "  for friends outside this network: brew install --cask tailscale, sign in, then rerun with --tailscale"
  fi
fi
echo "  press ctrl-c here to stop"
wait $SERVER
