import {AlertTriangle, FlaskConical, X} from "lucide-react";
import type {ReactNode} from "react";
import {isWebUrl, type PreviewResult} from "../pipelines";

export function Modal({title, kicker, onClose, busy, children, actions}: {
  title: string; kicker?: string; onClose: () => void; busy?: boolean; children: ReactNode; actions: ReactNode;
}) {
  return <div className="collector-config-backdrop pipeline-modal-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <section className="collector-config-dialog pipeline-modal" role="dialog" aria-modal="true" aria-label={title}>
      <div className="collector-config-heading">
        <div>{kicker && <span className="collector-kicker">{kicker}</span>}<h2>{title}</h2></div>
        <button type="button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={17} /></button>
      </div>
      {children}
      <div className="collector-config-actions">{actions}</div>
    </section>
  </div>;
}

export function Warning({children}: {children: ReactNode}) {
  return <p className="pipeline-warning"><AlertTriangle size={14} /> <span>{children}</span></p>;
}

const STATE_LABEL: Record<string, string> = {ok: "正常", no_data: "无候选", restricted: "访问受限", failed: "失败"};

/** 试跑结果抽屉：按来源列出候选、是否入选、原因与命中词。 */
export function PreviewDrawer({loading, result, error, sourceNames, builtin = false, onClose}: {
  loading: boolean; result: PreviewResult | null; error: string; sourceNames: Record<string, string>; builtin?: boolean; onClose: () => void;
}) {
  return <div className="pipeline-drawer-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <aside className="pipeline-drawer" role="dialog" aria-modal="true" aria-label="试跑结果">
      <header>
        <div><span className="collector-kicker"><FlaskConical size={13} /> 试跑 · 不写任何数据</span><h2>试跑结果</h2>
          <p>{builtin
            ? "按当前表单（含未保存修改）用原采集代码只跑改动过的来源（没有改动时取前 5 个启用来源），列出入选和被口径排除的条目。"
            : "按当前表单（含未保存修改）每个来源抓取前几条候选，检查过滤是否符合预期。"}</p></div>
        <button type="button" className="pipeline-icon-button" aria-label="关闭试跑结果" onClick={onClose}><X size={17} /></button>
      </header>
      {loading && <p className="pipeline-empty">正在试跑，通常需要几十秒{builtin ? "，最长约 2 分半" : ""}…</p>}
      {error && <p className="pipeline-error" role="alert">{error}</p>}
      {result?.sources.map((src) => {
        const accepted = src.candidates.filter((c) => c.accepted).length;
        return <section key={src.source_id} className="pipeline-preview-source">
          <h3>{sourceNames[src.source_id] || src.source_id}<em className={`pipeline-status status-${src.state}`}>{STATE_LABEL[src.state] || src.state}</em>
            <small>{typeof src.fetched === "number" ? `抓到 ${src.fetched} 条 · ` : ""}{builtin ? "列出" : "候选"} {src.candidates.length} 条 · 入选 {accepted} 条</small></h3>
          {src.error && <p className="pipeline-error">{src.error_message || src.error}</p>}
          {!src.candidates.length && !src.error && <p className="pipeline-empty">{builtin
            ? "没有条目通过这个采集器的关键词口径（或来源没有新内容）。可检查网址，或到“关键词”里放宽词表。"
            : "没有抓到候选，检查入口页、订阅地址或链接匹配正则。"}</p>}
          <ul>
            {src.candidates.map((c, i) => <li key={i} className={c.accepted ? "accepted" : "rejected"}>
              <span className="pipeline-tag">{c.accepted ? "入选" : "未入选"}</span>
              <div>{isWebUrl(c.url) ? <a href={c.url} target="_blank" rel="noreferrer noopener">{c.title || c.url}</a> : <span>{c.title || c.url}</span>}
                <small>{c.reason || (c.accepted ? "符合过滤条件" : "未通过过滤")}{c.matched.length ? ` · 命中：${c.matched.join("、")}` : ""}</small></div>
            </li>)}
          </ul>
        </section>;
      })}
      {result && !result.sources.length && <p className="pipeline-empty">试跑没有返回任何来源结果。</p>}
    </aside>
  </div>;
}
