# Streaming Minecraft from this machine

Two commands:

```sh
./stream/host.sh                 # local network
./stream/host.sh --tailscale     # also print a tailnet address for friends elsewhere (brew install --cask tailscale)
```

Then: open Minecraft, press **Share a window** on the page that opened, and pick the Minecraft window.
Send a friend the address it prints. They get a page; **Take control** gives them the keyboard and
mouse, **Esc** in the page hands it back. Video is 60 fps WebRTC straight from this browser to theirs;
audio too if you leave the box ticked.

## Why it is built this way

Streaming is sustained encoding plus sustained bandwidth, and every free host meters exactly that:
Vercel's fair-use guidelines exist because bandwidth is what they bill, Cloudflare routes video through
paid products, and GitHub Actions - where this was tried before, and where the account was suspended -
treats a streamed session as a game host on their compute. None of that is about repository
visibility: private repositories run on the same runners and are watched the same way.

A machine that already has the game has neither problem. It costs electricity, nobody's terms are
broken, and nothing has to be rented. What that machine *does* need is a way to get the picture out
without opening ports to the internet for strangers, which is what a tailnet (or the local network) is
for.

## What runs

* `server.py` - the standard library only. It serves the two pages, carries the WebRTC handshake as
  WebSocket messages, and injects the viewers' keystrokes with Quartz so the game responds. The video
  never passes through it: it is peer to peer, so one laptop can serve the handshake while the encoder
  does the heavy work. `--no-input` makes it watch-only, `--dry-run-input` records input and injects
  nothing (both are what the tests use, so they never move your real mouse).
* `host.html` - captures a window you choose, one peer connection per viewer, a bitrate slider
  (2-40 Mbit/s) that applies live, and a preview of exactly what viewers see.
* `watch.html` - full-screen priority, input capture while you have control, live stats (resolution,
  frames per second, megabits received), and a clear message when the host stops sharing.

## A real web address, without renting anything

```sh
brew install --cask tailscale     # one time, free plan, sign in from the menu bar
./stream/live.sh --funnel
```

That publishes this machine's stream server at `https://<this-mac>.<your-tailnet>.ts.net/` - a real
HTTPS address anyone can open, on every Tailscale plan, no port forwarding, nothing to pay. The page it
serves is the lobby; friends press join and play.

Three things worth knowing about that address:

* **Only the front door goes through it.** The video is peer to peer between the two browsers, so what
  passes through the funnel is the handshake and the keystrokes - kilobytes per second. Tailscale
  bandwidth-limits funnel traffic, and that limit is not what your frame rate depends on.
* **The host page stays local.** `/host` answers only to `127.0.0.1`; anyone else gets a 403. Otherwise
  the first stranger to open the public address could claim the host slot. They could not capture your
  screen - capture happens in the browser of whoever presses the button - but they could hold the slot
  and stop your friends from joining.
* **The game still runs here.** Funnel publishes a service on a machine you own; it does not run
  anything for you. That distinction is exactly what the earlier suspension was about: there, GitHub's
  runners were the game host. Here they are not, and no repository visibility setting is involved.

Cloudflare tunnels and ngrok can also publish a local port, but their free tiers are for development
traffic, and a game stream is not that - which is the same trap in a different shape. A rent-a-VPS is
honest but costs money: about $33/month for CPU-only software rendering, $1.25/hour for a real GPU.

## The honest limits

* **One viewer of a fast action game at a time.** WebRTC sends a separate stream per viewer, so two
  viewers means two encodes - fine on an M-series Mac at 1080p60, not free.
* **Input is whole-screen, not per-game.** While a viewer has control, their keystrokes go to whatever
  is focused on this machine. Don't leave it in control while you use the computer.
* **macOS permissions are the usual friction.** Screen Recording for the browser, Accessibility for
  Python. If input does nothing, that permission is missing: System Settings, Privacy & Security.
* **Public and permanent is still the wrong shape.** This is for you and a couple of friends. A
  genuinely public service - anyone joins, nothing is private - is a Mojang licensing question (their
  EULA says you must not make the game available to others), which no host choice changes.

## If you want it sharper later

Sunshine (host) plus a web Moonlight client is the high-quality version: hardware encoding, proper
input, gamepads. Sunshine publishes macOS arm64 builds, but the browser client
(`moonlight-web-stream`) publishes only Linux and Windows binaries - on macOS it has to be built from
source with Rust, which is the work that separates the two options.
