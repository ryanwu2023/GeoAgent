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

/** Re-send after every panel (re)start: PiChat announces it with finance:chat:ready. */
export function isPanelReady(data: unknown, agentId: string): boolean {
  return !!data && typeof data === 'object'
    && (data as {type?: unknown}).type === 'finance:chat:ready'
    && (data as {agentId?: unknown}).agentId === agentId;
}
