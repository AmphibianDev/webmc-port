#!/usr/bin/env bash
# Prepare a fresh Ubuntu box to stream Minecraft: display, audio, encoder, fallback, Java, Tailscale.
#
#   sudo bash provision.sh
#
# The package list and the Selkies install are the archived workflow's, unchanged, because they are the
# part that took the longest to get right: mesa for software OpenGL, x11vnc+websockify for the
# codec-free fallback, and Selkies itself from its release .deb rather than from source.
set -euo pipefail
cd "$(dirname "$0")"

echo "== packages"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  xvfb x11-utils x11-xserver-utils xauth xdotool \
  libgl1-mesa-dri libglx-mesa0 libgl1 libegl1 libgbm1 mesa-utils \
  x11vnc websockify ffmpeg pulseaudio pulseaudio-utils curl jq unzip python3 \
  openjdk-17-jre-headless ca-certificates >/dev/null
if ! command -v java >/dev/null; then
  echo "  ! no java: install a runtime (1.12.2 wants Java 8, newer want 17+)"
fi

echo "== noVNC (the codec-free fallback)"
NOVNC_WEB=/usr/share/novnc
if [ ! -f "$NOVNC_WEB/vnc.html" ]; then
  sudo mkdir -p /opt/novnc
  if curl -fsSL --retry 3 --max-time 300 -o /tmp/novnc.tar.gz \
      https://github.com/novnc/noVNC/archive/refs/tags/v1.7.0.tar.gz; then
    sudo tar -xzf /tmp/novnc.tar.gz -C /opt/novnc --strip-components=1
  else
    sudo apt-get install -y novnc >/dev/null
    sudo cp -r /usr/share/novnc/. /opt/novnc/
  fi
fi

echo "== selkies"
if ! command -v selkies >/dev/null; then
  DISTRO="$(. /etc/os-release && echo "${ID}${VERSION_ID}")"
  ARCH="$(dpkg --print-architecture)"
  VERSION="$(curl -fsSL https://api.github.com/repos/selkies-project/selkies/releases/latest | jq -r .tag_name)"
  PKG="selkies-${VERSION}-${DISTRO}-${ARCH}.deb"
  echo "  $PKG"
  if curl -fsSL --retry 3 -o "/tmp/$PKG" \
      "https://github.com/selkies-project/selkies/releases/download/${VERSION}/${PKG}"; then
    sudo apt-get install -y "/tmp/$PKG" >/dev/null
  else
    echo "  ! no .deb for ${DISTRO}/${ARCH}; install selkies by hand, or use --frontend novnc"
  fi
fi

echo "== tailscale (for a public address)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh

echo
echo "  ready. then:"
echo "    ./session.sh --version 1.12.2 --publish"
echo "  the first run downloads the client into ~/webmc-client (about 25 MB)"
