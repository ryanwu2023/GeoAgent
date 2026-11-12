import {Globe, Plus, Rss, Trash2} from "lucide-react";
import {fieldHit, hostsFromUrls, type PipelineSource} from "../pipelines";
import TermsArea, {FieldError} from "./TermsArea";

const MAX_SOURCES = 30;

function nextSourceId(sources: PipelineSource[], type: "web" | "rss") {
  const used = new Set(sources.map((s) => s.id));
  let n = sources.length + 1;
  while (used.has(`${type}-${n}`)) n++;
  return `${type}-${n}`;
}

export function newSource(sources: PipelineSource[], type: "web" | "rss"): PipelineSource {
  const base = {id: nextSourceId(sources, type), type, name: "", allowed_hosts: [], language: "en", tier: "T2" as const, content_policy: "metadata_only" as const};
  return type === "rss" ? {...base, feed_url: ""} : {...base, entry_urls: [], link_include_pattern: ""};
}

/** 来源卡片：web 填入口页，rss 填订阅地址；allowed_hosts 为空时按链接自动补齐。 */
export default function SourceEditor({sources, onChange, maxPages, onMaxPages, errField, errMessage}: {
  sources: PipelineSource[]; onChange: (next: PipelineSource[]) => void;
  maxPages: number; onMaxPages: (n: number) => void; errField?: string; errMessage?: string;
}) {
  const patch = (index: number, change: Partial<PipelineSource>) => onChange(sources.map((s, i) => i === index ? {...s, ...change} : s));
  const fillHosts = (index: number, urls: string[]) => { if (!sources[index].allowed_hosts.length) patch(index, {allowed_hosts: hostsFromUrls(urls)}); };
  const hit = (field: string) => fieldHit(errField, field);
  return <div className="pipeline-sources">
    <div className="pipeline-sources-toolbar">
      <span>来源 {sources.length} / {MAX_SOURCES}</span>
      <button type="button" disabled={sources.length >= MAX_SOURCES} onClick={() => onChange([...sources, newSource(sources, "web")])}><Globe size={13} /> 添加网页来源</button>
      <button type="button" disabled={sources.length >= MAX_SOURCES} onClick={() => onChange([...sources, newSource(sources, "rss")])}><Rss size={13} /> 添加 RSS 来源</button>
      <label className={`pipeline-inline-field ${hit("request.max_pages_per_source") ? "invalid" : ""}`}>每个来源最多翻页
        <input type="number" min={1} max={5} value={maxPages} onChange={(e) => onMaxPages(Number(e.target.value))} />
      </label>
    </div>
    <FieldError show={(hit("sources") && !/^sources\[/.test(errField || "")) || hit("request.max_pages_per_source")} message={errMessage} />
    {!sources.length && <p className="pipeline-empty">还没有来源。添加网页或 RSS 来源后可以试跑。</p>}
    {sources.map((s, i) => {
      const p = `sources[${i}]`;
      const invalid = hit(p);
      return <section key={i} className={`pipeline-source-card ${invalid ? "invalid" : ""}`} aria-label={`来源 ${s.name || s.id}`}>
        <header>
          <span className="pipeline-tag">{s.type === "rss" ? "RSS" : "网页"}</span>
          <strong>{s.name || "未命名来源"}</strong>
          <button type="button" className="pipeline-icon-button" aria-label={`删除来源 ${s.name || s.id}`} onClick={() => onChange(sources.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
        </header>
        <div className="pipeline-form-grid">
          <label>来源 ID<input className={hit(`${p}.id`) ? "invalid" : ""} value={s.id} onChange={(e) => patch(i, {id: e.target.value.trim()})} /></label>
          <label>名称<input className={hit(`${p}.name`) ? "invalid" : ""} value={s.name} placeholder="如：路透社世界新闻" onChange={(e) => patch(i, {name: e.target.value})} /></label>
          {s.type === "rss"
            ? <label className="wide">订阅地址（feed_url）<input className={hit(`${p}.feed_url`) ? "invalid" : ""} value={s.feed_url || ""} placeholder="https://example.com/rss.xml"
                onChange={(e) => patch(i, {feed_url: e.target.value.trim()})} onBlur={(e) => fillHosts(i, [e.target.value.trim()])} /></label>
            : <label className="wide">入口页（每行一个）<TermsArea label="入口页" rows={3} value={s.entry_urls || []} invalid={hit(`${p}.entry_urls`)} placeholder="https://example.com/world"
                onChange={(urls) => { patch(i, {entry_urls: urls}); }} /></label>}
          <label className="wide">允许的域名（allowed_hosts，链接必须落在这些域名内）
            <TermsArea label="允许的域名" rows={2} value={s.allowed_hosts} invalid={hit(`${p}.allowed_hosts`)} placeholder="www.example.com"
              onChange={(hosts) => patch(i, {allowed_hosts: hosts.map((h) => h.toLowerCase())})} />
            {!s.allowed_hosts.length && s.type === "web" && (s.entry_urls || []).length > 0 &&
              <button type="button" className="pipeline-link-button" onClick={() => fillHosts(i, s.entry_urls || [])}>按入口页填入域名</button>}
          </label>
          {s.type === "web" && <label className="wide">链接匹配正则（可选，≤200 字符）<input className={hit(`${p}.link_include_pattern`) ? "invalid" : ""} maxLength={200} value={s.link_include_pattern || ""}
            placeholder="如 /world/" onChange={(e) => patch(i, {link_include_pattern: e.target.value})} /></label>}
          <label>语言<input className={hit(`${p}.language`) ? "invalid" : ""} value={s.language} onChange={(e) => patch(i, {language: e.target.value.trim()})} /></label>
          <label>来源等级<select value={s.tier} onChange={(e) => patch(i, {tier: e.target.value as PipelineSource["tier"]})}>
            <option value="T1">T1 权威</option><option value="T2">T2 主流</option><option value="T3">T3 补充</option></select></label>
          <label>正文策略<select value={s.content_policy} onChange={(e) => patch(i, {content_policy: e.target.value as PipelineSource["content_policy"]})}>
            <option value="metadata_only">仅元数据</option><option value="public_fulltext">公开全文</option></select></label>
        </div>
        <FieldError show={invalid} message={errMessage} />
      </section>;
    })}
    {sources.length > 0 && <button type="button" className="pipeline-add-more" disabled={sources.length >= MAX_SOURCES} onClick={() => onChange([...sources, newSource(sources, "web")])}><Plus size={13} /> 再添加一个来源</button>}
  </div>;
}
