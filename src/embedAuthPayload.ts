// The product user ID is an external binding, not the PiChat shadow-user ID.
// Re-delivery must retain the server-issued identity so PiChat's verified-user
// boundary cannot be overwritten with a fabricated host identity.
export function embedAuthPayload(body: unknown) {
  if (!body || typeof body !== 'object') throw new Error('Invalid embed identity');
  const data = body as Record<string, unknown>;
  const user = data.user as Record<string, unknown> | undefined;
  if (typeof data.token !== 'string' || !data.token || !user || typeof user !== 'object'
      || typeof user.id !== 'string' || !user.id
      || typeof data.userId !== 'string' || data.userId !== user.id) {
    throw new Error('Invalid server-issued embed user');
  }
  return {token:data.token, user};
}
