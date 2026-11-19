import {useMemo, useState} from "react";
import {Plus, Search, Trash2} from "lucide-react";
import {fieldHit, newBuiltinSourceId, type BuiltinSource, type BuiltinTermGroup} from "../pipelines";
import TermsArea, {FieldError} from "./TermsArea";

const matches = (q: string, ...texts: string[]) => !q || texts.some((t) => t.toLowerCase().includes(q));

/** 内置采集器来源表：改名称、网址、等级、启用；新增时复制所选模板来源的专有字段；删除即本采集器不再抓这个来源。 */
export function BuiltinSourceEditor({sources, tiers, onChange, errField, errMessage}: {
  sources: BuiltinSource[]; tiers: string[]; onChange: (next: BuiltinSource[]) => void; errField?: string; errMessage?: string;
}) {
  const [query, setQuery] = useState("");
  const [template, setTemplate] = useState("");
  const q = query.trim().toLowerCase();
  const patch = (index: number, change: Partial<BuiltinSource>) => onChange(sources.map((s, i) => i === index ? {...s, ...change} : s));
  const add = () => {
    const base = sources.find((s) => s.id === template) || sources.find((s) => !s.template) || sources[0];
    const id = newBuiltinSourceId(sources);
    onChange([...sources, {id, name: "", url: "", tier: base?.tier || tiers[0] || "", enabled: true, note: "", template: base?.template || base?.id}]);
    setQuery("");
  };
  const hit = (field: string) => fieldHit(errField, field);
  const enabledCount = sources.filter((s) => s.enabled).length;
  return <div className="pipeline-sources">
    <div className="pipeline-sources-toolbar">
      <span>来源 {sources.length} 个（启用 {enabledCount}）</span>
      <label className="pipeline-builtin-search"><Search size={13} /><input aria-label="搜索来源" placeholder="搜索名称或网址" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
      <label>新增时复制<select aria-label="新增来源的模板" value={template} onChange={(e) => setTemplate(e.target.value)}>
        <option value="">第一个来源的设置</option>
        {sources.filter((s) => !s.template).map((s) => <option key={s.id} value={s.id}>{s.name || s.id}</option>)}
      </select></label>
      <button type="button" onClick={add}><Plus size={13} /> 新增来源</button>
    </div>
    <p className="pipeline-hint">新增来源会复制所选模板的抓取方式（RSS/网页、阵营、战区等专有设置），只需填名称和网址；删除的来源下次运行起不再抓取。</p>
    <FieldError show={errField === "sources"} message={errMessage} />
    <div className="pipeline-builtin-table" role="table" aria-label="来源列表">
      <div className="pipeline-builtin-row head" role="row"><span>名称</span><span>网址</span>{tiers.length > 0 && <span>等级</span>}<span>启用</span><span /></div>
      {sources.map((s, i) => {
        const p = `sources[${i}]`;
        if (!matches(q, s.name, s.url, s.id) && !hit(p)) return null;
        return <div key={s.id + i} role="row" className={`pipeline-builtin-row ${hit(p) ? "invalid" : ""} ${tiers.length ? "" : "no-tier"}`}>
          <span><input aria-label={`来源 ${s.id} 名称`} className={hit(`${p}.name`) ? "invalid" : ""} value={s.name} placeholder="来源名称" onChange={(e) => patch(i, {name: e.target.value})} />
            <small>{s.id}{s.template ? " · 新增" : ""}</small></span>
          <span><input aria-label={`来源 ${s.id} 网址`} className={hit(`${p}.url`) ? "invalid" : ""} value={s.url} placeholder="https://…" onChange={(e) => patch(i, {url: e.target.value.trim()})} /></span>
          {tiers.length > 0 && <span><select aria-label={`来源 ${s.id} 等级`} className={hit(`${p}.tier`) ? "invalid" : ""} value={s.tier} onChange={(e) => patch(i, {tier: e.target.value})}>
            {tiers.map((t) => <option key={t} value={t}>{t}</option>)}</select></span>}
          <span><input type="checkbox" aria-label={`启用来源 ${s.id}`} checked={s.enabled} onChange={(e) => patch(i, {enabled: e.target.checked})} /></span>
          <span><button type="button" className="pipeline-icon-button" aria-label={`删除来源 ${s.name || s.id}`} onClick={() => onChange(sources.filter((_, j) => j !== i))}><Trash2 size={14} /></button></span>
          {hit(p) && <FieldError show message={errMessage} />}
        </div>;
      })}
    </div>
  </div>;
}

/** 内置采集器词表：按 config.json 里的位置分组（分桶、实体、立场词等），每组每行一个词。 */
export function BuiltinTermsEditor({groups, terms, onChange, errField, errMessage}: {
  groups: BuiltinTermGroup[]; terms: Record<string, string[]>; onChange: (path: string, next: string[]) => void; errField?: string; errMessage?: string;
}) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const visible = useMemo(() => groups.filter((g) => matches(q, g.label, ...(terms[g.path] || []))
    || fieldHit(errField, `terms.${g.path}`)), [groups, terms, q, errField]);
  const total = groups.reduce((n, g) => n + (terms[g.path]?.length || 0), 0);
  return <div className="pipeline-builtin-terms">
    <div className="pipeline-sources-toolbar">
      <span>{groups.length} 个词表 · {total} 个词</span>
      <label className="pipeline-builtin-search"><Search size={13} /><input aria-label="搜索词表" placeholder="搜索分组或词" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
    </div>
    <p className="pipeline-hint">每行一个词，也可用逗号或顿号分隔。“须同时命中”的几组词要各命中一个才算；部分词按正则匹配，括号等符号需成对。每个词表至少保留一个词。</p>
    {!visible.length && <p className="pipeline-empty">没有匹配的词表。</p>}
    {visible.map((g) => {
      const field = `terms.${g.path}`;
      const invalid = fieldHit(errField, field);
      return <label key={g.path} className={`pipeline-builtin-group ${invalid ? "invalid" : ""}`}>
        <span>{g.label}<small>{terms[g.path]?.length || 0} 个{g.changed ? " · 已改" : ""}</small></span>
        <TermsArea label={g.label} rows={Math.min(6, Math.max(2, Math.ceil((terms[g.path]?.length || 0) / 4)))} value={terms[g.path] || []} invalid={invalid}
          onChange={(next) => onChange(g.path, next)} />
        <FieldError show={invalid} message={errMessage} />
      </label>;
    })}
  </div>;
}
