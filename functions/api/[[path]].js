export const onRequestGet = async ({ request, env }) => {
  return handleApi(request, env);
};

export const onRequestPost = async ({ request, env }) => {
  return handleApi(request, env);
};

async function handleApi(request, env) {
  const backendUrl = env.BACKEND_URL;
  if (!backendUrl) {
    return new Response('Backend not configured', { status: 503 });
  }
  
  const url = new URL(request.url);
  const targetUrl = backendUrl + url.pathname + url.search;
  
  const headers = new Headers(request.headers);
  headers.set('X-Forwarded-Host', url.host);
  headers.set('X-Forwarded-Proto', 'https');
  
  return fetch(targetUrl, {
    method: request.method,
    headers,
    body: request.method !== 'GET' ? request.body : undefined,
  });
}
