export const onRequestPost = async ({ request, env }) => {
  const backendUrl = env.BACKEND_URL;
  if (!backendUrl) {
    return new Response('Backend not configured', { status: 503 });
  }
  
  const url = new URL(request.url);
  const targetUrl = backendUrl + url.pathname + url.search;
  
  return fetch(targetUrl, {
    method: 'POST',
    headers: request.headers,
    body: request.body,
  });
};
