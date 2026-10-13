#!/usr/bin/env bash
# Put the session on a real web address, with the same reasoning as the browser streamer: the machine
# that runs the game publishes itself, and no host is asked to do the work.
set -euo pipefail
PORT="${1:-8080}"

if ! command -v tailscale >/dev/null; then
  echo "  install Tailscale (free plan): curl -fsSL https://tailscale.com/install.sh | sh"
  echo "  then: sudo tailscale up   and rerun with --publish"
  exit 1
fi
if ! tailscale status >/dev/null 2>&1; then
  echo "  Tailscale is not signed in: sudo tailscale up, then rerun"
  exit 1
fi

sudo tailscale funnel --bg --https=443 "http://127.0.0.1:$PORT" 2>/dev/null \
  || sudo tailscale funnel --bg "$PORT"
NAME="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print((json.load(sys.stdin).get("Self") or {}).get("DNSName","").rstrip("."))' 2>/dev/null || true)"
echo
if [ -n "$NAME" ]; then
  echo "  public session: https://$NAME/"
else
  tailscale funnel status
fi
