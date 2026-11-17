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
the operating system's font. `--commit-dirty=true` is what lets a working tree
publish; it is also how a deploy stops meaning anything about a commit, so the
law below compares the live bytes with the tree instead of trusting either.

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

## Logs and rollbacks

- Function logs: `wrangler pages deployment tail --project-name tippy`
- Backend logs: `docker compose logs -f app` on the VPS
- Previous deployments stay listed under the project's **Deployments** tab;
  promoting one back is a dashboard click, no rebuild.

## Troubleshooting

| Symptom | Cause |
|---|---|
| 503 maintenance card on every dynamic page | `BACKEND_URL` missing on the project, or the origin is down (the function replaces a gateway 5xx that carries no JSON) |
| `cloudflared: Could not lookup srv records on _v2-origintunneld._tcp.argotunnel.com` | The host's resolver answers A but not SRV, which is how VPN/TUN resolvers behave. Allow SRV for `argotunnel.com`, or run the origin where DNS is normal |
| Every page shows the landing page | A `_redirects` rewrite is still in the bundle |
| Signs in, then immediately signed out | `Secure` cookie rejected: check `X-Forwarded-Proto` and that the origin is https |
| `/index.html` (or any `*.html`) answers 200 with the template | The Pages project is serving the bundle without the origin: `BundleFiles` refuses those paths, so a raw template means the function did not run for it |
| The site shows the old design, or the text sets itself in the system font | The bundle was never published: `web/static` on the edge is older than `web/static` in the tree. Run `python -m pytest tests/test_production_health.py -m production` — it names every file that differs, and a missing face is not a 404 but the landing page with `200 text/html` |
| Address bar ends in `/?auth=state` after the Telegram button | Cookie not surviving the proxy (same as above) or older than 30 minutes |
| Address bar ends in `/?auth=slow` | That IP hit `AUTH_RATE_LIMIT` attempts in `AUTH_RATE_WINDOW` seconds — the origin answers the browser with the landing and a reason code, so the limit shows up as a message, not a JSON page |
| Rate limits trigger for one user only, or for everyone | `TRUST_PROXY_XFF` / `TRUSTED_PROXY_PEERS` misconfigured |
| Telegram keeps retrying the webhook | Wrong `secret_token` between `setWebhook` and `WEBHOOK_SECRET` |
