// GeoAgent tells the embedded PiChat panel how it looks. PiChat applies the
// palette only when the embed entry is configured to follow the host window
// (surface.theme.followHost); otherwise the entry's own look stays.
export type HostPalette = {
  canvas: string; panel: string; raised: string; text: string;
  textMuted: string; line: string; accent: string; onAccent: string;
};

/** Taken from the research workbench itself (dark navy, ice-blue accent). */
export const GEO_HOST_PALETTE: Readonly<HostPalette> = Object.freeze({
  canvas: '#0b1524',
  panel: '#13233a',
  raised: '#1a304b',
  text: '#dfebfa',
  textMuted: '#96abc5',
  line: '#273a59',
  accent: '#5ea7ff',
  onAccent: '#0b1524',
});

export function hostThemeMessage(palette: Readonly<HostPalette> = GEO_HOST_PALETTE) {
  return {type: 'finance:theme:set', theme: 'dark' as const, palette: {...palette}};
}

/**
 * When to (re)send the palette: on the panel's identity handshake
 * (finance:auth:request, sent before PiChat has an agent, so it carries none)
 * the loading screens already paint in our colors; on finance:chat:ready after
 * every panel (re)start the chat keeps them.
 */
export function wantsHostTheme(data: unknown, agentId: string): boolean {
  if (!data || typeof data !== 'object') return false;
  const {type, agentId: from} = data as {type?: unknown; agentId?: unknown};
  if (type === 'finance:auth:request') return from === undefined || from === agentId;
  return type === 'finance:chat:ready' && from === agentId;
}
