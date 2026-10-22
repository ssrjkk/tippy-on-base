// Routing for the Tippy Pages project: static bundle from the edge, everything
// else proxied to the FastAPI backend running on the VPS.
//
// Why documents are proxied instead of served as assets:
//   - the backend injects the Content-Security-Policy nonce into the inline
//     script of index.html, and the raw asset carries no valid nonce, so the
//     browser would refuse to run it;
//   - GET / issues the one-time login-state cookie that the Telegram Login
//     Widget callback (/api/auth/telegram) rejects without;
//   - GET /app substitutes the public URL into the mini app meta tags, and
//     /u/{id}, /m/{id}, /tos, /metrics are backend routes, not files.
//
// Assets are matched by extension, so no .html is ever served straight from
// the edge and the routes above always reach the origin.
//
// When the origin is not configured or unreachable, a branded maintenance
// page is returned instead of a raw error — the site never shows a stack of
// internal text. It carries its own copy of the Base tokens from
// web/static/style.css because the page has to render with the asset bundle
// missing too.

const ASSET = /\.(?:css|js|mjs|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|otf|map|txt|webmanifest)$/i;

const MAINTENANCE = `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tippy — временно недоступен</title>
<style>
  :root {
    --blue: #0000ff; --blue-hover: #0000cc; --blue-soft: rgba(0, 0, 255, .07);
    --canvas: #ffffff; --surface: #f8f8f8; --border: #efefef; --border-strong: #dadada;
    --ink: #111111; --muted: #6f6f6f; --muted-2: #9a9a9a; --warning: #8a5a00;
    --pill: 9999px;
    --font-ui: "Schibsted Grotesk", "Helvetica Neue", Helvetica, Arial, sans-serif;
    --font-mono: "Geist Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --blue: #224bff; --blue-hover: #3d61ff; --blue-soft: rgba(34, 75, 255, .16);
      --canvas: #111111; --surface: #1a1a1a; --border: #2d2d2d; --border-strong: #3a3a3a;
      --ink: #ededed; --muted: #9a9a9a; --muted-2: #8b8b8b; --warning: #d99a2b;
      color-scheme: dark;
    }
  }
  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    min-height: 100vh; display: grid; place-items: center; padding: 24px;
    background: var(--canvas); color: var(--ink);
    font: 16px/1.55 var(--font-ui);
    -webkit-font-smoothing: antialiased;
  }
  .card { width: 100%; max-width: 460px; }
  .eyebrow {
    display: flex; align-items: center; gap: 8px;
    font: 500 11px/1 var(--font-mono); letter-spacing: .08em;
    text-transform: uppercase; color: var(--muted); margin-bottom: 20px;
  }
  .dot { width: 7px; height: 7px; border-radius: var(--pill); background: var(--warning); }
  .mark {
    width: 40px; height: 40px; border-radius: 9px; background: var(--blue);
    display: inline-flex; align-items: center; justify-content: center;
    margin-bottom: 20px;
  }
  .mark svg { width: 60%; height: 60%; display: block; }
  h1 { font-size: 30px; font-weight: 600; letter-spacing: -.02em; line-height: 1.2; }
  p { color: var(--muted); margin-top: 12px; font-size: 15px; }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 28px; }
  .btn {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 11px 18px; border-radius: var(--pill);
    font: 500 14px/1 var(--font-ui); text-decoration: none;
    transition: background .15s ease, border-color .15s ease;
  }
  .btn-primary { background: var(--blue); color: #fff; }
  .btn-primary:hover { background: var(--blue-hover); }
  .btn-ghost { border: 1px solid var(--border-strong); color: var(--ink); }
  .btn-ghost:hover { background: var(--surface); }
  .hint {
    margin-top: 28px; padding-top: 18px; border-top: 1px solid var(--border);
    font: 400 12px/1.5 var(--font-mono); color: var(--muted-2);
  }
  :focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
</style>
</head>
<body>
  <div class="card">
    <div class="mark" aria-hidden="true">
      <svg viewBox="0 0 24 24"><text x="12" y="17" font-size="15" font-weight="800" text-anchor="middle" fill="white">b</text></svg>
    </div>
    <div class="eyebrow"><span class="dot"></span>Статус сервиса</div>
    <h1>Tippy временно недоступен</h1>
    <p>Идёт обслуживание. Балансы, чаевые и позиции лежат в контракте и базе
    данных — ничего не потеряется. Попробуйте через несколько минут.</p>
    <div class="actions">
      <a class="btn btn-primary" href="https://t.me/TippyOnBaseBot">Бот в Telegram</a>
      <a class="btn btn-ghost" href="https://github.com/ssrjkk/tippy-on-base">Исходный код</a>
    </div>
    <div class="hint">503 · автоповтор через 5 минут</div>
  </div>
</body>
</html>`;

function maintenance() {
  return new Response(MAINTENANCE, {
    status: 503,
    headers: { 'content-type': 'text/html; charset=utf-8', 'retry-after': '300' },
  });
}

export async function onRequest(context) {
  const { request, env } = context;
  const { pathname, search, host } = new URL(request.url);
  const reads = request.method === 'GET' || request.method === 'HEAD';

  if (reads && ASSET.test(pathname)) {
    const asset = await env.ASSETS.fetch(request);
    if (asset.status !== 404) return asset;
  }

  const backend = (env.BACKEND_URL || '').replace(/\/+$/, '');
  if (!backend) return maintenance();

  const headers = new Headers(request.headers);
  // The origin has its own hostname; forwarding pages.dev as Host makes the
  // backend build absolute URLs for the wrong origin.
  headers.delete('host');
  headers.set('x-forwarded-host', host);
  // TLS ends at Cloudflare, so without this the backend sees plain http and
  // drops the Secure flag from the session and login-state cookies.
  headers.set('x-forwarded-proto', 'https');

  // 52x is Cloudflare's own "the origin never answered" family. A bare
  // 502/503/504 that does not carry JSON comes from a proxy in front of uvicorn,
  // not from the app — every error the app raises renders as JSON. Both mean the
  // visitor would otherwise get a gateway's stock HTML instead of Tippy.
  const isDown = (res) =>
    (res.status >= 520 && res.status <= 527)
    || ([502, 503, 504].includes(res.status)
        && !(res.headers.get('content-type') || '').includes('application/json'));

  const send = () => fetch(backend + pathname + search, {
    method: request.method,
    headers,
    body: reads ? undefined : request.body,
  });

  // Edge subrequests blink occasionally, and for a read the cost of one more
  // try is milliseconds while the alternative is a maintenance card for a page
  // the origin would have served. Writes are never retried: a second POST is a
  // second action, and the money endpoints rely on Idempotency-Key instead.
  let upstream = null;
  for (let attempt = 0, tries = reads ? 2 : 1; attempt < tries; attempt += 1) {
    try {
      upstream = await send();
      if (!isDown(upstream)) break;
    } catch {
      upstream = null;
    }
  }
  return upstream && !isDown(upstream) ? upstream : maintenance();
}
