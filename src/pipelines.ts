import {sessionFetch as fetch} from "./sessionFetch";

export type PipelineSource = {
  id: string; type: "web" | "rss"; name: string;
  feed_url?: string; entry_urls?: string[]; allowed_hosts: string[];
  language: string; tier: "T1" | "T2" | "T3"; content_policy: "public_fulltext" | "metadata_only";
  link_include_pattern?: string;
};
export type PipelineConfig = {
  schema_version: string;
  sources: PipelineSource[];
  filters: {include_any: string[]; include_strong: string[]; exclude: string[]; require_category: boolean; lookback_days: number; max_candidates_per_source: number};
  request: {timeout_seconds: number; rate_limit_seconds: number; max_pages_per_source: number};
};
export type PipelineRow = {name: string; description: string; topics: string[]};
export type Pipeline = PipelineRow & {
  id: string; kind: "builtin" | "configurable"; enabled: boolean; current_version: number; owner: string | null;
  last_run_id: string | null; last_status: string | null; last_finished_at: string | null; last_new_items: number;
  source_count: number; config?: PipelineConfig; updated_at?: string | null; created_at?: string;
};
export type PipelineVersion = {pipeline_id: string; version: number; config: PipelineConfig; change_note: string; source_channel: string; created_by: string | null; created_at: string};
export type PipelineRun = {
  run_id: string; pipeline_id: string; trigger: "batch" | "trial"; status: string; snapshot_id: string | null;
  version: number; stats: Record<string, unknown> | null; error: string | null; started_at: string; finished_at: string | null;
};
export type PipelineRunItem = {
  title: string; summary: string | null; source_id: string; source_type: string; published_at: string | null;
  language: string | null; matched_terms: string[]; dimensions: Record<string, unknown>; evidence_id: string | null;
};
export type PreviewCandidate = {title: string; url: string; accepted: boolean; reason: string; matched: string[]};
export type PreviewResult = {sources: Array<{source_id: string; state: string; error: string | null; candidates: Array<{title: string; url: string; accepted: boolean; reason: string; matched: string[]}>}>};
export type PipelineDraft = {row: PipelineRow; config: PipelineConfig};
export type PipelineWriteBody = {row: Partial<PipelineRow>; config: PipelineConfig | null; change_note?: string};
export type ListParams = {topic?: string; status?: string; kind?: string; q?: string; changed_since?: string};

export class PipelineApiError extends Error {
  status: number;
  detail: unknown;
  field?: string;
  constructor(status: number, detail: unknown) {
    super(detailMessage(detail));
    this.name = "PipelineApiError";
    this.status = status;
    this.detail = detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail) && typeof (detail as {field?: unknown}).field === "string") this.field = (detail as {field: string}).field;
  }
}
function detailMessage(detail: unknown): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((x) => x && typeof x === "object" ? `${Array.isArray(x.loc) ? x.loc.join(".") : ""}${x.loc ? ": " : ""}${x.msg ?? ""}` : String(x)).filter(Boolean);
    if (parts.length) return parts.join("；");
  }
  if (detail && typeof detail === "object" && typeof (detail as {message?: unknown}).message === "string" && (detail as {message: string}).message) return (detail as {message: string}).message;
  return "请求失败";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch("/api/pipelines" + path, init);
  const raw = await response.text();
  if (!response.ok) {
    let detail: unknown = raw || undefined;
    try { const data = JSON.parse(raw); detail = data && typeof data === "object" && "detail" in data ? data.detail : data; } catch { /* 保留原始文本 */ }
    throw new PipelineApiError(response.status, detail);
  }
  return (raw ? JSON.parse(raw) : {}) as T;
}
const jsonInit = (method: string, body?: unknown, headers: Record<string, string> = {}): RequestInit =>
  ({method, headers: {"Content-Type": "application/json", ...headers}, body: body === undefined ? undefined : JSON.stringify(body)});
const enc = encodeURIComponent;

export function listPipelines(params: ListParams = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== "") query.set(k, String(v)); });
  const qs = query.toString();
  return request<{items: Pipeline[]; server_time: string}>(qs ? `?${qs}` : "");
}
export const getPipeline = (id: string) => request<Pipeline>(`/${enc(id)}`);
export const createPipeline = (body: {row: PipelineRow; config: PipelineConfig; change_note?: string}) => request<Pipeline>("", jsonInit("POST", body));
export const updatePipeline = (id: string, version: number, body: PipelineWriteBody) =>
  request<Pipeline>(`/${enc(id)}`, jsonInit("PATCH", body, {"If-Match": String(version)}));
export const deletePipeline = (id: string) => request<unknown>(`/${enc(id)}`, {method: "DELETE"});
export const setPipelineEnabled = (id: string, on: boolean) => request<Pipeline>(`/${enc(id)}/${on ? "enable" : "disable"}`, {method: "POST"});
export const trialPipeline = (id: string) => request<{pipeline_id: string; accepted: boolean}>(`/${enc(id)}/trial`, {method: "POST"});
export const previewPipeline = (body: {row: PipelineRow; config: PipelineConfig; limit?: number}) => request<PreviewResult>("/preview", jsonInit("POST", body));
export const rollbackPipeline = (id: string, to: number, expected: number) =>
  request<Pipeline>(`/${enc(id)}/rollback`, jsonInit("POST", {to_version: to, expected_version: expected}));
export const listVersions = (id: string) => request<PipelineVersion[]>(`/${enc(id)}/versions`);
export const listRuns = (id: string) => request<PipelineRun[]>(`/${enc(id)}/runs`);
export const listRunItems = (id: string, runId: string, offset = 0, limit = 50) =>
  request<{items: PipelineRunItem[]; total: number}>(`/${enc(id)}/runs/${enc(runId)}/items?offset=${offset}&limit=${limit}`);

export const isAccessDenied = (err: unknown): boolean => err instanceof PipelineApiError && (err.status === 403 || err.status === 404);

export function emptyDraft(topics: string[]): PipelineDraft {
  return {
    row: {name: "", description: "", topics: [...topics]},
    config: {
      schema_version: "pipeline/1", sources: [],
      filters: {include_any: [], include_strong: [], exclude: [], require_category: true, lookback_days: 14, max_candidates_per_source: 80},
      request: {timeout_seconds: 25, rate_limit_seconds: 1.5, max_pages_per_source: 3},
    },
  };
}
export const runLabel = (run: {trigger: string}) => run.trigger === "trial" ? "测试运行（不进快照）" : "整批运行";
export const unseenCount = (p: {id: string; last_run_id: string | null; last_new_items: number}, seen: Record<string, string>) =>
  seen[p.id] === p.last_run_id ? 0 : p.last_new_items;
export function conflictMessage(err: PipelineApiError): string | null {
  const d = err.detail as {current_version?: unknown} | null | undefined;
  return err.status === 409 && d && typeof d === "object" && d.current_version != null
    ? `该管线已被修改（当前第 ${d.current_version} 版），请刷新后再编辑` : null;
}
export const splitTerms = (text: string): string[] =>
  [...new Set(text.split(/[\n,，、]/).map((s) => s.trim()).filter(Boolean))];

const SEEN_KEY = "geoagent.pipelines.seen";
export function readSeen(): Record<string, string> {
  try {
    const value = JSON.parse(localStorage.getItem(SEEN_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}
export function writeSeen(seen: Record<string, string>) {
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch { /* 隐私模式或存储已满时忽略 */ }
}

export const latestTrial = <T extends {trigger: string}>(runs: T[]): T | null => runs.find((r) => r.trigger === "trial") ?? null;
export function newItemsOf(run: {stats: Record<string, unknown> | null}): number | null {
  const n = run.stats?.new_items;
  return typeof n === "number" ? n : null;
}
/** 422 的 detail.field 是否落在某个输入框上：同一字段、其子路径，或它的父级（如 filters 合计超限）。 */
export function fieldHit(errField: string | undefined | null, field: string): boolean {
  if (!errField) return false;
  return errField === field || errField.startsWith(field + ".") || errField.startsWith(field + "[") || field.startsWith(errField + ".");
}
export type DetailTab = "basic" | "sources" | "keywords" | "items" | "versions" | "runs";
export function tabForField(field: string | undefined | null): DetailTab | null {
  if (!field) return null;
  if (field.startsWith("row.")) return "basic";
  if (field === "sources" || field.startsWith("sources[") || field.startsWith("request.")) return "sources";
  if (field === "filters" || field.startsWith("filters.")) return "keywords";
  return null;
}
export function hostsFromUrls(urls: string[]): string[] {
  const hosts = urls.map((u) => { try { const url = new URL(u); return /^https?:$/.test(url.protocol) ? url.hostname.toLowerCase() : ""; } catch { return ""; } });
  return [...new Set(hosts.filter(Boolean))];
}
export function draftOf(p: PipelineRow & {config?: PipelineConfig}): PipelineDraft {
  return {
    row: {name: p.name, description: p.description ?? "", topics: [...p.topics]},
    config: p.config ? JSON.parse(JSON.stringify(p.config)) : emptyDraft([]).config,
  };
}
/** 列表行只带 last_run_id：整批运行记录 ID 为 “<整批 run_id>:<管线 id>”，测试运行没有冒号。 */
export const lastRunTrigger = (p: {last_run_id: string | null}): "batch" | "trial" | null =>
  p.last_run_id ? (p.last_run_id.includes(":") ? "batch" : "trial") : null;
export const recentlyUpdated = (rows: {last_new_items: number; last_finished_at: string | null}[], now = Date.now()): number =>
  rows.filter((p) => p.last_new_items > 0 && p.last_finished_at && now - Date.parse(p.last_finished_at) <= 86_400_000).length;
