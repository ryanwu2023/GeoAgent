import { useRef } from "react";
import type { PointerEvent, RefObject } from "react";
import { Maximize2, Minimize2 } from "lucide-react";
import { MAX_RESEARCH_WIDTH, MIN_RESEARCH_WIDTH, researchWidthAtPointer, researchWidthForKey } from "./researchLayout";

export function ResearchDivider({ shellRef, width, onChange }: {
  shellRef: RefObject<HTMLDivElement | null>;
  width: number;
  onChange: (width: number) => void;
}) {
  const drag = useRef<{ pointerId: number; initial: number; latest: number } | null>(null);

  const finish = (event: PointerEvent<HTMLButtonElement>, cancelled = false) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    drag.current = null;
    shellRef.current?.classList.remove("research-resizing");
    const next = cancelled ? current.initial : current.latest;
    shellRef.current?.style.setProperty("--research-dock-width", `${next}%`);
    event.currentTarget.setAttribute("aria-valuenow", String(next));
    event.currentTarget.setAttribute("aria-valuetext", `问答占 ${next}%`);
    onChange(next);
  };

  return <button
    className="research-divider"
    type="button"
    role="separator"
    aria-label="调整看板和问答宽度"
    aria-orientation="vertical"
    aria-valuemin={MIN_RESEARCH_WIDTH}
    aria-valuemax={MAX_RESEARCH_WIDTH}
    aria-valuenow={width}
    aria-valuetext={`问答占 ${width}%`}
    aria-controls="research-dock"
    onPointerDown={event => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.currentTarget.focus();
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { pointerId: event.pointerId, initial: width, latest: width };
      shellRef.current?.classList.add("research-resizing");
    }}
    onPointerMove={event => {
      const current = drag.current;
      if (!current || current.pointerId !== event.pointerId) return;
      const bounds = event.currentTarget.parentElement?.getBoundingClientRect();
      if (!bounds) return;
      const next = researchWidthAtPointer(event.clientX, bounds.left, bounds.width);
      current.latest = next;
      // Keep pointer updates out of App state: resizing should not rerender the
      // map, business records, or the cross-origin chat on every mouse move.
      shellRef.current?.style.setProperty("--research-dock-width", `${next}%`);
      event.currentTarget.setAttribute("aria-valuenow", String(next));
      event.currentTarget.setAttribute("aria-valuetext", `问答占 ${next}%`);
    }}
    onPointerUp={event => finish(event)}
    onPointerCancel={event => finish(event, true)}
    onLostPointerCapture={event => finish(event)}
    onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      onChange(researchWidthForKey(width, event.key));
    }}
    onDoubleClick={() => onChange(42)}
  />;
}

export function ResearchDockHeading({ chatOpen, expanded, onTabChange, onExpand }: {
  chatOpen: boolean;
  expanded: boolean;
  onTabChange: (chat: boolean) => void;
  onExpand: () => void;
}) {
  return <header className="research-dock-heading">
    <div className="research-dock-tabs" role="tablist" aria-label="研究工作区">
      <button id="research-chat-tab" role="tab" aria-selected={chatOpen}
        aria-controls="research-chat-pane" className={chatOpen ? "selected" : ""}
        onClick={() => onTabChange(true)}>智能问答</button>
      <button id="research-insight-tab" role="tab" aria-selected={!chatOpen}
        aria-controls="research-insight-pane" className={!chatOpen ? "selected" : ""}
        onClick={() => onTabChange(false)}>研判</button>
    </div>
    <div className="research-dock-actions">
      <button type="button" aria-label={expanded ? "还原研究工作区" : "放大研究工作区"}
        aria-pressed={expanded} onClick={onExpand}>
        {expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
        <span>{expanded ? "还原" : "放大"}</span>
      </button>
    </div>
  </header>;
}
