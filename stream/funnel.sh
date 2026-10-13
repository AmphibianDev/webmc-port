#!/usr/bin/env bash
# A real web address for the stream, without renting anything.
#
#   ./stream/funnel.sh
#
# Tailscale Funnel publishes a local service at https://<this-machine>.<your-tailnet>.ts.net/ on the
# public internet. It is on every plan, it is the front door only - the video itself goes straight
# between the two browsers - and the machine running the game is still this one, so no host is being
# used as a game server. That distinction is the whole reason the earlier version got suspended: the
# runners did the work there, and here they do not.
#
# Bandwidth limits: Funnel traffic is bandwidth-limited by Tailscale. Since only the handshake and the
# keystrokes pass through it (a few kilobytes a second) and the picture is peer to peer, that limit is
# not what your friends' frame rate depends on.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${WEBMC_STREAM_PORT:-8770}"

if ! command -v tailscale >/dev/null; then
  cat <<'MISSING'
  Tailscale is not installed. Two commands, then this script works:

      brew install --cask tailscale     # then sign in from the menu bar
      ./stream/funnel.sh

  The free plan covers three users, which is the "me and two friends" case.
MISSING
  exit 1
fi

if ! tailscale status >/dev/null 2>&1; then
  echo "  Tailscale is installed but not signed in: open the app and log in, then rerun."
  exit 1
fi

echo "  publishing http://127.0.0.1:$PORT on the public internet…"
# the modern spelling takes a proxy target; the older one took just a port. Try both.
if ! tailscale funnel --bg --https=443 "http://127.0.0.1:$PORT" 2>/dev/null; then
  tailscale funnel --bg "$PORT"
fi

NAME="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("Self") or {}).get("DNSName","").rstrip("."))' 2>/dev/null || true)"
echo
if [ -n "$NAME" ]; then
  echo "  ┌─ public address ────────────────────────────────────────"
  echo "  │ https://$NAME/"
  echo "  └─────────────────────────────────────────────────────────"
  echo "  send that to your friends; the page they get is the lobby, and /watch is the player"
else
  tailscale funnel status
fi
echo
echo "  to stop publishing: tailscale funnel --bg off"
