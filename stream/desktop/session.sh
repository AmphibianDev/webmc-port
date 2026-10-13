#!/usr/bin/env bash
# Run a real Minecraft client on a Linux box and stream it to browsers. This is the archived stack
# (Xvfb + PulseAudio + Selkies, with x11vnc/noVNC as the codec-free fallback), revived for a machine
# you own instead of a CI runner - which is the only change that matters about it.
#
#   ./session.sh --version 1.12.2            # start the box, stream to port 8080
#   ./session.sh --publish                   # ...and put it on the internet with a Tailscale funnel
#
# Quality knobs, all from the original and all worth keeping: resolution, fps, bitrate, encoder list.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="1.12.2"
WIDTH="${WEBMC_WIDTH:-854}"
HEIGHT="${WEBMC_HEIGHT:-480}"
FPS="${WEBMC_STREAM_FPS:-30}"
BITRATE="${WEBMC_VIDEO_BITRATE:-4000}"
CRF="${WEBMC_VIDEO_CRF:-28}"
QUEUE="${WEBMC_BACKPRESSURE_QUEUE:-4}"
# A list, not a choice: the client steps through the encoders until it finds one it can decode, so a
# browser without WebCodecs H.264 lands on JPEG instead of sitting on "waiting for stream" forever.
ENCODER="${WEBMC_STREAM_ENCODER:-h264enc-striped,jpeg}"
JPEG_Q="${WEBMC_STREAM_JPEG_QUALITY:-35}"
WEB_PORT="${WEBMC_WEB_PORT:-8080}"
VNC_PORT="${WEBMC_VNC_PORT:-5900}"
DISPLAY_NUM="${WEBMC_DISPLAY:-:99}"
FRONTEND="${WEBMC_STREAM_CLIENT:-selkies}"
CLIENT_DIR="${WEBMC_CLIENT_DIR:-$HOME/webmc-client}"
PUBLISH=0

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --publish) PUBLISH=1; shift ;;
    --frontend) FRONTEND="$2"; shift 2 ;;
    --width) WIDTH="$2"; shift 2 ;;
    --height) HEIGHT="$2"; shift 2 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

export DISPLAY="$DISPLAY_NUM"
say() { printf '  %s\n' "$*"; }

say "virtual screen ${WIDTH}x${HEIGHT} on $DISPLAY"
pgrep -f "Xvfb $DISPLAY" >/dev/null || {
  setsid Xvfb "$DISPLAY" -screen 0 "${WIDTH}x${HEIGHT}x24" -ac +extension GLX +extension RANDR \
    -nolisten tcp >/tmp/xvfb.log 2>&1 &
  sleep 2
}
xdpyinfo -display "$DISPLAY" >/dev/null || { say "Xvfb did not start"; exit 1; }
say "screen ready"

say "audio"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/webmc-runtime}"
export PULSE_RUNTIME_PATH="$XDG_RUNTIME_DIR/pulse"
mkdir -p "$XDG_RUNTIME_DIR" "$PULSE_RUNTIME_PATH"; chmod 700 "$XDG_RUNTIME_DIR"
pulseaudio --kill >/dev/null 2>&1 || true
( unset PULSE_SERVER; pulseaudio --start --daemonize=yes --exit-if-idle=no --exit-idle-time=-1 >/dev/null 2>&1 ) || true
export PULSE_SERVER="unix:$PULSE_RUNTIME_PATH/native"
sleep 2
pactl load-module module-null-sink sink_name=webmc >/dev/null 2>&1 || true
pactl set-default-sink webmc >/dev/null 2>&1 || true
say "audio ready (silent only if pulseaudio refused to start)"

say "client"
if [ ! -f "$CLIENT_DIR/classpath.txt" ]; then
  mkdir -p "$CLIENT_DIR"
  python3 fetch-client.py --version "$VERSION" --dir "$CLIENT_DIR"
fi
MAIN="$(head -1 "$CLIENT_DIR/run.txt")"
ASSET_INDEX="$(tail -1 "$CLIENT_DIR/run.txt")"

start_game() {
  # the arguments each era wants, from the same reasoning as the launch page: 1.6+ takes the long form
  say "starting $MAIN"
  java -Xmx2G -Djava.library.path="$CLIENT_DIR/natives" \
    -cp "$(cat "$CLIENT_DIR/classpath.txt")" "$MAIN" \
    --username WebMC --version "$VERSION" --gameDir "$CLIENT_DIR" --assetsDir "$CLIENT_DIR/assets" \
    --assetIndex "$ASSET_INDEX" --uuid 0 --accessToken 0 --userType legacy --demo \
    >/tmp/game.log 2>&1 &
  echo $! > /tmp/game.pid
}

start_selkies() {
  say "selkies on :$WEB_PORT"
  setsid selkies --public --port="$WEB_PORT" --mode=websockets --enable-https=false \
    --enable-basic-auth=false \
    --encoder="$ENCODER" --jpeg-quality="$JPEG_Q" --raw-pointer-motion=true \
    --framerate="$FPS" --video-bitrate="$BITRATE" --video-crf="$CRF" \
    --backpressure-queue-size="$QUEUE" \
    --manual-resolution=true --manual-width="$WIDTH" --manual-height="$HEIGHT" \
    --use-css-scaling=true --video-on-start=true --audio-on-start=true \
    --enable-cursors=false --use-browser-cursors=false \
    >/tmp/selkies.log 2>&1 &
  echo $! > /tmp/selkies.pid
  for _ in $(seq 1 60); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$WEB_PORT/" || true)"
    case "$code" in 200|401|403) say "selkies is answering"; return 0 ;; esac
    kill -0 "$(cat /tmp/selkies.pid)" 2>/dev/null || break
    sleep 1
  done
  return 1
}

start_novnc() {
  # no codec to negotiate: x11vnc captures the display Minecraft is already using, websockify carries it
  say "falling back to x11vnc + noVNC"
  NOVNC_WEB="${WEBMC_NOVNC_WEB:-/usr/share/novnc}"
  [ -f "$NOVNC_WEB/vnc.html" ] || NOVNC_WEB=/opt/novnc
  x11vnc -display "$DISPLAY" -localhost -rfbport "$VNC_PORT" -forever -shared -nopw \
    -wait 10 -defer 10 >/tmp/x11vnc.log 2>&1 &
  echo $! > /tmp/x11vnc.pid
  sleep 2
  websockify --web "$NOVNC_WEB" "$WEB_PORT" "127.0.0.1:$VNC_PORT" >/tmp/websockify.log 2>&1 &
  echo $! > /tmp/websockify.pid
}

case "$FRONTEND" in
  novnc) start_novnc ;;
  *) start_selkies || { say "selkies failed, using noVNC instead"; start_novnc; } ;;
esac

start_game

if [ "$PUBLISH" = "1" ]; then
  ./publish.sh "$WEB_PORT" || true
fi

say
say "session is up: http://127.0.0.1:$WEB_PORT/ locally"
say "tail -f /tmp/game.log to watch the client, /tmp/selkies.log for the picture"
say "ctrl-c stops the stream (game included)"
trap 'kill "$(cat /tmp/game.pid 2>/dev/null)" 2>/dev/null || true; exit 0' INT TERM
wait
