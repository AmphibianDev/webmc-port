#!/usr/bin/env bash
# Turn a fresh Ubuntu VM into a stream host. Run it on the VM; see README.md.
#
#   TAILSCALE_AUTHKEY=tskey-auth-... bash provision.sh [version]
#
# It installs Xvfb, Chrome and Tailscale, starts the stream server, publishes it with a funnel, and
# runs the launcher plus the host page inside Chrome on the virtual display, with the capture source
# pre-selected so the VM streams its own screen.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${1:-1.12.2}"
PORT="${WEBMC_STREAM_PORT:-8770}"
LAUNCHER="https://amphibiandev.github.io/webmc-port/#$VERSION"

echo "== installing what a headless host needs"
sudo apt-get update -qq
sudo apt-get install -y -qq xvfb x11-utils python3 curl ca-certificates fonts-dejavu >/dev/null
if ! command -v google-chrome >/dev/null; then
  ARCH="$(dpkg --print-architecture)"
  if [ "$ARCH" = "arm64" ]; then
    echo "  ! Google Chrome has no arm64 .deb; installing Chromium from the distribution instead"
    sudo apt-get install -y -qq chromium-browser >/dev/null || sudo apt-get install -y -qq chromium >/dev/null
    BROWSER="$(command -v chromium-browser || command -v chromium)"
  else
    curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    sudo apt-get install -y -qq /tmp/chrome.deb >/dev/null
    BROWSER="$(command -v google-chrome)"
  fi
else
  BROWSER="$(command -v google-chrome)"
fi
echo "  browser: $BROWSER"

echo "== starting a virtual display"
pgrep -f "Xvfb :99" >/dev/null || (Xvfb :99 -screen 0 1280x720x24 >/tmp/xvfb.log 2>&1 &)
sleep 2
export DISPLAY=:99

echo "== starting the stream server (input enabled, funnel follows)"
cd ..
nohup python3 server.py --port "$PORT" --host 0.0.0.0 >/tmp/webmc-stream.log 2>&1 &
sleep 2
curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && echo "  server is up"

if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
  echo "== publishing it with a funnel"
  if ! command -v tailscale >/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  if ! tailscale status >/dev/null 2>&1; then
    sudo tailscale up --authkey "$TAILSCALE_AUTHKEY" --hostname webmc-stream
  fi
  sudo tailscale funnel --bg --https=443 "http://127.0.0.1:$PORT" || sudo tailscale funnel --bg "$PORT"
  tailscale funnel status || true
else
  echo "  no TAILSCALE_AUTHKEY given: the stream is on this machine only"
fi

echo "== opening the game and the host page in the browser, on the virtual display"
# the two tabs: the version the friends will play, and the page that captures and shares it
nohup "$BROWSER" \
  --user-data-dir=/tmp/webmc-chrome \
  --no-first-run --no-default-browser-check --disable-dev-shm-usage \
  --use-fake-ui-for-media-stream \
  --auto-select-desktop-capture-source="Entire screen" \
  --kiosk "$LAUNCHER" "http://127.0.0.1:$PORT/host" >/tmp/chrome.log 2>&1 &

echo
echo "  the lobby is http://127.0.0.1:$PORT/ here, and the funnel address above for everyone else"
echo "  if the capture did not start by itself, open the host page over VNC and press Share the screen"
