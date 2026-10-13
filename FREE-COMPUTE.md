# Free 16 GB-class compute: who gives it, and what they allow

The GitHub Actions runner is 4 vCPU / 16 GB / 14 GB SSD, and on a **public** repository the minutes are
unlimited. That is what made it attractive for this project, and it is why the account was suspended
when it was used to host game sessions. This file is the honest list of what else exists, so nobody has
to guess.

## The free things with real RAM

| service | what you get free | allowed to run a game session? | notes |
|---|---|---|---|
| **Oracle Cloud Always Free** | **4 ARM cores, 24 GB RAM, 200 GB storage, 10 TB egress/month, permanently** | **yes** - it is a general-purpose VM you own | needs a card for verification (not charged), "out of capacity" is common, idle instances may be reclaimed |
| GitHub Actions (public repo) | 4 vCPU / 16 GB / 14 GB, unlimited minutes | **no** | terms: production, testing, deployment, publication of the software project - a runner as a game host is the unrelated activity that got the previous account suspended |
| Azure Pipelines (public project) | 10 parallel jobs, unlimited minutes, **2 vCPU / 7 GB / 14 GB** | **no** | same clause, same shape as GitHub's |
| GitLab SaaS free | 400 compute minutes/month, 2 vCPU / 3.75 GB | **no** | same clause |
| CircleCI free | a few thousand minutes at 2 vCPU / 4 GB | **no** | same clause |
| Travis CI (open source) | 2 vCPU / 7.5 GB | **no** | same clause |
| CircleCI free | 2 vCPU / 4 GB, a few thousand minutes | **no** | same clause, and 4 GB cannot run a client anyway |
| GitLab CI/CD free | 400 compute minutes/month, 2 vCPU / 3.75 GB | **no** | same clause, and 400 minutes is seven hours of CI a month |
| Cirun | nothing of its own | **no** | it provisions cloud VMs **in your own account** for CI jobs: you bring the cloud credentials, so it is a broker, not free compute |
| GitHub Codespaces free | 120 core-hours/month (2-core / 8 GB) | **no** | explicitly for development; 16 GB needs a paid machine type |
| Google Cloud free trial | $300 for 90 days, then paid | yes, but it ends | the Always Free e2-micro has **1 GB RAM** and cannot run a client; a working 4-vCPU box is about $0.20/hour and egress about $0.12/GB |
| Colab / Kaggle / Hugging Face Spaces | big RAM, sometimes GPUs | **no** | notebook and model-demo platforms; CI-style hosting is against their terms |
| Vercel / Netlify / Cloudflare free | static and serverless only | **no** | no long-running process, no GPU, and fair-use rules aimed at exactly this traffic |
| Render / Railway / Fly.io free tiers | 512 MB - 1 GB, sleeping | **no** | too small to run a client at all |

## The two answers, depending on what you want

* **Free, allowed, and enough to run or stream the game:** your own machine, or Oracle Cloud's Always
  Free tier (the only free 16 GB-class box that is legitimately general-purpose). Publish it with a
  Tailscale funnel - `stream/desktop/publish.sh` - and it has a real address.
* **Free, allowed, and enough to *build*:** any of the CI providers above, because compiling this
  project is exactly the activity their terms describe. That is what this repository's own workflows do
  (catalogue refresh, the 45-minute client compile on manual dispatch, Pages deploy), and it is why the
  suspension was about a *game session* and not about the builds.

## No credit card at all

Oracle and every other cloud require a card for identity verification, and the honest answer is that
there is no way around that - a virtual or prepaid card that is not yours is fraud, not a workaround,
and it takes the account with it when it is noticed. So the card-free options are:

| option | card? | can it run the stream? |
|---|---|---|
| **the machine you already have** | no | **yes** - `stream/live.sh` on this Mac, `stream/desktop/session.sh` on any Linux box |
| **a friend's PC** | no | **yes** - open the page on GitHub, press "Host this machine", run `python3 stream/server.py` next to it. Input works on macOS, Windows and Linux (Quartz, SendInput, xdotool). |
| **an old laptop or desktop** | no | **yes**, if it can run the client - same as above |
| **Azure for Students** (GitHub Student Pack) | **no card required** | yes - $100 of credit runs a 4 vCPU box for weeks; student verification instead of payment |
| **GitHub Student Pack credits** (DigitalOcean etc.) | DigitalOcean takes **PayPal** | yes, if the credit is spent on a suitable VM |
| **Codespaces, Colab, Spaces, Replit** | no | no - their terms forbid hosting, and they are too small |
| **Oracle Cloud Always Free** | **card required** | yes, but only with a real card |

The public address is free of charge and free of cards either way: a Tailscale funnel needs no payment
method at all (free plan, three users), and it is what publishes a session from a machine you own.

## They are all the same shape

Every CI provider - GitHub Actions, Azure Pipelines, CircleCI, GitLab, Travis, Cirun - exists to build,
test and publish software, and every one of their terms says so. Running a game session on any of them
is the same violation at a different vendor, and the thing that detects it is the same: hours-long jobs,
a saturated core, gigabytes of egress. None of them is a loophole; they are the same door with a
different name on it.

Two of them are also simply too small: CircleCI's free executor is 2 vCPU / 4 GB, and GitLab's free
tier is 400 minutes a month. Cirun is not even compute - it starts machines in *your* cloud account, so
it needs the account (and the card) first.

## The rule, in one line

A build farm is free because builds are cheap and predictable. A game stream is neither: it holds a
machine for hours, saturates a core, and pushes gigabytes out of someone else's network. Any provider
that gives away 16 GB is watching for that pattern, whether or not their terms spell it out - and the
Vercel team that got paused for a *looping background video* never broke a written rule either.

So "adapt it to another CI service" moves the same violation to a different vendor. Adapting it to
Oracle's free VM, or to the machine under your desk, moves it to hardware you are entitled to use, and
that is the only version that keeps working.
