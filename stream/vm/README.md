# Putting the stream on a machine that is not yours

You do not need this to get a public address - `./stream/live.sh --funnel` already gives
`https://<machine>.<tailnet>.ts.net/`, which is a normal internet address, not localhost. This folder
is for the other wish: the game running on a cloud box instead of under your desk.

## What can host it, honestly

| host | can it run the game + capture? | what it costs | notes |
|---|---|---|---|
| **Oracle Cloud Always Free** | **yes** - 4 ARM cores, 24 GB RAM, 10 TB/month egress | **free** | needs a card for verification, "out of capacity" is common in busy regions, and Oracle may reclaim instances that sit idle. The only free tier with enough RAM for a Minecraft client. |
| Google Cloud | yes | **$300/90-day trial, then paid** | the Always Free `e2-micro` has 1 GB RAM and cannot run this; a working 4-vCPU box is roughly $0.20/hour, and egress beyond the monthly allowance is about $0.12/GB |
| Fly.io | yes | about $33/month CPU-only, $1.25/hour with a GPU | software rendering only at the cheap end |
| Vercel | **no** | - | serverless has no long-running process and no GPU, and its fair-use rules are aimed at exactly this traffic. It can host the lobby page and nothing else. |
| Cloudflare free, ngrok free | no | - | dev-traffic tiers; a game stream is the abuse pattern they look for |
| GitHub Actions | no | - | where the previous account was suspended: their runners as a game host |

Nothing in that table changes one fact: **somewhere, a machine you are entitled to use must run the
game.** Free and legitimate means your own computer (or a free tier that is meant for general compute,
like Oracle's). It never means borrowing a build farm.

And the licensing line is separate from hosting: friends, on a private link, with legitimate accounts,
is the defensible version. A public service where anyone joins is a Mojang question (their EULA: you
must not make the game available to others), whichever host is underneath.

## Deploying to a fresh Ubuntu VM

```sh
# 1. create an Ubuntu 22.04+ VM anywhere (Oracle Always Free: 4 ARM cores / 24 GB is plenty)
# 2. get a Tailscale auth key: login.tailscale.com/admin/settings/keys  (reusable, ephemeral)
# 3. from your own machine:
scp -r stream ubuntu@<vm-ip>:~/
ssh ubuntu@<vm-ip> 'TAILSCALE_AUTHKEY=tskey-auth-... bash ~/stream/vm/provision.sh'
```

`provision.sh` installs what a headless host needs - Xvfb, Chrome, Tailscale - starts the stream
server with the funnel, then runs Chrome on the virtual display with the launcher open and the capture
source pre-selected, so the box captures its own screen and publishes the lobby at
`https://<vm>.<tailnet>.ts.net/`.

Two honest notes about that: Chrome's capture flags are test flags, and if the auto-selection does not
take, you open the host page once over VNC and click the button; and the free tier's ARM cores render
Minecraft's software path slowly, so 1.12.2 in a tab will feel like a low-end laptop, not a gaming PC.
