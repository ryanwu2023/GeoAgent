export type GeoView = {
  version: 'geoagent-view/v1'; snapshot_id: string; topic: string;
  dimension: string; start: string; end: string;
};
type Binding = {panelId: string; agentId: string; sessionId: string | null; workspaceScope?: string};
type Pending = Binding & {requestId: string; revision: number; view: GeoView};
const id = /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$/;
const date = (value: string) => !value || (/^\d{4}-\d{2}-\d{2}$/.test(value)
  && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value);
export const validView = (view: GeoView) =>
  view.version === 'geoagent-view/v1' && [view.snapshot_id,view.topic,view.dimension].every(v => !v || id.test(v))
  && date(view.start) && date(view.end) && !(view.start && view.end && view.start > view.end);
export const trustedViewSource = (origin: string, source: unknown, expectedOrigin: string, expectedSource: unknown) =>
  Boolean(expectedSource) && origin === expectedOrigin && source === expectedSource;

/** Transport-independent state machine. No identity, prompt, or input is stored. */
export class HostViewBridge {
  binding: Binding | null = null;
  pending: Pending | null = null;
  revision = 0;
  constructor(readonly agentId: string, readonly uuid: () => string) {}
  reset() { this.binding = null; this.pending = null; }
  ready(data: Record<string, unknown>): boolean {
    if(data.type !== 'finance:view-context:ready' || data.capability !== 'host-view-context.v1'
      || data.agentId !== this.agentId || typeof data.panelId !== 'string' || !data.panelId
      || !(data.sessionId === null || typeof data.sessionId === 'string')
      || !(data.workspaceScope === undefined || typeof data.workspaceScope === 'string')
      || !Array.isArray(data.supportedViews) || !data.supportedViews.includes('geoagent-view/v1')) return false;
    this.binding = {panelId:data.panelId,agentId:this.agentId,sessionId:data.sessionId,
      ...(data.workspaceScope === undefined ? {} : {workspaceScope:data.workspaceScope})};
    this.pending = null;
    return true;
  }
  send(view: GeoView) {
    this.pending = null;
    if(!this.binding || !validView(view) || !Number.isSafeInteger(this.revision + 1)) return null;
    this.pending = {...this.binding,requestId:this.uuid(),revision:++this.revision,
      view:{version:'geoagent-view/v1',snapshot_id:view.snapshot_id,topic:view.topic,
        dimension:view.dimension,start:view.start,end:view.end}};
    return {type:'finance:view-context:set',...this.pending};
  }
  result(data: Record<string, unknown>): 'received' | 'failed' | null {
    const p=this.pending;
    if(!p || data.type !== 'finance:view-context:result' || data.requestId !== p.requestId
      || data.panelId !== p.panelId || data.agentId !== p.agentId || data.sessionId !== p.sessionId
      || (data.workspaceScope !== undefined && data.workspaceScope !== p.workspaceScope)) return null;
    if(data.ok === false) {
      if(data.revision !== undefined && data.revision !== p.revision) return null;
      this.pending=null; return 'failed';
    }
    if(data.ok !== true || data.revision !== p.revision || data.stage !== 'received'
      || data.modelConsumed !== false || data.appliesTo !== 'next_user_turn'
      || !data.view || typeof data.view !== 'object') return null;
    const v=data.view as Record<string,unknown>;
    if(Object.keys(v).length !== Object.keys(p.view).length || Object.entries(p.view).some(([k,value])=>v[k]!==value)) return null;
    this.pending=null;
    return 'received';
  }
}
// Legacy PiChat session lists use exact snapshot scopes, not parent prefixes.
// Capture on explicit entry; dashboard changes during a panel lifetime must
// never navigate the iframe or move the active conversation.
export function panelEntryScope(current: string, alreadyOpen: boolean, snapshotId: string): string {
  if (alreadyOpen) return current;
  return /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$/.test(snapshotId)
    ? `.geo-research-assistant/${snapshotId}` : '';
}
