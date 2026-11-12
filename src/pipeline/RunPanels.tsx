import {useState} from "react";
import {History, RotateCcw} from "lucide-react";
import {dateText} from "../api";
import {projectCollectorDiagnosis} from "../collectorDiagnostics";
import {newItemsOf, runLabel, type PipelineRun, type PipelineRunItem, type PipelineVersion} from "../pipelines";

export const STATUS_LABEL: Record<string, string> = {running: "运行中", success: "成功", partial: "部分成功", failed: "失败"};
export const statusLabel = (status: string | null | undefined) => status ? STATUS_LABEL[status] || status : "尚未运行";

function RunError({error}: {error: string}) {
  const code = /^[A-Z_]+$/.test(error) ? error : "";
  const d = projectCollectorDiagnosis({error_type: code, error_message: error});
  return <div className="pipeline-run-error"><b>{code === "SERVICE_INTERRUPTED" ? "服务重启，运行被中断" : d.errorReason}</b><small>建议：{d.suggestedAction}</small></div>;
}

/** 运行记录：区分整批运行与测试运行，可切到该次运行的新增条目。 */
export function RunList({runs, loading, onShowItems}: {runs: PipelineRun[]; loading: boolean; onShowItems: (run: PipelineRun) => void}) {
  if (loading && !runs.length) return <p className="pipeline-empty">正在读取运行记录…</p>;
  if (!runs.length) return <p className="pipeline-empty">还没有运行记录。配置化管线可以先“测试运行”，启用后随整批采集运行。</p>;
  return <ol className="pipeline-run-list">
    {runs.map((run) => {
      const n = newItemsOf(run);
      return <li key={run.run_id} className={`status-${run.status}`}>
        <div className="pipeline-run-head">
          <span className={`pipeline-tag ${run.trigger === "trial" ? "trial" : "batch"}`}>{runLabel(run)}</span>
          <em className={`pipeline-status status-${run.status}`}>{statusLabel(run.status)}</em>
          <small>第 {run.version} 版</small>
          <small>开始 {dateText(run.started_at)}{run.finished_at ? ` · 结束 ${dateText(run.finished_at)}` : ""}</small>
        </div>
        <div className="pipeline-run-meta">
          <span>新增 {n ?? "—"} 条</span>
          <span>{run.trigger === "trial" ? "测试结果，不进快照" : run.snapshot_id ? `快照 ${run.snapshot_id.slice(0, 10)}…` : "待并入快照"}</span>
          {run.status !== "running" && <button type="button" className="pipeline-link-button" onClick={() => onShowItems(run)}>查看新增条目</button>}
        </div>
        {run.error && <RunError error={run.error} />}
      </li>;
    })}
  </ol>;
}

function dimensionText(dimensions: Record<string, unknown>) {
  return Object.entries(dimensions || {}).map(([k, v]) => `${k}：${Array.isArray(v) ? v.join("、") : typeof v === "object" && v ? JSON.stringify(v) : String(v)}`);
}

/** 本次新增：某次运行新进入的条目；测试运行标注不进快照，整批已并入快照的可打开证据。 */
export function NewItems({run, items, total, loading, error, sourceNames, onMore, onOpenEvidence}: {
  run: PipelineRun | null; items: PipelineRunItem[]; total: number; loading: boolean; error: string;
  sourceNames: Record<string, string>; onMore: () => void; onOpenEvidence?: (evidenceId: string, snapshotId: string) => void;
}) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  if (!run) return <p className="pipeline-empty">还没有运行记录，暂无新增条目。</p>;
  const trial = run.trigger === "trial";
  return <div className="pipeline-items">
    <div className={`pipeline-items-banner ${trial ? "trial" : ""}`}>
      <b>{runLabel(run)}</b>
      <span>{statusLabel(run.status)} · {dateText(run.finished_at || run.started_at)} · 共 {total} 条</span>
      <span>{trial ? "测试结果，不进快照" : run.snapshot_id ? `已并入快照 ${run.snapshot_id.slice(0, 10)}…` : "待并入快照"}</span>
    </div>
    {error && <p className="pipeline-error" role="alert">{error}</p>}
    {!loading && !items.length && !error && <p className="pipeline-empty">本次运行没有新增条目。</p>}
    <ul>
      {items.map((item, i) => {
        const dims = dimensionText(item.dimensions);
        return <li key={i}>
          <strong>{item.title}</strong>
          <div className="pipeline-item-meta">
            <span>{sourceNames[item.source_id] || item.source_id}</span>
            <span>发布 {dateText(item.published_at)}</span>
            {item.language && <span>{item.language}</span>}
          </div>
          {item.summary && <p className={expanded[i] ? "" : "collapsed"}>{item.summary}</p>}
          {item.summary && item.summary.length > 80 && <button type="button" className="pipeline-link-button" onClick={() => setExpanded((e) => ({...e, [i]: !e[i]}))}>{expanded[i] ? "收起摘要" : "展开摘要"}</button>}
          {item.matched_terms.length > 0 && <div className="pipeline-chips"><small>命中词</small>{item.matched_terms.map((t) => <i key={t}>{t}</i>)}</div>}
          {dims.length > 0 && <div className="pipeline-chips"><small>维度</small>{dims.map((t) => <i key={t}>{t}</i>)}</div>}
          <div className="pipeline-item-foot">
            {trial ? <small>测试结果，不进快照</small>
              : !run.snapshot_id ? <small>待并入快照</small>
              : item.evidence_id
                ? onOpenEvidence
                  ? <button type="button" className="pipeline-link-button" onClick={() => onOpenEvidence(item.evidence_id as string, run.snapshot_id as string)}>查看证据详情</button>
                  : <small>证据 {item.evidence_id}</small>
                : <small>已并入快照，未关联证据</small>}
          </div>
        </li>;
      })}
    </ul>
    {loading && <p className="pipeline-empty">正在读取新增条目…</p>}
    {!loading && items.length < total && <button type="button" onClick={onMore}>加载更多（{items.length} / {total}）</button>}
  </div>;
}

function versionSummary(v: PipelineVersion) {
  const f = v.config?.filters;
  const terms = f ? f.include_any.length + f.include_strong.length + f.exclude.length : 0;
  return `${v.config?.sources?.length ?? 0} 个来源 · ${terms} 个关键词`;
}
const CHANNEL: Record<string, string> = {web: "网页", mcp: "PiChat", migration: "内置迁移"};

/** 版本历史：列出各版摘要，非当前版本可回滚（由调用方二次确认）。 */
export function VersionList({versions, current, loading, busy, onRollback}: {
  versions: PipelineVersion[]; current: number; loading: boolean; busy: boolean; onRollback: (version: number) => void;
}) {
  if (loading && !versions.length) return <p className="pipeline-empty">正在读取版本历史…</p>;
  if (!versions.length) return <p className="pipeline-empty">暂无版本记录。</p>;
  const currentConfig = versions.find((v) => v.version === current);
  return <ol className="pipeline-version-list">
    {versions.map((v) => <li key={v.version} className={v.version === current ? "current" : ""}>
      <div><b><History size={13} /> 第 {v.version} 版</b>{v.version === current && <span className="pipeline-tag">当前</span>}
        <small>{dateText(v.created_at)} · {v.created_by || "未知"} · {CHANNEL[v.source_channel] || v.source_channel}</small></div>
      <p>{v.change_note || "未填写修改说明"}</p>
      <small>{versionSummary(v)}{currentConfig && v.version !== current ? `（当前版：${versionSummary(currentConfig)}）` : ""}</small>
      {v.version !== current && <button type="button" disabled={busy} onClick={() => onRollback(v.version)}><RotateCcw size={13} /> 回滚到此版本</button>}
    </li>)}
  </ol>;
}
