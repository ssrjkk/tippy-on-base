# Cloudflare Pages + VPS

## What runs where

| Piece | Where | What it serves |
|---|---|---|
| Cloudflare Pages | `web/static` (build output dir) | `style.css`, `app.js`, `site.js`, images |
| Pages Functions | `functions/[[path]].js` | proxies every other request to the VPS |
| FastAPI backend | VPS, Docker (`web/server.py`) | `/`, `/app`, `/u/{id}`, `/m/{id}`, `/m/oc/{id}`, `/agent`, `/tos`, `/robots.txt`, `/sitemap.xml`, `/metrics`, `/qr`, `/api/*`, `/telegram-webhook`, `/.well-known/farcaster.json`; `/login` and `/me` redirect to `/#workspace-section` |

The function catches all requests first (a Pages Function takes precedence over
the asset bundle) and asks the asset bundle only for `GET`/`HEAD` paths that end
in a known static extension. Everything else — including the HTML pages — goes
to the origin. Two reasons documents must not be served as bare assets:

- `GET /` injects the Content-Security-Policy nonce into the inline script of
  `index.html` and issues the one-time login-state cookie that
  `/api/auth/telegram` requires. The raw file has neither.
- `GET /app` substitutes the public URL into the mini app meta tags.
- `/robots.txt` and `/sitemap.xml` are written by the origin from the rows the
  ledger has. `txt` is therefore not a static extension: the bundle has no
  `.txt` in it, Pages answers a missing path with `index.html`, and a crawler
  was handed `200 text/html` — a whole website's markup — where it expected
  rules. While the origin is down both are a 503 with a `Retry-After`, which is
  the honest answer to the same question.

Note that the bundle root **is** `web/static`, so an asset URL has no `/static/`
in it: `/miniapp-hero.png`, not `/static/miniapp-hero.png`. Every absolute image
URL the site publishes — `og:image`, `twitter:image`, `fc:miniapp.imageUrl`,
`fc:frame:image` — is checked against the bundle by
`tests/test_web.py::test_no_share_image_points_at_a_path_the_bundle_does_not_have`.

For the same reason there is no `_redirects` file in the bundle: a
`/* /index.html 200` rewrite is applied before Functions and would swallow
`/api/*`, `/app`, `/telegram-webhook` and every other dynamic path.

There *is* a `_headers` file, and it carries two kinds of promise. Under `/*` it
sends the two keys the origin already sends on every response —
`X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` — because it
applies to what the bundle answers itself (`style.css`, the scripts, the images,
the faces) and proxied documents keep the origin's own headers, including the CSP
nonce. Without it the same site sends protection on its pages and nothing on the
files those pages load.

The rest of the file is the cache. `web/static` is the whole site's source of
bytes, so a visitor who refetches it on every page view is downloading the design
instead of reading it: measured before this existed, one view of the landing
re-fetched 61 KB of CSS, seven scripts and eleven faces, and Pages answered every
one of them with its own default (`max-age=0, must-revalidate`).

| Files | Window | Why |
|---|---|---|
| `*.css`, `*.js` | `public, max-age=300, stale-while-revalidate=86400` | five minutes of freshness, then what is already on hand paints while the current file arrives in the background — so a deploy lands on the next visit rather than after a hard refresh |
| `fonts/*`, `*.png` | `public, max-age=86400, stale-while-revalidate=604800` | a face or a picture changes with a design, not with a page view |

A `Cache-Control` on `/*` would be a `Cache-Control` on a document, and a document
is the session, the nonce and the balance — so the `/*` block sends protection and
no window, and the origin's middleware keeps answering every page it writes with
`no-store`. The one non-asset the origin publishes as cacheable is `/qr`, whose
whole input is its own query string: a cache key cannot go stale relative to bytes
that are a function of the URL asking for them, and the page that chose that URL
is `no-store`.

The two numbers above are the same two the origin stamps in `ASSET_CACHE`
(`web/server.py`) for a visitor who reaches the VPS directly, and
`tests/test_design_system.py::test_one_file_one_caching_policy_in_both_answers`
reads both tables against the extensions the bundle directory actually carries —
a new file type joins the site only when someone decides how long it may be cached
in both places. `tests/test_production_health.py::test_the_published_bundle_is_this_checkouts_bundle`
then asks the live edge the same question about every shipped file: is this the
byte the tree has, and is it cached for the length `_headers` promises.

One consequence is worth knowing before you replace a picture in place: a
`miniapp-hero.png` committed under the same name can be up to a day old in a
returning visitor's browser. A renamed file is current the second this project is
published; an overwritten one waits for the window.

The proxy strips `Host` (the origin has its own hostname) and sets
`X-Forwarded-Host` plus `X-Forwarded-Proto: https` — TLS terminates at
Cloudflare, and without that header the backend would mark the session cookies
insecure and drop the `Secure` flag.

## 1. Backend on the VPS

```bash
git clone https://github.com/ssrjkk/tippy-on-base.git
cd tippy-on-base
cp .env.example .env      # then edit: chmod 600 .env, never commit it
docker compose up -d
```

The origin must be reachable over HTTPS. Put nginx/caddy in front of
`WEB_PORT` with a real certificate, or expose the container through a Cloudflare
Tunnel — either way the URL you give Pages is `https://…`.

## 2. Pages project

```bash
npm install -g wrangler
wrangler login
wrangler pages project create tippy --production-branch main
wrangler pages deploy web/static --project-name tippy
```

`wrangler.toml` already sets `pages_build_output_dir = "web/static"`, so
`wrangler pages deploy` with no argument works from the repo root. The
`functions/` directory sits next to the build output directory, which is where
Pages looks for it.

Alternative to the CLI: connect the repository in the Cloudflare dashboard and
set the build output dir to `web/static` with an empty build command. Every
branch then gets a preview URL.

This project is not connected to a repository, so **nothing publishes itself**:
the production domain is `https://tippy-egi.pages.dev` and it holds whatever the
last `wrangler pages deploy` uploaded. The two halves travel together — the
bundle and `functions/[[path]].js` — and both can be older than `main`. Measured
the day the freshness law was written, the published bundle was two weeks behind
this checkout: `style.css`, `app.js`, `chain.js` and `site.js` were the previous
revisions, and `fonts.css` plus all eleven faces were not in the bundle at all,
which is not a 404 — Pages answers a missing path with the landing page, so every
`@font-face` src resolved to a whole website and the browser silently fell back to
the operating system's font. That answer is now refused where it is produced: the
Function treats an asset path whose bundle response is `text/html` as a miss and
lets it reach the origin, which is the only thing that knows it is a 404. Measured
on the live host: `/wave33-no-such-asset.css` answered `200 text/html` before the
fix and the origin's own answer after it — while the origin cannot be reached, that
answer is the outage card. The cause the law below measures is unchanged: a file
that was never published is still absent, and it is absent silently. `--commit-dirty=true`
is what lets a working tree publish, and it is also how a deploy stops meaning
anything about a commit.

One page reaches the visitor from three vehicles at once, and each ages separately. The markup is
rendered from disk by the origin process at request time; the `*.js` and `*.css` come from this
hand-published bundle; and the bounds a field binds come from whatever `deploy/run.py` was started
whenever the host last restarted. Measured 2026-11-18 with all three in view: the published
`chain.js` was 3,945 bytes against this checkout's 4,748 and did not define `barWidth`, while four
call sites in freshly served markup reached for it — the Mini App's market list, its market detail,
`/m/{id}` and `/oc/{id}` each threw, and `/m/{id}` answered its readers with «Пул #N не найден» for
four pools that `/api/market/{id}` returned in full. Five of the 21 bundle assets were behind `main`
that day. The origin answering `/api/info` published eight of the eleven bounds this checkout
publishes — `default_deadline_hours`, `max_deadline_hours` and `deposit_confirmations` were missing —
because the process was older than `web/server.py`. So after any wave that touches markup,
`web/static` or a published bound: republish the bundle, and restart the origin.
`test_the_running_origin_publishes_every_key_this_checkout_publishes` holds the live answer to the
key set read out of `api_info()`'s own dict literal, in both directions, and the bundle hashes every
file the edge can answer against this checkout; both are in the run below, and a red result there is
evidence a deploy is owed, not evidence the tree is wrong.

```bash
python -m pytest tests/test_production_health.py -m production
```

## 3. Backend URL

Set `BACKEND_URL` to the origin, without a trailing slash:

```bash
echo -n "https://tippy.yourdomain.com" | wrangler pages secret put BACKEND_URL --project-name tippy
```

or in the dashboard under **Workers & Pages → tippy → Settings → Variables**.
Requests that reach the function without it get a 503 maintenance card with
`Retry-After: 300` instead of a silent failure — the same card replaces a gateway
5xx from a dead origin, so a visitor never sees an infrastructure error page.

The card has to arrive, too. Before the deadline existed, a request into a tunnel
that was mid-reconnect waited for Cloudflare's own subrequest limit — measured once
at more than twenty seconds of spinner — where the page promised an answer.
`functions/[[path]].js` now arms `UPSTREAM_DEADLINE_MS` on the upstream request, so a
visitor gives up on the origin after **15 s** and reads the card instead; a window
spent hearing nothing is not retried, because a second silence is a longer wait, not
better evidence, and the timer is cleared the moment the origin starts answering, so
a slow document is delivered rather than truncated. The number is deliberately not
tighter: the same origin has answered a plain health read in 7.8 s while the machine
hosting it ran the test suite, and a deadline that cuts a live origin off invents the
outage it is supposed to report.

### Preview of the bundle, for a project that asks for one

The same function can render the design with no application behind it, and that is a
thing a project has to want. Four questions decide it, all of them on the edge's own
side: the deployment carries `DEMO_HTML=1` (a Pages variable, set on a preview
deployment and absent from production and from every `.env` in this repository), the
request is a `GET` or `HEAD`, it accepts `text/html`, and the path is a page — not
`/api/…`, not `/_…`, and not a name carrying a format suffix. A fifth answer comes from
the bundle itself: the function asks Pages for a filename no file can hold, and a page
returned byte-identical to that answer is the project's single-page capture standing in
for a document that does not exist, so the card is served instead.

```bash
# A page-shaped read on a deployment with the preview on answers the bundle:
curl -sSI -H 'accept: text/html' https://tippy.yourdomain.com/ | grep -i 'x-tippy-demo-mode'
# and an API read never does, whatever the client asks for:
curl -sSI -H 'accept: text/html' https://tippy.yourdomain.com/api/health | grep -i 'x-tippy-outage-reason'
```

The ungated version of that fallback was measured live on 2026-11-20, on a project
whose `BACKEND_URL` was empty: `GET /api/health` answered `200 text/html` carrying
`x-tippy-demo-mode: true` and the landing's 41,661 bytes, `/robots.txt` and
`/sitemap.xml` answered markup where a crawler reads rules, an unknown URL answered a
soft `200` where `not_found.html` owes a `noindex` dead end, and `/app` answered the
Mini App shell with `__MIN_WITHDRAW_USDC__` and six more placeholders unsubstituted.
Every one of those is the answer a preview owes only a browser looking at a page, and
the card naming `no-backend` — the row in [Why the card came](#why-the-card-came-and-what-each-reason-says)
that tells an operator the variable is missing — appeared for nothing but a POST. That
is also why the preview is the one place where the bundle may speak for the origin: the
`*.html` refusal in `BundleFiles`, the `txt` exclusion at the head of the function and
the dead-end page all describe a site that answers a page with a page, and a document
that stands in for an application has to say so in its headers.

## 4. Proxy trust on the backend

Per-IP rate limits read the client address from the connection by default. Once
requests arrive through Cloudflare, every visitor shares the same peer address,
so the backend has to be told to honour `X-Forwarded-For` — and only from
Cloudflare:

```bash
TRUST_PROXY_XFF=1
TRUSTED_PROXY_PEERS=<the CIDR blocks published at https://www.cloudflare.com/ips/>
```

Without `TRUSTED_PROXY_PEERS` covering your real proxy peers, leave
`TRUST_PROXY_XFF=0`: an attacker who can connect directly to the origin would
otherwise spoof any client IP and defeat the limits and the webhook peer checks.

## 5. Telegram webhook

The webhook path is `/telegram-webhook` (`WEBHOOK_PATH`). Point Telegram at the
Pages domain so the same proxy serves it:

```bash
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://tippy.example.com/telegram-webhook",
       "secret_token": "'"${WEBHOOK_SECRET}"'"}'
```

`WEBHOOK_SECRET` is required whenever `WEBHOOK_URL` is set (`bot/config.py`
refuses to start otherwise). Telegram sends it as
`X-Telegram-Bot-Api-Secret-Token` and the handler rejects anything that does not
match, so proxying the webhook adds no new trust requirement.

Set `WEBHOOK_URL` and `MINI_APP_URL` to the public Pages origin in `.env`;
`MINI_APP_URL` feeds the WebApp buttons and the meta tags on `/app`.

## 6. Verify after a deploy

```bash
curl -sI https://tippy.example.com/style.css | head -3      # served by the edge
curl -sI https://tippy.example.com/                          # text/html + Set-Cookie: tippy_login_state
curl -s  https://tippy.example.com/api/info | head -c 200    # JSON from the origin
curl -sI https://tippy.example.com/telegram-webhook          # 405 from FastAPI, not 404 from Pages
```

Then open `/` in a browser and sign in with the Telegram widget: the page must
stay on `/` and show the workspace with your balance.

The three probes above answer three paths. The bundle is the whole directory, the
publish is by hand, and a page can look perfect while the file behind it is two
weeks old, so the check that closes a deploy is the one that reads every shipped
asset off the live edge and compares the bytes, the type and the cache window with
this tree:

```bash
python -m pytest tests/test_production_health.py -m production
```

## The origin on a Windows host

The VPS above is the shape this project ships to. The origin the live edge points
at today runs on a Windows machine: `deploy/run.py` serving `127.0.0.1:8000`,
reached through a named tunnel by `deploy/cloudflared.exe`, supervised by
`deploy/watchdog.ps1 -Once` from Task Scheduler (the `\TippyOriginWatchdog` task,
every few minutes).

**Two replicas, each with its own metrics port.** With a single replica the public
health endpoint answered 200 for 6 of 19 probes (32%) while that same process's
`/ready` answered 200 for 10 of the 19 (53%), and the origin behind it answered
every loopback request in 0.24 s. The page was down because one connector had no
edge connections, not because anything behind it was. The pair was re-walked this
wave over 87 samples, both replicas read on the same pass at
`http://127.0.0.1:20241/ready` and `http://127.0.0.1:20242/ready`: `20241` answered
ready for 73 of 87 (84%), `20242` for 45 of 87 (52%), and the two together for 73
of 87 (84%). Read beside the single-replica walk above, the pair is the better
tunnel; read beside itself, the second replica bought this host nothing, because
both connectors die on one shared egress — the host lost every edge connection for
one contiguous 68-second window, and the origin answered loopback the whole time.
So the availability here comes from the supervisor replacing the replica that went
quiet, not from the count; the count is what lets it do that without taking the
page down while it does (the older walk held three samples in which one replica had
no connection at all and the page still served). Each replica writes its own logfile
(`data/runtime/cloudflared-20241.log`, `data/runtime/cloudflared-20242.log`),
because cloudflared takes one `--logfile` per process.

**Two separate things were killing the connections.** Over the whole connector log
this host registered 35,943 edge connections with a median lifetime of 20 seconds,
82% of them dead inside a minute, and the deaths were dominated by QUIC errors —
the VPN/TUN interface answers cloudflared's QUIC precheck with PASS and then drops
the long-lived UDP streams. `--protocol http2` removed that class outright: zero
QUIC errors on this host since the pin, in all three launchers
(`deploy/tunnel.py`, `deploy/launch.py`, `deploy/watchdog.ps1`).

What the pin did not remove was the other one, and for a while the supervisor was
the one holding the knife. `--edge-bind-address` named the Wi-Fi address that
already carried 82 of 82 established `:443` sockets, so it selected nothing, while
a connector that carried it lost its edge connections every 22-173 s (median
101 s, 49 deaths in 30 minutes) — and the same binary without it registered four
connections and terminated none in the ten minutes the comparison ran. The
watchdog then treated a missing flag as evidence of a wrong route and stopped the
process, which restarted the churn it was meant to heal. No launcher on this host
passes the flag, and the container in `docker-compose.yml` runs
`command: tunnel run` on a VM with no TUN interface, where QUIC is not the failure
and neither setting is needed.

**A Telegram failure used to end the web server with it.** `deploy/run.py` polls
Telegram and serves FastAPI in one process, so whatever stops polling can stop the
site — and until this wave the retry rule named `TelegramNetworkError` alone. Aiogram
raises that class for a client-side network fault and builds `TelegramServerError`
from any 5xx Telegram answers, as a sibling rather than a child, so «Telegram server
says - Bad Gateway» at `get_me` re-raised, ended the polling task, closed the ledger
and took `/` down with it until the supervisor started the process again. Over the
270 h ending 2026-11-17 the supervisor's own log carries `origin=False` on 1,830 of
5,563 verdict passes and 707 origin starts. One tuple now answers «is this Telegram's
to fix?» — `TRANSIENT_TELEGRAM_ERRORS` in `bot/main.py`, read by both entrypoints and
both polling loops — and `tests/test_bot_main_lifecycle.py` derives the family from
aiogram's own class hierarchy instead of a list kept here: every server-side class
must be covered, no permanent answer may be, and a retry site that hand-names a single
class fails the build. A revoked token still ends the process, because spinning on it
keeps the site answering HTTP for a bot that answers nobody. Measured the night the fix
shipped: `api.telegram.org` became unreachable from this host, and
`data/runtime/origin.err.log` filled with `telegram failed, retrying in 15s` warnings
while the origin kept answering its health endpoint and every pass line read
`origin=True`.

**The windows the supervisor judges by.** A probe is one instant, and this host's
connection sets turn over every minute or two while the page serves, so a verdict
is a window, never a sample:

| Rule | Value | Why |
|---|---|---|
| Startup grace, connector | 90 s | a replica four seconds old has no connections by definition |
| Confirm probes before replacing | 3, 15 s apart | one `/ready` 503 was the old trigger and the page was up |
| Wait for the pair before judging the public path | 45 s | the pass used to probe the edge microseconds after starting a connector that could not yet answer: 4,745 of 5,526 logged passes said `public=False` (86%) |
| Public probes | 3, 5 s apart | one request through one edge POP says nothing about the site |
| Startup grace, origin | 600 s | `run.py` runs the alembic chain before it binds a socket |
| Origin verdict | 3 probes, all `silent` | see below |

Three outcomes for a probe, and only the third condemns an origin: `ok`, `answered` (a `/ready`
503, or a 200 whose `db` is not `ok` — a fault the process is reporting, which a
restart would hide while it killed Telegram polling with it) and `silent` (nothing
answered at the HTTP layer at all). A connector is replaced when its whole window
failed to say `ok`; the origin is replaced only when its whole window was `silent`,
which is the wedged-process case this host has actually produced.

**What the public leg asks, and what it is allowed to answer.** `$PublicOrigin` is the
site, `$PublicHealth` is `$PublicOrigin` plus `/api/health`, and `$PublicControl` is
`$PublicOrigin` plus `/fonts.css`: the `ASSET` branch of `functions/[[path]].js`
answers a stylesheet out of the bundle before the function names the origin, so the
tunnel, the app and the database decide nothing about that request, and a 200 proves
this machine finished a handshake with Pages. It is probed because the answer it gives
is not the answer the measurement gives: `$PublicHealth` is tried `$PublicProbes`
times, `$PublicProbeDelaySeconds` apart, and the verdict is one of `$PublicOutcomes`.
`ok` is the page being up. `down` is a measured failure — an HTTP answer that is not
the health the site owes, or nothing at all while the witness answered anything
(including a 404, which is still proof of a route). `unreachable` is this host having
no route to look, and it deliberately does not fail the pass: measured while the
origin answered every loopback request, TCP to both `tippy-egi.pages.dev` addresses
opened in 9-33 ms and every HTTPS request to that hostname then died inside the
handshake, so a leg that cannot finish a handshake holds no evidence about a
visitor's page — while a scheduled task that exits 1 every few minutes for hours says
nothing on the occasion it exists to speak. It is still printed in the pass line and
still named by `-Check`, and the vantage that does see this site from outside is the
`production` suite, which `.github/workflows/prod-health-check.yml` runs on GitHub's
runners rather than from this machine.

Read the state without touching anything:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/watchdog.ps1 -Check
```

It answers `OriginReady`, `OriginOutcome`, `ConnectorReady`, `PublicOutcome`,
`PublicReady`, `Connectors`, `Origins`, `ReplicaPorts` and `ReadyPorts` — the last two
together are what distinguishes "the tunnel is down" from "one of two replicas is
down", which the single boolean it replaced could not say, and the two outcome fields
are what says whether the supervisor will reboot anything at all.

`tests/test_watchdog.py` runs these rules rather than reading them: the restart
verdicts are executed as the PowerShell that produces them against a truth table,
and a mutation of the grace period or the probe window in a copy of the script is
required to move the verdict — so the numbers in this table are the numbers the
supervisor uses.

### Why the card came, and what each reason says

The outage card is the one page this project writes without the origin, so it is
the one place an outage can be named. It carries the word twice — the
`x-tippy-outage-reason` response header and the `data-reason` attribute on its own
hint line — so an outage is read with `curl -I https://tippy-egi.pages.dev/api/health`
instead of opened in a browser and guessed at. `functions/[[path]].js` chooses the
word in one function, `outageReason()`, from which branch was taken; it never reads
an exception, because a page anyone can request must not quote what broke. Four
words, one per branch:

| Reason | What the edge measured | Remedy |
|---|---|---|
| `no-backend` | Nothing was asked at all: `BACKEND_URL` is empty on the Pages project, so the function has no origin to reach and the host behind the tunnel is irrelevant | Set `BACKEND_URL` in the Pages project's environment (section 3 above names the value) and publish the deployment; nothing on this host needs touching, and `curl -I` of any dynamic page answers 200 once the variable has arrived |
| `deadline` | A connection was made and the origin said nothing for the whole window — `UPSTREAM_DEADLINE_MS` spent without a first byte, which the code treats as the end of the retry rather than a blink worth a second wait | Read `deploy/watchdog.ps1 -Check`: `OriginOutcome: silent` with a live process is the wedged uvicorn the supervisor now replaces by itself, so check the pass lines in `data/runtime/watchdog.log` before starting anything by hand |
| `refused` | The connection was made to nothing: no process listens, or it closed the socket mid-request, so every attempt threw before a window could expire | `Origins` in `-Check` is empty when the app has exited — `data/runtime/origin.err.log` carries the traceback that ended it, and the next scheduled pass starts the process again |
| `gateway-5xx` | Something answered, and it was not the app: a 520-527 from Cloudflare's side, or a bare 502/503/504 carrying HTML instead of the JSON every error the app raises renders as | Compare the two logs on the host: `data/runtime/cloudflared-20241.log` and `data/runtime/cloudflared-20242.log` for `timeout: no recent network activity`, `data/runtime/origin.out.log` for a process that is up but not serving — the pin to `http2` is why the first is no longer the common cause |

A reason the code can print and this table does not name, or a row for a word the
code cannot produce, fails `tests/test_design_system.py` — the same file also drives
the shipped function against each of these shapes and refuses a card whose printed
word is not the one that shape deserves.

## Logs and rollbacks

- Function logs: `wrangler pages deployment tail --project-name tippy`
- Backend logs: `docker compose logs -f app` on the VPS
- Previous deployments stay listed under the project's **Deployments** tab;
  promoting one back is a dashboard click, no rebuild.

## Troubleshooting

| Symptom | Cause |
|---|---|
| 503 maintenance card on every dynamic page | The card names its own reason in `x-tippy-outage-reason`; the four words and their remedies are in [Why the card came](#why-the-card-came-and-what-each-reason-says) |
| `cloudflared: Could not lookup srv records on _v2-origintunneld._tcp.argotunnel.com` | The host's resolver answers A but not SRV, which is how VPN/TUN resolvers behave. Allow SRV for `argotunnel.com`, or run the origin where DNS is normal |
| The origin serves `127.0.0.1:8000` fine, `/ready` says 503, and Pages shows the maintenance card | Two causes, measured on this host and described in [The origin on a Windows host](#the-origin-on-a-windows-host). (1) `--protocol auto`: a VPN/TUN interface answers cloudflared's QUIC precheck with PASS and then drops the long-lived UDP streams, so each connection registers and dies on `timeout: no recent network activity`. All three local launchers pin `http2`, whose TCP path the same precheck also passes. (2) `--edge-bind-address`: it selects an address every tunnel connection already uses and costs the connector its edge connections every 22-173 s. Neither flag belongs in the command any more, and one replica is not enough — the second answers while the first has no connection |
| `/` serves and `-Check` says the origin is ready, but the bot answers nobody | Telegram's side of the wire, not this host's: `api.telegram.org` unreachable, or Telegram answering 5xx. The retry rule keeps the process — and the web server inside it — up and logs each failure as `telegram failed, retrying in 15s` in `data/runtime/origin.err.log`. A `TelegramUnauthorizedError` there instead means the token is revoked, and that answer ends the process deliberately: a supervisor that kept restarting it would only hide the one report that says what broke |
| Every page shows the landing page | A `_redirects` rewrite is still in the bundle |
| Signs in, then immediately signed out | `Secure` cookie rejected: check `X-Forwarded-Proto` and that the origin is https |
| `/index.html` (or any `*.html`) answers 200 with the template | The Pages project is serving the bundle without the origin: `BundleFiles` refuses those paths, so a raw template means the function did not run for it |
| The site shows the old design, or the text sets itself in the system font | The bundle was never published: `web/static` on the edge is older than `web/static` in the tree. Run `python -m pytest tests/test_production_health.py -m production` — it names every file that differs. A face the bundle does not carry used to arrive as the landing page with `200 text/html`; the Function refuses that answer now, so a missing file reaches the origin and reads as its 404 — or as the outage card while the origin is unreachable |
| Address bar ends in `/?auth=state` after the Telegram button | Cookie not surviving the proxy (same as above) or older than 30 minutes |
| Address bar ends in `/?auth=slow` | That IP hit `AUTH_RATE_LIMIT` attempts in `AUTH_RATE_WINDOW` seconds — the origin answers the browser with the landing and a reason code, so the limit shows up as a message, not a JSON page |
| Rate limits trigger for one user only, or for everyone | `TRUST_PROXY_XFF` / `TRUSTED_PROXY_PEERS` misconfigured |
| Telegram keeps retrying the webhook | Wrong `secret_token` between `setWebhook` and `WEBHOOK_SECRET` |
