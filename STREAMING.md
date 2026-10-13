# Streaming: where it can run, and why not where it looks like it can

Streaming means running the game on a machine you rented, encoding the picture, and sending video to
the visitor's browser. That is a compute-and-bandwidth business, and no host gives either away.

This note exists so the question is settled once, with the terms in hand.

## The free hosts cannot do it, and it is not a grey area

* **Vercel** — serverless functions do not keep running: there is no process to hold a game session, no
  GPU, and Hobby is explicitly non-commercial personal use. Their own Fair Use Guidelines exist because
  bandwidth is the thing they meter, and their community support threads show Hobby teams being
  **paused** for as little as a looping background video. A 720p60 session is hundreds of times that.
  Video does not go through a Next.js function; the platform is the wrong shape.
* **Cloudflare** — the free CDN has never been for this. Section 2.8 of their self-serve terms limited
  what could be served through the CDN, and the updated terms are explicit: video and other large files
  must be served through a **paid** Cloudflare product (Stream, Images, R2). Workers cap CPU per
  request, which no game session fits inside.
* **GitHub** — this is the one that already happened. A workflow that starts a streamed session turns
  their runners into a game host: sustained CPU, sustained egress, hours-long jobs. That is what
  suspended the previous account, and it is the same violation whether the repository is public or
  private, because what is being watched is usage, not visibility.

The example worth remembering is the Vercel one: a *paused team* for a looping video. Nobody read a
rule and decided; the bandwidth meter did.

## What it actually costs, if it is paid for

| host | what one always-on session needs | price |
|---|---|---|
| Fly.io performance-1x (2 GB, CPU only) | software rendering: playable at low resolution only | ≈ $33/month |
| Fly.io shared-cpu-8x (2 GB) | slower still, for one viewer | ≈ $17.55/month |
| Fly.io GPU (L40s) | the only comfortably smooth option | **$1.25/hour** ≈ $900/month |
| your own machine | one session, real GPU | electricity |

Plus egress on every plan: 720p60 is roughly 1.5–3 GB per hour, per viewer. Ten friends for two hours
is tens of gigabytes, which is exactly the pattern every host's abuse system flags.

## The part that is not about hosts at all

Minecraft's EULA says you must not distribute what Mojang made - "give copies to someone else", "make
it available to others". Hosting a *server* for online play is allowed; running the game **for** people
who connect to your stream is closer to lending them the game, and their commercial usage guidelines
govern doing it for money. So even on infrastructure you own and pay for, a *public* streaming service
is a licensing problem with Mojang, not a hosting problem you can shop around.

Private, for yourself and a few friends, with legitimate accounts, on a machine you own: that is the
version of this that is defensible, and it is also the only one that is free.

## What that looks like here

The archived `webmc` repository already contains it: `host-here.sh` runs the game under Xvfb with
x11vnc and noVNC on your own machine and publishes a link, and its own comment says the same thing
this file does - "GitHub-hosted runners are for building, not game hosting; a machine you own has
neither limit". The honest upgrade for smoothness is to replace noVNC with WebRTC (20-50 ms locally
instead of a laggy VNC canvas), which is a weekend of work and needs no host at all.

## It exists in this repository now

`stream/` is that tool: `./stream/host.sh`, then press "Share a window" and pick Minecraft. Viewers
open the address, press "Take control", and play; Esc in the page hands control back. Video is WebRTC
straight between the two browsers, the local server carries only the handshake and the keystrokes, and
afraid of nothing it is standard-library Python. See `stream/README.md` for the limits, which are
honest ones: one action-game viewer at a time is comfortable, input goes to whatever window is focused,
and macOS will ask for Screen Recording and Accessibility the first time.

For friends outside your network there are two ways, both free:

* **Tailnet only** - they install Tailscale and join yours (free for three users). Most private.
* **Funnel** - `./stream/live.sh --funnel` publishes the lobby at a real HTTPS address,
  `https://<machine>.<tailnet>.ts.net/`, for anyone to open. Funnel is on every Tailscale plan and is
  built for publishing a local service; only the handshake and the keystrokes go through it, because
  the video is peer to peer. The host page stays local-only either way.

What this repository will not do, and `tests/test_pipeline.py` asserts it: no workflow may mention
xvfb, x11vnc, noVNC, ffmpeg, rtmp, cloudflared, ngrok or a server jar, and the only long job stays
manual. If streaming comes back, it comes back as a self-hosted tool a person runs on their own
machine - never as a CI job, never as a deployment target.
