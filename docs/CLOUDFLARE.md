# Cloudflare Pages + VPS

## What runs where

| Piece | Where | What it serves |
|---|---|---|
| Cloudflare Pages | `web/static` (build output dir) | `style.css`, `app.js`, `site.js`, images |
| Pages Functions | `functions/[[path]].js` | proxies every other request to the VPS |
| FastAPI backend | VPS, Docker (`web/server.py`) | `/`, `/login`, `/app`, `/me`, `/u/{id}`, `/m/{id}`, `/tos`, `/metrics`, `/qr`, `/api/*`, `/telegram-webhook`, `/.well-known/farcaster.json` |

The function catches all requests first (a Pages Function takes precedence over
the asset bundle) and asks the asset bundle only for `GET`/`HEAD` paths that end
in a known static extension. Everything else — including the HTML pages — goes
to the origin. Two reasons documents must not be served as bare assets:

- `GET /` injects the Content-Security-Policy nonce into the inline script of
  `index.html` and issues the one-time login-state cookie that
  `/api/auth/telegram` requires. The raw file has neither.
- `GET /app` substitutes the public URL into the mini app meta tags.

For the same reason there is no `_redirects` file in the bundle: a
`/* /index.html 200` rewrite is applied before Functions and would swallow
`/api/*`, `/app`, `/telegram-webhook` and every other dynamic path.

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

## 3. Backend URL

Set `BACKEND_URL` to the origin, without a trailing slash:

```bash
echo -n "https://tippy.yourdomain.com" | wrangler pages secret put BACKEND_URL --project-name tippy
```

or in the dashboard under **Workers & Pages → tippy → Settings → Variables**.
Requests that reach the function without it return 503 with a plain-text
explanation instead of a silent failure.

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

## Logs and rollbacks

- Function logs: `wrangler pages deployment tail --project-name tippy`
- Backend logs: `docker compose logs -f app` on the VPS
- Previous deployments stay listed under the project's **Deployments** tab;
  promoting one back is a dashboard click, no rebuild.

## Troubleshooting

| Symptom | Cause |
|---|---|
| 503 `BACKEND_URL is not set…` | Variable missing on the Pages project |
| Every page shows the landing page | A `_redirects` rewrite is still in the bundle |
| Signs in, then immediately signed out | `Secure` cookie rejected: check `X-Forwarded-Proto` and that the origin is https |
| `403 missing or expired login state` | Cookie not surviving the proxy (same as above) or older than 30 minutes |
| Rate limits trigger for one user only, or for everyone | `TRUST_PROXY_XFF` / `TRUSTED_PROXY_PEERS` misconfigured |
| Telegram keeps retrying the webhook | Wrong `secret_token` between `setWebhook` and `WEBHOOK_SECRET` |
