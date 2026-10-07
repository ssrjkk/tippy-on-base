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
//     /u/{id}, /m/{id}, /me, /tos, /metrics are backend routes, not files.
//
// Assets are matched by extension, so no .html is ever served straight from
// the edge and the routes above always reach the origin.

const ASSET = /\.(?:css|js|mjs|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|otf|map|txt|webmanifest)$/i;

export async function onRequest(context) {
  const { request, env } = context;
  const { pathname, search, host } = new URL(request.url);
  const reads = request.method === 'GET' || request.method === 'HEAD';

  if (reads && ASSET.test(pathname)) {
    const asset = await env.ASSETS.fetch(request);
    if (asset.status !== 404) return asset;
  }

  const backend = (env.BACKEND_URL || '').replace(/\/+$/, '');
  if (!backend) {
    return new Response('BACKEND_URL is not set for this Pages deployment', {
      status: 503,
      headers: { 'content-type': 'text/plain; charset=utf-8' },
    });
  }

  const headers = new Headers(request.headers);
  // The origin has its own hostname; forwarding pages.dev as Host makes the
  // backend build absolute URLs for the wrong origin.
  headers.delete('host');
  headers.set('x-forwarded-host', host);
  // TLS ends at Cloudflare, so without this the backend sees plain http and
  // drops the Secure flag from the session and login-state cookies.
  headers.set('x-forwarded-proto', 'https');

  return fetch(backend + pathname + search, {
    method: request.method,
    headers,
    body: reads ? undefined : request.body,
  });
}
