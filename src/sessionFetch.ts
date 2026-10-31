/** Only our business API's explicit session error can expire the browser login. */
export async function sessionFetch(input: RequestInfo | URL, init?: RequestInit) {
  const response = await globalThis.fetch(input, init);
  if (response.status === 401) {
    const data = await response.clone().json().catch(() => null);
    if (data?.code === 'LOGIN_REQUIRED') window.dispatchEvent(new Event('geoagent-session-expired'));
  }
  return response;
}
