#!/usr/bin/env bash
# The whole thing in one command: serve, open the game, open the host page, print the link.
#
#   ./stream/live.sh                 # 1.12.2 in a tab, then share your screen
#   ./stream/live.sh --tailscale     # also print the address for friends outside your network
set -euo pipefail
cd "$(dirname "$0")"
PORT="${WEBMC_STREAM_PORT:-8770}"
BIND="127.0.0.1"
EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --tailscale) BIND="0.0.0.0"; EXTRA+=(--tailscale) ;;
    --funnel) BIND="0.0.0.0"; WANT_FUNNEL=1 ;;
    *) EXTRA+=("$arg") ;;
  esac
done

python3 server.py --port "$PORT" --host "$BIND" >/tmp/webmc-stream.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.25
done

open "http://127.0.0.1:$PORT/host"
sleep 1
open "https://amphibiandev.github.io/webmc-port/#1.12.2" 2>/dev/null || true

LAN="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || python3 -c 'import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect(("10.255.255.255", 1))
a = s.getsockname()[0]
print(a if not a.startswith("127.") else "your-LAN-address")' 2>/dev/null || echo your-LAN-address)"
echo
echo "  ┌─ the links ──────────────────────────────────────────────"
echo "  │ on this machine : http://127.0.0.1:$PORT/          (watch)"
echo "  │                   http://127.0.0.1:$PORT/host       (host controls)"
echo "  │ friends (LAN)   : http://$LAN:$PORT/"
if command -v tailscale >/dev/null 2>&1; then
  echo "  │ friends (anywhere): http://$(tailscale ip -4 | head -1):$PORT/"
else
  echo "  │ friends (anywhere): brew install --cask tailscale, then rerun with --tailscale"
fi
echo "  └──────────────────────────────────────────────────────────"
echo
echo "  1. the launcher tab that just opened is the game - let 1.12.2 reach the title screen"
echo "  2. go fullscreen (green button), come back to the host page, press 'Share the screen'"
echo "  3. send the friends link"
echo
if [ "$WANT_FUNNEL" = "1" ]; then
  ./funnel.sh || true
fi
echo "  ctrl-c here stops the stream (the game tab keeps running)"
wait $SERVER
