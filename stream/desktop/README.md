# The archived stack, revived: a real desktop session streamed from a box you own

This is the streaming version from the archive, moved out of GitHub Actions and onto a machine you
control. It streams a **real desktop running a real Minecraft client** - Xvfb virtual screen,
PulseAudio, and Selkies (GStreamer + WebRTC) as the frontend, with x11vnc + noVNC as the codec-free
fallback on the same port.

## Why this one is the better streamer

The browser-capture streamer in `../` needs you to click "share a window", only works while that tab
is open, and gives one viewer per connection. This stack is what streaming actually looks like:

| | browser capture (`../`) | this (Selkies) |
|---|---|---|
| what is captured | a tab or screen you pick by hand | a virtual display that always exists |
| who runs the game | a browser (window, focused, on screen) | a real client process, windowless |
| video | WebRTC from the browser's encoder | GStreamer WebRTC, H.264 with a JPEG fallback |
| audio | the tab's audio | PulseAudio, properly |
| input | injected with Quartz (macOS only) | Selkies' own pointer/keyboard path |
| works on | macOS, Windows, Linux | **Linux only** |

That last row is the catch, and it is not a preference: macOS has no Xvfb and no x11vnc, so this stack
cannot run on your Mac. It is built for exactly what the archive used it for - a Linux box.

## Running it

```sh
# 1. a fresh Ubuntu 22.04+ machine. Oracle Cloud's Always Free tier is the only free one with enough
#    RAM (4 ARM cores, 24 GB) - see ../../STREAMING.md for the full table and the caveats.
# 2. on that machine:
sudo bash provision.sh                       # packages, noVNC, selkies, tailscale
./session.sh --version 1.12.2 --publish      # starts the session and publishes it
```

The first session downloads the client (jar, libraries and Linux natives, about 25 MB) into
`~/webmc-client`. The published address is `https://<vm>.<your-tailnet>.ts.net/`, from a Tailscale
funnel - the same free, built-for-this mechanism the browser streamer uses.

Quality knobs, all inherited from the archive and all load-bearing:

```sh
WEBMC_WIDTH=1280 WEBMC_HEIGHT=720 WEBMC_STREAM_FPS=60 WEBMC_VIDEO_BITRATE=8000 ./session.sh
WEBMC_STREAM_CLIENT=novnc ./session.sh            # force the fallback
WEBMC_STREAM_ENCODER=h264enc-striped,jpeg ./session.sh
```

## What the archive had learned, and what it still applies here

* **The encoder is a list, not a choice.** A browser without WebCodecs H.264 can decode JPEG but not
  H.264; offering both, in order, is what stopped sessions sitting on "waiting for stream".
* **Keep the resolution small and scale in CSS.** 854x480 encoded, stretched by the client, is smooth
  on a four-core box; letting the client resize the desktop to a 2K window makes the game render four
  times the pixels and turns it into a slideshow.
* **No window manager.** A tiling WM draws a bar over the game and shifts every click.
* **No streamed cursors.** Two cursors - the browser's and X's - is what made the camera spin; the game
  draws its own crosshair and grabs the pointer.
* **Raw pointer motion on.** Minecraft wants unaccelerated movement under pointer lock.

## What did not come across

The GitHub Actions workflow. That is what the suspension was about - a CI runner hosting a game session
- and it would be just as much of a violation today, private repository or not, because what is watched
is resource use rather than visibility. Nothing in this repository starts a session on CI: a test fails
if a workflow ever mentions xvfb, x11vnc, noVNC, ffmpeg, cloudflared, ngrok or a server jar.

And the licensing line, which is separate from hosting: friends on a private link, with legitimate
accounts, is defensible. A public service where anyone can join is a Mojang EULA question - their terms
say you must not make the game available to others - and no choice of host changes that.
