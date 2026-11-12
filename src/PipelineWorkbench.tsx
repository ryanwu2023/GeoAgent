import "./pipelines.css";
import {useCallback, useEffect, useRef, useState} from "react";
import {FlaskConical, Play, Plus, RefreshCw, Save, Search, Trash2, Workflow} from "lucide-react";
import {dateText} from "./api";
import {
  conflictMessage, createPipeline, deletePipeline, draftOf, emptyDraft, fieldHit, getPipeline, latestTrial,
  listPipelines, listRunItems, listRuns, listVersions, newItemsOf, PipelineApiError, previewPipeline, readSeen, recentlyUpdated,
  rollbackPipeline, runLabel, setPipelineEnabled, tabForField, trialOutcome, trialPipeline, unseenCount, updatePipeline, writeSeen,
  type DetailTab, type Pipeline, type PipelineDraft, type PipelineRun, type PipelineRunItem, type PipelineVersion, type PreviewResult,
} from "./pipelines";
import SourceEditor from "./pipeline/SourceEditor";
import TermsArea, {FieldError} from "./pipeline/TermsArea";
import {NewItems, RunList, statusLabel, VersionList} from "./pipeline/RunPanels";
import {Modal, PreviewDrawer, Warning} from "./pipeline/Dialogs";

const TOPICS: [string, string][] = [["usiran", "美伊"], ["ukraine", "俄乌"]];
const TOPIC_LABEL: Record<string, string> = Object.fromEntries(TOPICS);
const POLL_MS = 15000;
const RUN_POLL_MS = 4000;
const TRIAL_START_TIMEOUT_MS = 60_000;
const MAX_TERMS = 200;
const TABS: [DetailTab, string][] = [["basic", "基本信息"], ["sources", "来源"], ["keywords", "关键词"], ["items", "本次新增"], ["versions", "版本历史"], ["runs", "运行记录"]];
const BUILTIN_TABS = new Set<DetailTab>(["basic", "sources"]);
const BUILTIN_NOTE = "内置采集器随整批采集运行，来源由代码维护";

type Filters = {topic: string; status: string; kind: string; q: string};
type SaveError = {message: string; field?: string; conflict?: boolean};
type ToggleState = {pipeline: Pipeline; on: boolean; runs: PipelineRun[] | null; runsFailed?: boolean; error: string};
/** 点击“测试运行”后的跟踪：before 为点击前最近一次测试运行，deadline 内新运行仍未出现则提示。 */
type TrialWatch = {id: string; before: string | null; deadline: number};
type ItemsState = {runId: string; items: PipelineRunItem[]; total: number; loading: boolean; error: string};

const errText = (e: unknown) => e instanceof Error ? e.message : String(e);
const withCount = (p: Pipeline): Pipeline => p.config ? {...p, source_count: p.config.sources.length} : p;
/** changed_since 往前留 2 秒余量，避免查询与 server_time 之间提交的修改被漏掉（重复条目按 id 合并）。 */
const since = (t: string) => { const ms = Date.parse(t); return Number.isNaN(ms) ? t : new Date(ms - 2000).toISOString(); };

export default function PipelineWorkbench({onOpenEvidence}: {onOpenEvidence?: (evidenceId: string, snapshotId: string) => void}) {
  const [rows, setRows] = useState<Pipeline[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [filters, setFilters] = useState<Filters>({topic: "", status: "", kind: "", q: ""});
  const [seen, setSeen] = useState<Record<string, string>>(readSeen);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Pipeline | null>(null);
  const [draft, setDraft] = useState<PipelineDraft | null>(null);
  const [baseline, setBaseline] = useState("");
  const [detailError, setDetailError] = useState("");
  const [staleVersion, setStaleVersion] = useState<number | null>(null);
  const [tab, setTab] = useState<DetailTab>("basic");
  const [changeNote, setChangeNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<SaveError | null>(null);
  const [notice, setNotice] = useState("");
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [versions, setVersions] = useState<PipelineVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [itemsRunId, setItemsRunId] = useState<string | null>(null);
  const [runItems, setRunItems] = useState<ItemsState | null>(null);
  const [preview, setPreview] = useState<{loading: boolean; result: PreviewResult | null; error: string} | null>(null);
  const [trialStarting, setTrialStarting] = useState(false);
  const [watchingTrial, setWatchingTrial] = useState(false);
  const [toggle, setToggle] = useState<ToggleState | null>(null);
  const [toggling, setToggling] = useState("");
  const [busy, setBusy] = useState(false);

  const serverTime = useRef("");
  const filtersRef = useRef(filters); filtersRef.current = filters;
  const detailRef = useRef(detail); detailRef.current = detail;
  const runsRef = useRef(runs); runsRef.current = runs;
  const dirty = draft != null && JSON.stringify(draft) !== baseline;
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const detailSeq = useRef(0);
  const trialWatch = useRef<TrialWatch | null>(null);
  const stopTrialWatch = () => { trialWatch.current = null; setWatchingTrial(false); };

  const isNew = selectedId === "new";
  const isBuiltin = detail?.kind === "builtin";
  const editable = isNew || detail?.kind === "configurable";
  const sourceNames = Object.fromEntries((draft?.config.sources || []).map((s) => [s.id, s.name || s.id]));
  const hit = (field: string) => fieldHit(saveError?.field, field);

  const upsertRows = useCallback((changed: Pipeline[]) => setRows((list) => {
    const next = [...list];
    for (const p of changed) {
      const i = next.findIndex((x) => x.id === p.id);
      if (i >= 0) next[i] = {...next[i], ...withCount(p)}; else next.push(withCount(p));
    }
    return next.sort((a, b) => a.name.localeCompare(b.name, "zh-CN") || a.id.localeCompare(b.id));
  }), []);

  const loadList = useCallback(async () => {
    setListLoading(true);
    try {
      const {topic, status, kind, q} = filtersRef.current;
      const r = await listPipelines({topic, status, kind, q: q.trim()});
      setRows(r.items); serverTime.current = r.server_time; setListError("");
    } catch (e) { setListError(errText(e)); } finally { setListLoading(false); }
  }, []);

  const loadRuns = useCallback(async (id: string, quiet = false) => {
    if (!quiet) setRunsLoading(true);
    try {
      const next = await listRuns(id);
      if (detailRef.current?.id !== id) return;
      const wasRunning = runsRef.current.some((r) => r.status === "running");
      runsRef.current = next; setRuns(next);
      let refresh = wasRunning && !next.some((r) => r.status === "running");
      const watch = trialWatch.current;
      if (watch && watch.id === id) {
        const outcome = trialOutcome(next, watch.before);
        const trial = latestTrial(next);
        if (outcome === "finished" && trial) {
          // 第一次拉取就已结束也要反馈
          trialWatch.current = null; setWatchingTrial(false); refresh = true;
          setNotice(`测试运行已结束：${statusLabel(trial.status)}，新增 ${newItemsOf(trial) ?? 0} 条（不进快照）`);
        } else if (outcome === "waiting" && Date.now() > watch.deadline) {
          trialWatch.current = null; setWatchingTrial(false);
          setNotice("测试运行未开始或超时，请查看运行记录");
        }
      } else if (refresh && next[0]) {
        setNotice(`${runLabel(next[0])}已结束：${statusLabel(next[0].status)}，新增 ${newItemsOf(next[0]) ?? 0} 条`);
      }
      if (refresh) {
        const fresh = await getPipeline(id);
        upsertRows([fresh]);
        setDetail((d) => d && d.id === id ? {...d, last_run_id: fresh.last_run_id, last_trigger: fresh.last_trigger, last_status: fresh.last_status, last_finished_at: fresh.last_finished_at, last_new_items: fresh.last_new_items, enabled: fresh.enabled} : d);
      }
    } catch (e) { if (!quiet) setNotice(`运行记录读取失败：${errText(e)}`); } finally { if (!quiet) setRunsLoading(false); }
  }, [upsertRows]);

  const loadVersions = useCallback(async (id: string) => {
    setVersionsLoading(true);
    try { const v = await listVersions(id); if (detailRef.current?.id === id) setVersions(v); }
    catch (e) { setNotice(`版本历史读取失败：${errText(e)}`); } finally { setVersionsLoading(false); }
  }, []);

  const applyLoaded = useCallback((p: Pipeline) => {
    const d = draftOf(p);
    detailRef.current = p; // 随后的运行记录、版本请求按 id 校验，需在重渲染前更新
    setDetail(p); setDraft(d); setBaseline(JSON.stringify(d)); setStaleVersion(null); setSaveError(null);
  }, []);

  /** background：轮询发现新版本时的静默重载；请求期间用户开始编辑则不覆盖草稿，只提示有新版本。 */
  const loadDetail = useCallback(async (id: string, background = false) => {
    const seq = ++detailSeq.current;
    setDetailError("");
    try {
      const p = await getPipeline(id);
      if (seq !== detailSeq.current) return;
      if (background && dirtyRef.current) { setStaleVersion(p.current_version); upsertRows([p]); return; }
      applyLoaded(p); upsertRows([p]);
      void loadRuns(id);
      if (p.kind === "configurable") void loadVersions(id); else setVersions([]);
    } catch (e) { if (seq === detailSeq.current) setDetailError(errText(e)); }
  }, [applyLoaded, upsertRows, loadRuns, loadVersions]);

  useEffect(() => { if (!notice) return; const t = window.setTimeout(() => setNotice(""), 8000); return () => window.clearTimeout(t); }, [notice]);
  // 筛选变化后稍等再查，避免搜索框每次按键都请求。
  useEffect(() => { const t = window.setTimeout(() => void loadList(), 300); return () => window.clearTimeout(t); }, [filters, loadList]);

  // 每 15 秒、页面可见时增量拉取 changed_since 之后变化的管线（PiChat 修改、整批或测试运行完成都会出现在这里）。
  useEffect(() => {
    const timer = window.setInterval(async () => {
      if (document.visibilityState !== "visible" || !serverTime.current) return;
      try {
        const {topic, status, kind, q} = filtersRef.current;
        const r = await listPipelines({topic, status, kind, q: q.trim(), changed_since: since(serverTime.current)});
        serverTime.current = r.server_time;
        if (!r.items.length) return;
        upsertRows(r.items);
        const cur = detailRef.current;
        const row = cur && r.items.find((p) => p.id === cur.id);
        if (!cur || !row) return;
        if (row.current_version !== cur.current_version) {
          if (dirtyRef.current) setStaleVersion(row.current_version); else void loadDetail(cur.id, true);
          return;
        }
        setDetail((d) => d && d.id === row.id ? {...d, enabled: row.enabled, last_run_id: row.last_run_id, last_trigger: row.last_trigger, last_status: row.last_status, last_finished_at: row.last_finished_at, last_new_items: row.last_new_items} : d);
        if (row.last_run_id !== cur.last_run_id || row.last_status !== cur.last_status) void loadRuns(cur.id, true);
      } catch { /* 网络抖动时等下一轮 */ }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [upsertRows, loadDetail, loadRuns]);

  // 有运行进行中，或刚提交测试运行、等待其结束时，每 4 秒刷新运行记录。
  const runActive = runs.some((r) => r.status === "running") || watchingTrial;
  const trialRunning = runs.some((r) => r.status === "running" && r.trigger === "trial");
  useEffect(() => {
    const id = detail?.id;
    if (!id || !runActive) return;
    const timer = window.setInterval(() => void loadRuns(id, true), RUN_POLL_MS);
    return () => window.clearInterval(timer);
  }, [detail?.id, runActive, loadRuns]);

  const activeRun = (itemsRunId ? runs.find((r) => r.run_id === itemsRunId) : runs.find((r) => r.status !== "running")) || null;
  const loadItems = useCallback(async (id: string, run: PipelineRun, offset: number) => {
    setRunItems((s) => ({runId: run.run_id, items: offset && s?.runId === run.run_id ? s.items : [], total: s?.runId === run.run_id ? s.total : 0, loading: true, error: ""}));
    try {
      const r = await listRunItems(id, run.run_id, offset);
      setRunItems((s) => s && s.runId === run.run_id ? {...s, items: offset ? [...s.items, ...r.items] : r.items, total: r.total, loading: false} : s);
    } catch (e) { setRunItems((s) => s && s.runId === run.run_id ? {...s, loading: false, error: `新增条目读取失败：${errText(e)}`} : s); }
  }, []);
  useEffect(() => {
    if (tab === "items" && detail && activeRun) void loadItems(detail.id, activeRun, 0);
    // 运行结束（finished_at 变化）时重新读取
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, detail?.id, activeRun?.run_id, activeRun?.finished_at, loadItems]);

  // 打开“本次新增”即视为已查看，清掉列表上的新增标记（只存本浏览器）。
  useEffect(() => {
    if (tab !== "items" || !detail?.last_run_id || seen[detail.id] === detail.last_run_id) return;
    const next = {...seen, [detail.id]: detail.last_run_id};
    setSeen(next); writeSeen(next);
  }, [tab, detail, seen]);

  function confirmDiscard() {
    return !dirty || window.confirm("当前管线有未保存的修改，继续将放弃这些修改，确定吗？");
  }
  function select(id: string) {
    if (id === selectedId || !confirmDiscard()) return;
    setSelectedId(id); setTab("basic"); setItemsRunId(null); setRunItems(null); setRuns([]); setVersions([]); setDetail(null); setDraft(null);
    setChangeNote(""); setSaveError(null); stopTrialWatch();
    void loadDetail(id);
  }
  function startNew() {
    if (!confirmDiscard()) return;
    const d = emptyDraft([filters.topic || "usiran"]);
    detailSeq.current++;
    setSelectedId("new"); setDetail(null); setDraft(d); setBaseline(JSON.stringify(d)); setTab("basic");
    setRuns([]); setVersions([]); setRunItems(null); setItemsRunId(null); setSaveError(null); setStaleVersion(null); setChangeNote(""); setDetailError("");
  }
  function reload() {
    if (!detail || !confirmDiscard()) return;
    void loadDetail(detail.id);
  }
  const patchRow = (change: Partial<PipelineDraft["row"]>) => setDraft((d) => d && {...d, row: {...d.row, ...change}});
  const patchConfig = (fn: (c: PipelineDraft["config"]) => PipelineDraft["config"]) => setDraft((d) => d && {...d, config: fn(d.config)});
  const patchFilters = (change: Partial<PipelineDraft["config"]["filters"]>) => patchConfig((c) => ({...c, filters: {...c.filters, ...change}}));

  function writeFailed(e: unknown) {
    if (e instanceof PipelineApiError) {
      const conflict = conflictMessage(e);
      if (conflict) { setSaveError({message: conflict, conflict: true}); return; }
      if (e.status === 422 && e.field) {
        setSaveError({message: e.message, field: e.field});
        const t = tabForField(e.field); if (t) setTab(t);
        return;
      }
    }
    setSaveError({message: errText(e)});
  }

  async function save() {
    if (!draft || !editable) return;
    setSaving(true); setSaveError(null);
    try {
      const body = {row: draft.row, config: draft.config, change_note: changeNote.trim()};
      const saved = isNew ? await createPipeline(body) : await updatePipeline(detail!.id, detail!.current_version, body);
      detailSeq.current++;
      applyLoaded(saved); upsertRows([saved]); setSelectedId(saved.id); setChangeNote("");
      setNotice(isNew ? "已创建管线（默认未启用）。建议先测试运行，确认结果后再启用。" : `已保存为第 ${saved.current_version} 版`);
      void loadVersions(saved.id);
      if (isNew) setRuns([]);
    } catch (e) { writeFailed(e); } finally { setSaving(false); }
  }

  async function runPreview() {
    if (!draft) return;
    setPreview({loading: true, result: null, error: ""}); setSaveError(null);
    try { setPreview({loading: false, result: await previewPipeline({row: draft.row, config: draft.config, limit: 5}), error: ""}); }
    catch (e) {
      if (e instanceof PipelineApiError && e.status === 422 && e.field) { setPreview(null); writeFailed(e); return; }
      setPreview({loading: false, result: null, error: errText(e)});
    }
  }

  async function startTrial() {
    if (!detail) return;
    setTrialStarting(true);
    try {
      const before = latestTrial(runs)?.run_id ?? null;
      await trialPipeline(detail.id);
      trialWatch.current = {id: detail.id, before, deadline: Date.now() + TRIAL_START_TIMEOUT_MS};
      setWatchingTrial(true); setTab("runs");
      setNotice("已开始测试运行（不进快照），完成后在“运行记录”和“本次新增”查看结果");
      void loadRuns(detail.id, true);
    } catch (e) {
      setNotice(e instanceof PipelineApiError && e.status === 409 && !e.message.includes("远端") ? "该管线正在测试运行" : `测试运行未启动：${errText(e)}`);
    } finally { setTrialStarting(false); }
  }

  function askToggle(p: Pipeline) {
    if (p.kind === "builtin") { void applyToggle(p, !p.enabled); return; }
    setToggle({pipeline: p, on: !p.enabled, runs: p.enabled ? [] : null, error: ""});
    if (!p.enabled) listRuns(p.id).then((r) => setToggle((t) => t && t.pipeline.id === p.id ? {...t, runs: r} : t))
      .catch((e) => setToggle((t) => t && t.pipeline.id === p.id ? {...t, runs: [], runsFailed: true, error: ""} : t));
  }
  async function applyToggle(p: Pipeline, on: boolean) {
    setToggling(p.id);
    try {
      const updated = await setPipelineEnabled(p.id, on);
      upsertRows([updated]);
      setDetail((d) => d && d.id === p.id ? {...d, enabled: updated.enabled} : d);
      setToggle(null);
      setNotice(`${p.name} 已${on ? "启用" : "停用"}${p.kind === "builtin" ? "（已同步采集监控页的排除列表）" : on ? "，将从下一次整批采集起运行" : ""}`);
    } catch (e) {
      if (toggle) setToggle((t) => t && {...t, error: errText(e)}); else setNotice(`切换失败：${errText(e)}`);
    } finally { setToggling(""); }
  }

  async function rollback(version: number) {
    if (!detail) return;
    if (!window.confirm(`确认回滚到第 ${version} 版？将以该版配置生成新的版本${dirty ? "，并放弃当前未保存的修改" : ""}。`)) return;
    setBusy(true);
    try {
      const p = await rollbackPipeline(detail.id, version, detail.current_version);
      applyLoaded(p); upsertRows([p]); void loadVersions(p.id);
      setNotice(`已回滚到第 ${version} 版的配置（新版本：第 ${p.current_version} 版）`);
    } catch (e) { writeFailed(e); } finally { setBusy(false); }
  }

  async function remove() {
    if (!detail || !window.confirm(`确认删除管线“${detail.name}”？删除后不再参与整批采集，已采集的产物也不再进入后续快照。`)) return;
    setBusy(true);
    try {
      await deletePipeline(detail.id);
      setRows((list) => list.filter((p) => p.id !== detail.id));
      detailSeq.current++;
      setSelectedId(null); setDetail(null); setDraft(null); setBaseline(""); setNotice(`已删除管线“${detail.name}”`);
    } catch (e) { setNotice(`删除失败：${errText(e)}`); } finally { setBusy(false); }
  }

  const recent = recentlyUpdated(rows);
  const terms = draft ? draft.config.filters.include_any.length + draft.config.filters.include_strong.length + draft.config.filters.exclude.length : 0;
  const tabs = TABS.filter(([id]) => !isBuiltin || BUILTIN_TABS.has(id)).filter(([id]) => !isNew || ["basic", "sources", "keywords"].includes(id));
  const lastTrial = toggle?.runs ? latestTrial(toggle.runs) : null;

  return (
    <section className="collector-monitor pipeline-workbench" aria-label="采集管线配置">
      <div className="collector-command-bar">
        <div className="collector-command-title">
          <span className="collector-kicker"><Workflow size={14} /> 采集源配置</span>
          <h1>采集管线配置</h1>
          <p>添加网页或 RSS 来源，试跑、测试运行确认无误后启用；已启用的管线随整批采集运行，结果进入快照。</p>
        </div>
        <div className="collector-command-actions">
          <span className="collector-demo-badge">最近 24 小时有新增：{recent} 条管线</span>
          <button onClick={() => void loadList()} disabled={listLoading}><RefreshCw size={14} /> 刷新</button>
          <button className="primary" onClick={startNew}><Plus size={14} /> 新建管线</button>
        </div>
      </div>

      <div className="pipeline-filters">
        <select aria-label="主题" value={filters.topic} onChange={(e) => setFilters({...filters, topic: e.target.value})}>
          <option value="">全部主题</option>{TOPICS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <select aria-label="状态" value={filters.status} onChange={(e) => setFilters({...filters, status: e.target.value})}>
          <option value="">全部状态</option><option value="enabled">已启用</option><option value="disabled">未启用</option>
          <option value="running">运行中</option><option value="success">最近成功</option><option value="partial">最近部分成功</option><option value="failed">最近失败</option>
        </select>
        <select aria-label="类型" value={filters.kind} onChange={(e) => setFilters({...filters, kind: e.target.value})}>
          <option value="">全部类型</option><option value="configurable">配置</option><option value="builtin">内置</option>
        </select>
        <label className="pipeline-search"><Search size={13} /><input aria-label="搜索管线" placeholder="搜索名称或说明" value={filters.q} onChange={(e) => setFilters({...filters, q: e.target.value})} /></label>
      </div>

      <div className="pipeline-layout">
        <div className="pipeline-list" aria-label="管线列表">
          {listError && <p className="pipeline-error" role="alert">管线列表读取失败：{listError}</p>}
          {!listError && !listLoading && !rows.length && <p className="pipeline-empty">没有符合条件的管线。</p>}
          {rows.map((p) => {
            const unseen = unseenCount(p, seen);
            const trigger = p.last_trigger;
            return <div key={p.id} className={`pipeline-row ${p.id === selectedId ? "selected" : ""} ${p.enabled ? "" : "is-disabled"}`}>
              <button type="button" className="pipeline-row-main" onClick={() => select(p.id)}>
                <strong>{p.name}<span className={`pipeline-tag ${p.kind}`}>{p.kind === "builtin" ? "内置" : "配置"}</span>{unseen > 0 && <span className="pipeline-new">新增 {unseen} 条</span>}</strong>
                <small>{p.topics.map((t) => TOPIC_LABEL[t] || t).join("、") || "未设主题"} · {p.kind === "builtin" ? "来源由代码维护" : `${p.source_count} 个来源`}</small>
                <small className={`status-${p.last_status || "none"}`}>{p.last_status ? `${trigger ? runLabel({trigger}) : "最近运行"} · ${statusLabel(p.last_status)} · ${dateText(p.last_finished_at)}` : "尚未运行"}</small>
              </button>
              <label className="pipeline-switch" title={p.kind === "builtin" ? "切换即修改采集监控页的排除列表" : p.enabled ? "停用" : "启用前需确认"}>
                <input type="checkbox" role="switch" aria-label={`${p.enabled ? "停用" : "启用"} ${p.name}`} checked={p.enabled} disabled={toggling === p.id} onChange={() => askToggle(p)} />
                <span>{p.enabled ? "已启用" : "未启用"}</span>
              </label>
            </div>;
          })}
          {listLoading && <p className="pipeline-empty">正在读取管线…</p>}
        </div>

        <div className="pipeline-detail">
          {!selectedId && <div className="pipeline-placeholder"><Workflow size={30} /><p>选择左侧管线查看与编辑，或点击“新建管线”添加采集源。</p></div>}
          {detailError && <p className="pipeline-error" role="alert">管线读取失败：{detailError}</p>}
          {selectedId && !draft && !detailError && <p className="pipeline-empty">正在读取管线…</p>}
          {draft && <>
            <header className="pipeline-detail-head">
              <div><h2>{isNew ? "新建管线" : detail?.name}</h2>
                <small>{isNew ? "保存后默认未启用" : `${detail?.kind === "builtin" ? "内置" : "配置"} · 第 ${detail?.current_version} 版 · ${detail?.enabled ? "已启用" : "未启用"}`}{dirty ? " · 有未保存修改" : ""}</small></div>
            </header>
            {isBuiltin && <Warning>{BUILTIN_NOTE}。在这里切换启用状态等同于修改采集监控页的“排除采集器”。运行情况见‘采集管线监控’页。</Warning>}
            {staleVersion != null && <div className="pipeline-banner" role="status">已有新版本（第 {staleVersion} 版），可能由 PiChat 或其他管理员修改。当前表单有未保存修改，未自动刷新。<button type="button" onClick={reload}>重新加载</button></div>}
            <nav className="pipeline-tabs" role="tablist">
              {tabs.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "selected" : ""} onClick={() => setTab(id)}>{label}</button>)}
            </nav>
            <div className="pipeline-tab-body">
              {tab === "basic" && <div className="pipeline-form-grid">
                <label className="wide">名称<input className={hit("row.name") ? "invalid" : ""} disabled={!editable} maxLength={100} value={draft.row.name} onChange={(e) => patchRow({name: e.target.value})} placeholder="如：路透社中东新闻" />
                  <FieldError show={hit("row.name")} message={saveError?.message} /></label>
                <label className="wide">说明<textarea className={hit("row.description") ? "invalid" : ""} disabled={!editable} rows={3} maxLength={2000} value={draft.row.description} onChange={(e) => patchRow({description: e.target.value})} />
                  <FieldError show={hit("row.description")} message={saveError?.message} /></label>
                <fieldset className={`wide pipeline-topics ${hit("row.topics") ? "invalid" : ""}`}><legend>主题（可多选，至少一个）</legend>
                  {TOPICS.map(([id, label]) => {
                    const checked = draft.row.topics.includes(id);
                    return <label key={id}><input type="checkbox" disabled={!editable || (checked && draft.row.topics.length === 1)} checked={checked}
                      onChange={() => patchRow({topics: checked ? draft.row.topics.filter((t) => t !== id) : TOPICS.map(([t]) => t).filter((t) => t === id || draft.row.topics.includes(t))})} /> {label}</label>;
                  })}
                  <FieldError show={hit("row.topics")} message={saveError?.message} />
                </fieldset>
              </div>}
              {tab === "sources" && (isBuiltin
                ? <p className="pipeline-empty">内置采集器，来源由代码维护。</p>
                : <SourceEditor sources={draft.config.sources} onChange={(sources) => patchConfig((c) => ({...c, sources}))}
                    errField={saveError?.field} errMessage={saveError?.message} />)}
              {tab === "keywords" && <div className="pipeline-form-grid">
                <p className="wide pipeline-hint">每行一个词，也可用逗号或顿号分隔。锚词需命中标题或至少两个；强锚词命中一个即可；排除词命中即剔除。合计 {terms} / {MAX_TERMS}。</p>
                <label>锚词<TermsArea label="锚词" value={draft.config.filters.include_any} invalid={hit("filters.include_any")} onChange={(v) => patchFilters({include_any: v})} placeholder="伊朗&#10;Iran" /></label>
                <label>强锚词<TermsArea label="强锚词" value={draft.config.filters.include_strong} invalid={hit("filters.include_strong")} onChange={(v) => patchFilters({include_strong: v})} placeholder="霍尔木兹" /></label>
                <label>排除词<TermsArea label="排除词" value={draft.config.filters.exclude} invalid={hit("filters.exclude")} onChange={(v) => patchFilters({exclude: v})} placeholder="体育" /></label>
                <FieldError show={hit("filters.include_any") || hit("filters.include_strong") || hit("filters.exclude")} message={saveError?.message} />
                <label className="wide pipeline-check"><input type="checkbox" checked={!draft.config.filters.require_category} onChange={(e) => patchFilters({require_category: !e.target.checked})} /> 不要求命中事项词（默认要求同时命中军事、制裁、谈判等事项词）</label>
                <label>回看天数（1~90）<input type="number" min={1} max={90} className={hit("filters.lookback_days") ? "invalid" : ""} value={draft.config.filters.lookback_days} onChange={(e) => patchFilters({lookback_days: Number(e.target.value)})} /></label>
                <label>每个来源最多候选（1~200）<input type="number" min={1} max={200} className={hit("filters.max_candidates_per_source") ? "invalid" : ""} value={draft.config.filters.max_candidates_per_source} onChange={(e) => patchFilters({max_candidates_per_source: Number(e.target.value)})} /></label>
                <FieldError show={hit("filters.lookback_days") || hit("filters.max_candidates_per_source")} message={saveError?.message} />
              </div>}
              {tab === "items" && detail && <NewItems key={activeRun?.run_id ?? "none"} run={activeRun} items={runItems?.items || []} total={runItems?.total || 0} loading={!!runItems?.loading} error={runItems?.error || ""}
                sourceNames={sourceNames} onOpenEvidence={onOpenEvidence} onMore={() => activeRun && void loadItems(detail.id, activeRun, runItems?.items.length || 0)} />}
              {tab === "versions" && detail && <VersionList versions={versions} current={detail.current_version} loading={versionsLoading} busy={busy} onRollback={(v) => void rollback(v)} />}
              {tab === "runs" && detail && <RunList runs={runs} loading={runsLoading} onShowItems={(run) => { setItemsRunId(run.run_id); setTab("items"); }} />}
            </div>
            {saveError && !saveError.field && <div className="pipeline-banner error" role="alert">{saveError.message}
              {saveError.conflict && <button type="button" onClick={reload}>重新加载</button>}</div>}
            {saveError?.field && !tabForField(saveError.field) && <div className="pipeline-banner error" role="alert">{saveError.message}</div>}
            {editable && <footer className="pipeline-footer">
              <input aria-label="修改说明" placeholder="修改说明（可选，记入版本历史）" value={changeNote} maxLength={200} onChange={(e) => setChangeNote(e.target.value)} />
              <button type="button" disabled={!draft.config.sources.length || preview?.loading} onClick={() => void runPreview()} title="按当前表单抓取少量候选，不写任何数据"><FlaskConical size={14} /> 试跑</button>
              <button type="button" className="primary" disabled={saving || (!dirty && !isNew)} onClick={() => void save()}><Save size={14} /> {saving ? "保存中…" : isNew ? "创建" : "保存"}</button>
              {!isNew && <button type="button" disabled={trialStarting || dirty || trialRunning} onClick={() => void startTrial()}
                title={dirty ? "有未保存修改，请先保存再测试运行" : "按已保存版本完整运行一次，结果进“本次新增”，不进快照"}><Play size={14} /> {trialRunning ? "测试运行中…" : "测试运行"}</button>}
              {!isNew && <button type="button" className="pipeline-danger" disabled={busy} onClick={() => void remove()}><Trash2 size={14} /> 删除</button>}
            </footer>}
          </>}
        </div>
      </div>

      {preview && <PreviewDrawer loading={preview.loading} result={preview.result} error={preview.error} sourceNames={sourceNames} onClose={() => setPreview(null)} />}

      {toggle && (toggle.on
        ? <Modal title={`确认启用“${toggle.pipeline.name}”`} kicker="启用即同意加入整批采集" busy={toggling === toggle.pipeline.id} onClose={() => setToggle(null)}
            actions={<><button type="button" onClick={() => setToggle(null)}>取消</button><button type="button" className="primary" disabled={toggling === toggle.pipeline.id || toggle.runs === null} onClick={() => void applyToggle(toggle.pipeline, true)}>确认启用</button></>}>
            <p className="pipeline-hint">启用后将从下一次整批采集起与现有采集器一起运行，结果进入快照。</p>
            <div className="pipeline-trial-summary">
              <b>最近一次测试运行</b>
              {toggle.runs === null ? <span>正在读取…</span>
                : toggle.runsFailed ? <span>无法确认最近测试运行</span>
                : lastTrial ? <span>{statusLabel(lastTrial.status)} · 新增 {newItemsOf(lastTrial) ?? "—"} 条 · {lastTrial.finished_at ? `完成于 ${dateText(lastTrial.finished_at)}` : "尚未结束"} · 第 {lastTrial.version} 版</span>
                : <span>尚未测试运行</span>}
            </div>
            {toggle.runsFailed && <Warning>运行记录读取失败，无法确认这条管线是否测试运行过、结果是否正常。仍可启用，但请确认来源与过滤结果无误。</Warning>}
            {toggle.runs && !toggle.runsFailed && !lastTrial && <Warning>这条管线尚未测试运行，建议先测试运行确认来源与过滤结果再启用。</Warning>}
            {lastTrial && lastTrial.status !== "success" && lastTrial.status !== "running" && <Warning>最近一次测试运行未完全成功，请确认来源可用后再启用。</Warning>}
            {lastTrial && lastTrial.version !== toggle.pipeline.current_version && <Warning>最近一次测试运行基于第 {lastTrial.version} 版，当前已是第 {toggle.pipeline.current_version} 版。</Warning>}
            {dirty && detail?.id === toggle.pipeline.id && <Warning>表单中的未保存修改不会随启用生效，启用的是已保存的第 {detail.current_version} 版。</Warning>}
            {toggle.error && <p className="pipeline-error" role="alert">{toggle.error}</p>}
          </Modal>
        : <Modal title={`确认停用“${toggle.pipeline.name}”`} busy={toggling === toggle.pipeline.id} onClose={() => setToggle(null)}
            actions={<><button type="button" onClick={() => setToggle(null)}>取消</button><button type="button" className="primary" disabled={toggling === toggle.pipeline.id} onClick={() => void applyToggle(toggle.pipeline, false)}>确认停用</button></>}>
            <Warning>停用后从下一次整批采集起不再运行，该管线已采集的产物也将不再进入后续快照（已生成的快照不受影响）。</Warning>
            {toggle.error && <p className="pipeline-error" role="alert">{toggle.error}</p>}
          </Modal>)}

      {notice && <div className="collector-notice" role="status">{notice}<button aria-label="关闭提示" onClick={() => setNotice("")}>×</button></div>}
    </section>
  );
}
