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
// ``txt`` is deliberately NOT in that extension list. The bundle has no .txt in
// it, and Pages answers a missing path with the landing page — so /robots.txt
// came back as 200 text/html carrying a whole website's markup where a crawler
// expected rules, and a robot that cannot read robots.txt is a robot guessing.
// While the origin is down those two paths are a 503 with a Retry-After, which
// is the honest answer to the same question.
//
// When the origin is not configured or unreachable, a branded maintenance
// page is returned instead of a raw error — the site never shows a stack of
// internal text. It carries its own copy of the Base tokens from
// web/static/style.css because the page has to render with the asset bundle
// missing too, and it names the one thing the origin cannot say from a grave:
// which measurement put it there.

const ASSET = /\.(?:css|js|mjs|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|otf|map|webmanifest)$/i;

// The longest a visitor waits for an origin that is not answering, in
// milliseconds. One number, here: `docs/CLOUDFLARE.md` states it in seconds and
// a gate compares the two, because a promise about an outage is only useful if
// the outage keeps it.
const UPSTREAM_DEADLINE_MS = 15000;

// The four ways this edge can end up painting the card, and the one sentence each
// says to the visitor. `docs/CLOUDFLARE.md` gives every one of these words its own
// remedy, and a gate holds the two lists together: a reason the code can produce
// that the runbook never names is a card nobody knows how to clear, and a runbook
// row for a reason the code cannot produce sends an operator hunting for a fault
// that does not exist.
const OUTAGE_REASONS = {
  'no-backend': 'Origin не настроен в этом проекте Pages.',
  'deadline': 'Origin не успел ответить за отведённое время.',
  'refused': 'Origin не принимает соединение.',
  'gateway-5xx': 'Ответил прокси перед origin, а не приложение.',
};

// The only place a reason is chosen, so the vocabulary stays four words and the
// answer comes from which branch was taken — never from what an exception said.
// A card that quoted a failure would be this edge leaking its own internals into
// a page that anyone can open.
function outageReason({ hasBackend = true, upstream = null, expired = false } = {}) {
  if (!hasBackend) return 'no-backend';
  if (upstream) return 'gateway-5xx';
  if (expired) return 'deadline';
  return 'refused';
}

const MAINTENANCE = (reason) => `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tippy — временно недоступен</title>
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#111111">
<link rel="stylesheet" href="/fonts.css">
<style>
  :root {
    --blue: #0000ff; --blue-hover: #0000cc; --blue-soft: rgba(0, 0, 255, 0.07);
    --canvas: #ffffff; --surface: #f8f8f8; --border: #efefef; --border-strong: #dadada;
    --ink: #111111; --muted: #666666; --muted-2: #707070; --warning: #8a5a00;
    --pill: 9999px;
    --font-ui: "Schibsted Grotesk", "Golos Text", "Helvetica Neue", Helvetica, Arial, sans-serif;
    --font-mono: "Geist Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --blue: #224bff; --blue-hover: #3d61ff; --blue-soft: rgba(34, 75, 255, 0.16);
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
    <div class="hint" data-reason="${reason}">${OUTAGE_REASONS[reason]}<br>503 · автоповтор через 5 минут</div>
  </div>
</body>
</html>`;

function maintenance(reason) {
  // The one document this edge writes by itself, so the one document that has to
  // carry the protection layer by hand: the origin stamps nosniff, Referrer-Policy
  // and no-store on everything it answers, and a card that asks to be read again
  // in five minutes cannot itself be cached. The reason goes out as a header as
  // well as in the markup, because an operator reads an outage with `curl -I` far
  // more often than with a browser.
  return new Response(MAINTENANCE(reason), {
    status: 503,
    headers: {
      'content-type': 'text/html; charset=utf-8',
      'retry-after': '300',
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
      'referrer-policy': 'no-referrer',
      'x-tippy-outage-reason': reason,
    },
  });
}

export async function onRequest(context) {
  const { request, env } = context;
  const { pathname, search, host } = new URL(request.url);
  const reads = request.method === 'GET' || request.method === 'HEAD';

  if (reads && ASSET.test(pathname)) {
    const asset = await env.ASSETS.fetch(request);
    // A miss is not always a 404. Measured on this deployment: a stylesheet the
    // bundle does not carry comes back as the landing document — `200
    // text/html`, with `_headers` still stamping it the code cache window — so a
    // status test alone returns a whole website where a visitor asked for one
    // file, and the browser then paints the operating system's font without ever
    // reporting an error. The payload's type is the fact; the status is not.
    const served = (asset.headers.get('content-type') || '').toLowerCase();
    if (asset.status !== 404 && !served.includes('text/html')) return asset;
  }

  const backend = (env.BACKEND_URL || '').replace(/\/+$/, '');
  
  // When the origin is not configured or unreachable, try to serve the HTML from
  // the bundle as a design demo. The page will render without dynamic data, but
  // the layout, typography and styles will be visible.
  if (!backend && reads) {
    const htmlPath = pathname === '/' ? '/index.html' : pathname + '.html';
    try {
      const demo = await env.ASSETS.fetch(new URL(htmlPath, request.url));
      if (demo.status === 200 && (demo.headers.get('content-type') || '').includes('text/html')) {
        return new Response(demo.body, {
          status: 200,
          headers: {
            'content-type': 'text/html; charset=utf-8',
            'cache-control': 'no-store',
            'x-content-type-options': 'nosniff',
            'referrer-policy': 'no-referrer',
            'x-tippy-demo-mode': 'true',
          },
        });
      }
    } catch {
      // Fall through to maintenance card
    }
  }
  
  if (!backend) return maintenance(outageReason({ hasBackend: false }));

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

  // Measured on this deployment while the laptop's tunnel was mid-reconnect: a
  // request into a black-holed origin sat 22.5 s before the visitor saw the card,
  // because `fetch` has no deadline of its own and the wait fell through to
  // Cloudflare's subrequest limit. A visitor cannot tell a 22-second spinner from a
  // dead tab, which is the one thing this card exists to avoid saying. The window
  // bounds the whole upstream exchange — retries included, since the controller is
  // armed once outside `send()` — and it is 15 s rather than something snappier
  // because this origin has been measured at 7.8 s for a plain health read while the
  // same machine ran its test suite, and a deadline that cuts off a live origin
  // manufactures the outage it is supposed to report.
  const waiting = new AbortController();
  let expired = false;
  const timer = setTimeout(() => { expired = true; waiting.abort(); }, UPSTREAM_DEADLINE_MS);

  const send = () => fetch(backend + pathname + search, {
    method: request.method,
    headers,
    body: reads ? undefined : request.body,
    signal: waiting.signal,
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
      // A refused connection is a blink worth another try; a spent window is the
      // origin having said nothing for fifteen seconds, and a second silence is not
      // evidence — it is only a longer wait before the same card.
      if (expired) break;
    }
  }
  
  // When the origin is down, serve the HTML from the bundle as a design demo
  // instead of the maintenance card. The page will render without dynamic data,
  // but the layout, typography and styles will be visible.
  if ((!upstream || isDown(upstream)) && reads) {
    const htmlPath = pathname === '/' ? '/index.html' : pathname + '.html';
    try {
      const demo = await env.ASSETS.fetch(new URL(htmlPath, request.url));
      if (demo.status === 200 && (demo.headers.get('content-type') || '').includes('text/html')) {
        clearTimeout(timer);
        return new Response(demo.body, {
          status: 200,
          headers: {
            'content-type': 'text/html; charset=utf-8',
            'cache-control': 'no-store',
            'x-content-type-options': 'nosniff',
            'referrer-policy': 'no-referrer',
            'x-tippy-demo-mode': 'true',
          },
        });
      }
    } catch {
      // Fall through to maintenance card
    }
  }
  
  const answer = upstream && !isDown(upstream)
    ? upstream
    : maintenance(outageReason({ upstream, expired }));
  // The deadline guarded the wait for the first byte. Once the origin has begun
  // answering, a body still arriving is a live page, and letting the timer fire
  // would truncate a half-delivered document and call it a maintenance window.
  clearTimeout(timer);
  return answer;
}
