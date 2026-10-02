# Cloudflare Deployment Guide

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Cloudflare CDN │────▶│  Pages Functions │────▶│  Backend (VPS)  │
│  (Static files) │     │  (API proxy)     │     │  (FastAPI)      │
└─────────────────┘     └──────────────────┘     └─────────────────┘
       /app, /style.css         /api/*              Docker + Postgres
```

- **Cloudflare Pages**: serves static files (index.html, app.html, style.css, app.js)
- **Pages Functions**: proxies `/api/*` and `/telegram-webhook` to backend
- **Backend**: runs on VPS/Render/Railway with Docker, exposed via public URL

## Setup

### 1. Install Wrangler CLI

```bash
npm install -g wrangler
wrangler login
```

### 2. Deploy to Cloudflare Pages

```bash
# First time setup
wrangler pages project create tippy --production-branch main

# Deploy
wrangler pages deploy web/static --project-name tippy
```

### 3. Configure Backend URL

In Cloudflare Dashboard:
1. Go to **Workers & Pages** → **tippy** → **Settings** → **Variables**
2. Add environment variable:
   - **Name**: `BACKEND_URL`
   - **Value**: `https://your-backend-url.com` (your VPS/Render/Railway URL)
   - **Encrypt**: ✅

Or via CLI:
```bash
wrangler pages secret put BACKEND_URL --project-name tippy
```

### 4. Custom Domain (optional)

```bash
# In Cloudflare Dashboard:
# Workers & Pages → tippy → Custom domains → Set up
# Or via CLI:
wrangler pages domain add tippy.example.com --project-name tippy
```

## Backend Deployment

The backend still needs a server with Docker. Options:

### Option A: VPS (recommended for full control)

```bash
# On your VPS
git clone https://github.com/ssrjkk/tippy-on-base.git
cd tippy-on-base
cp .env.example .env
# Edit .env with your secrets
docker compose up -d
```

### Option B: Render/Railway (quick start)

Use the existing Dockerfile:
- **Render**: Web Service → connect repo → Docker
- **Railway**: connect repo → auto-detects Dockerfile

Set environment variables in the platform dashboard.

### Option C: Cloudflare Workers (advanced)

Port the Python backend to JavaScript/TypeScript for Workers.
Not recommended — the backend uses PostgreSQL, web3.py, and complex state.

## Webhook Configuration

Update Telegram webhook to point to Cloudflare:

```bash
curl -X POST https://api.telegram.org/bot<BOT_TOKEN>/setWebhook \
  -H "Content-Type: application/json" \
  -d '{"url": "https://tippy.example.com/telegram-webhook"}'
```

## Monitoring

- **Cloudflare Analytics**: Dashboard → Workers & Pages → tippy → Analytics
- **Backend logs**: `docker compose logs -f app` on your VPS
- **Function logs**: `wrangler pages deployment tail --project-name tippy`

## Benefits of Cloudflare Pages

- **Global CDN**: static files served from 300+ locations
- **Free tier**: 500 builds/month, unlimited bandwidth
- **Automatic HTTPS**: certificates managed by Cloudflare
- **Fast deploys**: `wrangler pages deploy` in seconds
- **Preview deployments**: every PR gets a preview URL

## Troubleshooting

| Issue | Solution |
|-------|----------|
| API returns 503 | Check `BACKEND_URL` is set in Pages variables |
| Webhook not working | Verify backend URL is public and webhook is set |
| Static files 404 | Check `wrangler.toml` has `pages_build_output_dir = "web/static"` |
| CORS errors | Backend should allow Cloudflare domain in CORS origins |
