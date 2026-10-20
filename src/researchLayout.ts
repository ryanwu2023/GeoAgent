export const DEFAULT_RESEARCH_WIDTH = 42;
export const MIN_RESEARCH_WIDTH = 36;
export const MAX_RESEARCH_WIDTH = 54;

export function clampResearchWidth(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_RESEARCH_WIDTH;
  return Math.min(MAX_RESEARCH_WIDTH, Math.max(MIN_RESEARCH_WIDTH, value));
}

/** Pointer coordinates and bounds are both in CSS viewport pixels. */
export function researchWidthAtPointer(clientX: number, left: number, width: number): number {
  if (!Number.isFinite(clientX) || !Number.isFinite(left) || !Number.isFinite(width) || width <= 0)
    return DEFAULT_RESEARCH_WIDTH;
  return clampResearchWidth(Math.round(((left + width - clientX) / width) * 100));
}

export function researchWidthForKey(width: number, key: string): number {
  switch (key) {
    // Moving the separator left makes the right pane wider.
    case "ArrowLeft": return clampResearchWidth(width + 2);
    case "ArrowRight": return clampResearchWidth(width - 2);
    case "Home": return MIN_RESEARCH_WIDTH;
    case "End": return MAX_RESEARCH_WIDTH;
    default: return clampResearchWidth(width);
  }
}
