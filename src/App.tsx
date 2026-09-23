import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  BookOpen,
  ChevronRight,
  Database,
  Download,
  FileText,
  Globe2,
  Layers,
  MessageSquare,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  X,
} from "lucide-react";
import { api, dateText, num } from "./api";
import type {
  Answer,
  Asset,
  Brief,
  Evidence,
  Metric,
  Status,
  Workspace,
} from "./types";
import ReviewPanel from "./ReviewPanel";
import MetricChart from "./MetricChart";
import MetricGuide from "./MetricGuide";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { briefPresentation } from "./briefPresentation";

const MapView = lazy(() => import("./MapView"));

const tierName = (tier: string) =>
  ({ T1: "一级来源", T2: "二级来源", T3: "三级来源", T4: "四级来源" })[tier] ||
  "未分级";
const chineseMetric = (s: string) =>
  s
    .replace("Treasury MTS net outlay", "美国财政部月度净支出")
    .replace("美国总统支持率聚合", "特朗普支持率（Silver Bulletin 聚合）")
    .replace("(FMS)", "（对外军售）")
    .replace("(FMF)", "（对外军事融资）");
const names: Record<string, string> = {
  all: "综合研判",
  usiran: "美伊局势",
  ukraine: "俄乌冲突",
  briefs: "研究简报",
};

const metricCategory = (m: Metric) => {
  if (
    ["RCPPTAPP_approve", "taco_trump_approval"].includes(m.series || "") ||
    /特朗普.*支持率|总统支持率/.test(m.name)
  )
    return "民调";
  if (m.bucket === "shipping") return "动态";
  if (
    m.shared_market ||
    ["market", "equity", "rates", "credit", "commodity", "fx"].includes(
      m.bucket || "",
    )
  )
    return "经济";
  return "军事";
};
const movementClass = (m: Metric) =>
  m.delta == null || m.delta === 0 ? "flat" : m.delta > 0 ? "up" : "down";
export default function App() {
  const [sourceMetric, setSourceMetric] = useState<Metric | null>(null);
  const [translating, setTranslating] = useState(false);
  const [checkingModel, setCheckingModel] = useState(false),
    [modelCheck, setModelCheck] = useState("");
  const [metricSearch, setMetricSearch] = useState(""),
    [metricGroup, setMetricGroup] = useState("全部");
  const [status, setStatus] = useState<Status | null>(null),
    [sid, setSid] = useState(""),
    [page, setPage] = useState("usiran"),
    [dimension, setDimension] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [work, setWork] = useState<Workspace | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [loading, setLoading] = useState(false),
    [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState<Evidence | null>(null),
    [asset, setAsset] = useState<Asset | null>(null),
    [sourcesOpen, setSourcesOpen] = useState(false),
    [search, setSearch] = useState(""),
    [tab, setTab] = useState("map"),
    [limit, setLimit] = useState(30);
  const [chatOpen, setChatOpen] = useState(false),
    [question, setQuestion] = useState(""),
    [answer, setAnswer] = useState<Answer | null>(null),
    [asking, setAsking] = useState(false),
    [replay, setReplay] = useState(false),
    [history, setHistory] = useState<Answer[]>([]);
  const [briefs, setBriefs] = useState<Brief[]>([]),
    [brief, setBrief] = useState<Brief | null>(null),
    [generating, setGenerating] = useState(false),
    [compare, setCompare] = useState(""),
    [diff, setDiff] = useState("");
  const [resetKey, setResetKey] = useState(0);
  const queryVersion = useRef(0),
    selectionVersion = useRef(0);
  const lastTopic = useRef("usiran");
  if (page !== "briefs") lastTopic.current = page;
  const topic = lastTopic.current;
  const workspaceCache = useRef(
    new Map<string, { time: number; data: Workspace }>(),
  );
  async function refreshStatus() {
    const s = await api<Status>("/status");
    setStatus(s);
    return s;
  }
  useEffect(() => {
    if (!status?.translation?.running || !sid) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const s = await api<Status>("/status");
        const w = await api<Workspace>(
          "/workspace?" +
            new URLSearchParams({
              snapshot_id: sid,
              topic,
              dimension,
              start,
              end,
            }),
        );
        if (!cancelled) {
          setStatus(s);
          setWork(w);
          setSelected((current) =>
            current
              ? w.records.find((r) => r.id === current.id) || current
              : null,
          );
        }
      } catch {}
    }, 60000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [status?.translation?.running, sid, topic, dimension, start, end]);
  useEffect(() => {
    refreshStatus()
      .then((s) => {
        if (s.snapshots.length) setSid(s.snapshots[0].id);
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (!sid) return;
    const version = ++queryVersion.current;
    const key = new URLSearchParams({
      snapshot_id: sid,
      topic,
      dimension,
      start,
      end,
    }).toString();
    const cached = workspaceCache.current.get(key);
    setError("");
    setSelected(null);
    setAsset(null);
    setLimit(30);
    if (cached && Date.now() - cached.time < 60000) {
      setWork(cached.data);
      setLoading(false);
      return;
    }
    setLoading(true);
    setWork(null);
    api<Workspace>("/workspace?" + key)
      .then((w) => {
        workspaceCache.current.set(key, { time: Date.now(), data: w });
        if (workspaceCache.current.size > 8)
          workspaceCache.current.delete(
            workspaceCache.current.keys().next().value!,
          );
        if (queryVersion.current === version) setWork(w);
      })
      .catch((e) => {
        if (queryVersion.current === version) setError(e.message);
      })
      .finally(() => {
        if (queryVersion.current === version) setLoading(false);
      });
  }, [sid, topic, dimension, start, end]);
  useEffect(() => {
    selectionVersion.current++;
    setAnswer(null);
    setReplay(false);
    setHistory([]);
    setBrief((prev) => (prev?.snapshot_id === sid ? prev : null));
    setDiff("");
    if (sid) {
      const v = selectionVersion.current;
      api<Answer[]>("/answers?snapshot_id=" + sid)
        .then((h) => {
          if (v === selectionVersion.current)
            setHistory(
              h.filter(
                (a) =>
                  a.topic === topic &&
                  (a.filters?.dimension || "") === dimension,
              ),
            );
        })
        .catch(() => {});
      api<Brief[]>("/briefs")
        .then(setBriefs)
        .catch(() => {});
    }
  }, [sid, topic, dimension]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelected(null);
        setAsset(null);
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  async function importData() {
    setImporting(true);
    setError("");
    try {
      const r = await api<{
        id: string;
        created: boolean;
        count: number;
        errors: unknown[];
      }>("/import", {});
      await refreshStatus();
      setSid(r.id);
      setNotice(
        `${r.created ? "已冻结新快照" : "数据未变化，使用已有快照"} · ${r.count} 条归并记录${r.errors.length ? ` · ${r.errors.length} 条解析异常` : ""}`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setImporting(false);
    }
  }
  async function openEvidence(id: string, metric: Metric | null = null) {
    if (!sid) return;
    const version = selectionVersion.current;
    try {
      const r = await api<Evidence>(`/evidence/${id}?snapshot_id=${sid}`);
      if (version === selectionVersion.current) {
        setSelected(r);
        setSourceMetric(metric);
      }
      setAsset(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function ask(q = question) {
    if (!q.trim() || !sid) return;
    const previous_answer_id = answer?.id || "";
    const version = selectionVersion.current;
    setAsking(true);
    setAnswer(null);
    setReplay(false);
    setChatOpen(true);
    setSourcesOpen(false);
    setTab("chat");
    try {
      const a = await api<Answer>("/ask", {
        snapshot_id: sid,
        question: q,
        topic,
        dimension,
        start,
        end,
        previous_answer_id,
      });
      if (version === selectionVersion.current) {
        setAnswer(a);
        setHistory((h) => [a, ...h]);
        api<Workspace>(
          "/workspace?" +
            new URLSearchParams({
              snapshot_id: sid,
              topic,
              dimension,
              start,
              end,
            }),
        ).then((w) => {
          if (version === selectionVersion.current) setWork(w);
        });
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAsking(false);
    }
  }
  async function generate() {
    if (!sid) return;
    setGenerating(true);
    setError("");
    const version = selectionVersion.current;
    try {
      const b = await api<Brief>("/briefs", { snapshot_id: sid });
      setBriefs((bs) => [b, ...bs]);
      if (version === selectionVersion.current) {
        setPage("briefs");
        setChatOpen(false);
        setSourcesOpen(false);
        setDimension("");
        setStart("");
        setEnd("");
        setBrief(b);
      }
      setNotice("完整快照简报已生成，包含两个主题；不受页面报道日期筛选限制。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGenerating(false);
    }
  }
  function navigate(id: string) {
    setPage(id);
    setDimension("");
    setSearch("");
    setSelected(null);
    setAsset(null);
    setTab(id === "all" ? "assets" : "map");
    setChatOpen(false);
    setSourcesOpen(false);
  }
  const visible =
    work?.records.filter((r) =>
      (
        r.title +
        " " +
        r.text +
        " " +
        (r.original_title || "") +
        " " +
        r.source_name +
        " " +
        r.signal_label +
        " " +
        r.event_category_label
      )
        .toLowerCase()
        .includes(search.toLowerCase()),
    ) || [];
  const topicInfo = work?.topics.find((t) => t.id === topic);
  const dimensionCount =
    work?.topics.flatMap((t) => t.dimensions).filter((d) => d.count > 0)
      .length || 0;
  const sourceStates: Record<string, string> = {
    failed: "采集失败",
    no_data: "暂无有效数据",
    ok: "采集正常",
    "ok-empty": "正常无新增",
    imported: "已导入 · 采集状态未知",
    not_connected: "未接入",
    disabled: "已停用",
    "fail-http": "采集失败",
  };
  return (
    <div className="prototype-shell">
      <header className="prototype-header">
        <div className="prototype-brand">
          <b>地缘</b>
          <div>
            地缘政治智能体<small>指标变化 · 证据核验 · 资产关联</small>
          </div>
        </div>
        <nav className="theme-tabs">
          {["usiran", "ukraine", "all"].map((id) => (
            <button
              className={page === id ? "selected" : ""}
              key={id}
              onClick={() => navigate(id)}
            >
              {names[id]}
            </button>
          ))}
        </nav>
        <div className="header-actions">
          <select
            aria-label="数据快照"
            value={sid}
            onChange={(e) => setSid(e.target.value)}
          >
            {!sid && <option value="">尚未导入</option>}
            {status?.snapshots.map((s) => (
              <option key={s.id} value={s.id}>
                {dateText(s.created_at)} · {s.id.slice(0, 8)}
              </option>
            ))}
          </select>
          <button
            disabled={importing}
            onClick={importData}
            title="导入爬虫数据"
          >
            <RefreshCw size={15} className={importing ? "spin" : ""} />
            <span>更新快照</span>
          </button>
          <button
            className="primary"
            aria-label="生成双主题简报"
            disabled={!sid || generating}
            onClick={generate}
          >
            <FileText size={15} />
            <span>{generating ? "生成中…" : "生成简报"}</span>
          </button>
        </div>
      </header>
      {(error || notice) && (
        <div
          className={"global-notice " + (error ? "error" : "")}
          role={error ? "alert" : "status"}
        >
          {error || notice}
          <button
            aria-label="关闭提示"
            onClick={() => {
              setError("");
              setNotice("");
            }}
          >
            <X size={14} />
          </button>
        </div>
      )}
      <div className="prototype-grid">
        <main className="monitor-panel">
          <div className="monitor-tabs">
            {[
              ["map", "风险监测"],
              ["evidence", "证据台账"],
              ["scenarios", "情景推演"],
              ["sources", "数据源"],
              ["chat", "智能问答"],
              ["metrics", "量化指标"],
              ["assets", "资产关联"],
              ["briefs", "研究简报"],
            ].map(([id, label]) => (
              <button
                key={id}
                className={
                  (page === "briefs" ? id === "briefs" : tab === id)
                    ? "selected"
                    : ""
                }
                onClick={() => {
                  if (id === "briefs") {
                    setPage("briefs");
                    setChatOpen(false);
                    setSourcesOpen(false);
                  } else {
                    if (page === "briefs") setPage(lastTopic.current);
                    setTab(id);
                    setChatOpen(id === "chat");
                    setSourcesOpen(id === "sources");
                  }
                }}
              >
                {label}
              </button>
            ))}
          </div>
          {page !== "briefs" && (
            <div className="compact-filters">
              <select
                aria-label="跟踪维度"
                value={dimension}
                onChange={(e) => setDimension(e.target.value)}
              >
                <option value="">全部跟踪维度</option>
                {topicInfo?.dimensions.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} · {d.count || "待接入"}
                  </option>
                ))}
              </select>
              <input
                aria-label="开始日期"
                type="date"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
              <span>—</span>
              <input
                aria-label="结束日期"
                type="date"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
              {(start || end) && (
                <button
                  onClick={() => {
                    setStart("");
                    setEnd("");
                  }}
                >
                  清除
                </button>
              )}
              <small>报道日期 · 冻结快照</small>
              {tab === "map" && (
                <button
                  className="reset-map"
                  onClick={() => setResetKey((k) => k + 1)}
                >
                  重置地图
                </button>
              )}
            </div>
          )}
          {!sid && (
            <div className="empty landing">
              <Database size={38} />
              <h2>接入真实爬虫数据</h2>
              <p>保存原始文件、历史版本与不可变快照。</p>
              <code>{status?.crawler_root}</code>
              <button
                className="primary"
                disabled={importing}
                onClick={importData}
              >
                导入真实数据
              </button>
            </div>
          )}
          {loading && (
            <div className="empty">
              <RefreshCw className="spin" />
              正在加载快照…
            </div>
          )}
          {work && page !== "briefs" && (
            <div
              className={
                "center-view " + (tab === "map" ? "map-view" : "scroll-view")
              }
            >
              {tab === "map" &&
                <div
                  style={{ height: "100%", width: "100%", minWidth: 0, flex: 1 }}
                >
                  <Suspense fallback={<div className="empty"><RefreshCw className="spin" />正在加载地图…</div>}>
                    <MapView
                      records={work.records}
                      topic={topic}
                      resetKey={resetKey}
                      onSelect={(r) => {
                        openEvidence(r.id);
                        setAsset(null);
                      }}
                    />
                  </Suspense>
                </div>
              }
              {tab === "evidence" && (
                <>
                  <div className="evidence-tools">
                    <div className="search-box">
                      <Search size={16} />
                      <input
                        placeholder="检索中文标题、摘要或来源"
                        aria-label="检索证据"
                        value={search}
                        onChange={(e) => {
                          setSearch(e.target.value);
                          setLimit(30);
                        }}
                      />
                    </div>
                    <small>{visible.length} 条 · 查看新闻详情</small>
                  </div>
                  <div className="evidence-list">
                    {visible.slice(0, limit).map((r) => (
                      <button
                        className="evidence-row"
                        key={r.id}
                        onClick={() => openEvidence(r.id)}
                      >
                        <div className="evidence-time">
                          {r.published_at?.slice(0, 10) || "日期未知"}
                          <span>{tierName(r.tier)}</span>
                        </div>
                        <div className="evidence-main">
                          <strong>{r.title}</strong>
                          <p>{r.text || "此记录没有正文摘要，请回源核验。"}</p>
                          <div>
                            <span>{r.source_name}</span>
                            <span className="pill tiny">
                              {r.event_category_label}
                            </span>
                            <span className="pill tiny">{r.signal_label}</span>
                            <span className="pill tiny subdued">
                              {r.verification}
                            </span>
                          </div>
                        </div>
                        <ChevronRight size={16} />
                      </button>
                    ))}
                    {!visible.length && (
                      <div className="empty">
                        <BookOpen />
                        <h3>该范围尚无可用证据</h3>
                        <p>
                          可以调整筛选，或接入该维度的爬虫；不会用其他主题数据补齐。
                        </p>
                      </div>
                    )}
                  </div>
                  {visible.length > limit && (
                    <button
                      className="load-more"
                      onClick={() => setLimit(limit + 30)}
                    >
                      再显示 30 条
                    </button>
                  )}
                </>
              )}
              {tab === "metrics" && (
                <>
                  <div className="metric-controls">
                    <input
                      aria-label="搜索量化指标"
                      placeholder="搜索指标、单位或统计口径"
                      value={metricSearch}
                      onChange={(e) => setMetricSearch(e.target.value)}
                    />
                    <div>
                      {["全部", "经济", "民调", "军事", "动态"].map((g) => (
                        <button
                          key={g}
                          className={metricGroup === g ? "selected" : ""}
                          onClick={() => setMetricGroup(g)}
                        >
                          {g}{" "}
                          {
                            work.metrics.filter(
                              (m) => g === "全部" || metricCategory(m) === g,
                            ).length
                          }
                        </button>
                      ))}
                    </div>
                    <small className="metric-color-key">
                      红色表示高于前值，绿色表示低于前值；只表示数值方向，不代表资产利好或利空。
                    </small>
                  </div>
                  {metricGroup === "民调" &&
                    !work.metrics.some((m) => metricCategory(m) === "民调") && (
                    <article className="metric-card">
                      <h3>民调 · 调查口径待补全</h3>
                      <p>
                        已有新闻线索，尚缺调查起止日期、调查机构、样本量、完整题目和回答选项。不同问题的百分比不能连接成支持率趋势。
                      </p>
                      <button
                        onClick={() => {
                          setSearch("");
                          setDimension(
                            topic === "ukraine" ? "eu_domestic" : "us_opinion",
                          );
                          if (topic === "all") setPage("usiran");
                          setTab("evidence");
                        }}
                      >
                        查看民调报道
                      </button>
                    </article>
                  )}
                  <div className="metrics-grid">
                    {work.metrics
                      .filter(
                        (m) =>
                          (metricGroup === "全部" ||
                            metricCategory(m) === metricGroup) &&
                          (
                            m.name +
                            " " +
                            m.scope +
                            " " +
                            m.unit +
                            " " +
                            m.method
                          )
                            .toLowerCase()
                            .includes(metricSearch.toLowerCase()),
                      )
                      .map((m) => (
                        <article className="metric-card" key={m.id}>
                          <div className="metric-label">
                            {chineseMetric(m.name)}
                          </div>
                          <small
                            className={
                              m.freshness === "可用" ? "muted" : "warning-text"
                            }
                          >
                            数据状态：{m.freshness || "待核对"} · 观测日期{" "}
                            {m.current?.date || "未提供"}
                          </small>
                          <div className="metric-value">
                            {num(m.current?.value, m.unit)}{" "}
                            {m.unit === "USD" ? "" : m.unit}
                          </div>
                          <div className={"metric-delta " + movementClass(m)}>
                            {m.pct == null
                              ? m.delta == null
                                ? "历史不足"
                                : `${m.delta >= 0 ? "+" : ""}${num(m.delta)} ${m.delta_unit || m.unit}`
                              : `${m.pct >= 0 ? "+" : ""}${m.pct.toFixed(2)}%`}
                            <span> 相对前 {m.comparison_step || 1} 期</span>
                          </div>
                          <div className="metric-baseline">
                            前值 {num(m.previous?.value, m.unit)} ·{" "}
                            {m.previous?.date || "无可比日期"}
                            <br />
                            变化额 {num(m.delta, m.delta_unit || m.unit)}{" "}
                            {m.unit === "USD" ? "" : m.delta_unit || m.unit}
                            <br />
                            {m.frequency || ""} ·{" "}
                            {m.new_point === false
                              ? "本轮无新观测"
                              : m.new_point === true
                                ? "本轮有新观测"
                                : ""}{" "}
                            {m.lag_days != null
                              ? `· 数据距采集日 ${m.lag_days} 天`
                              : ""}
                          </div>
                          {m.quality_issue && (
                            <p className="alert">{m.quality_issue}</p>
                          )}
                          <MetricChart metric={m} />
                          <small>
                            {m.scope} · {chineseMetric(m.method)}
                          </small>
                          <button
                            className="metric-source"
                            onClick={() => openEvidence(m.source_ids[0], m)}
                          >
                            指标含义与来源
                          </button>
                        </article>
                      ))}
                    {(metricGroup === "全部" || metricGroup === "军事") &&
                      !metricSearch && (
                        <div className="metric-card missing">
                          <div className="metric-label">弹药绝对库存</div>
                          <div className="metric-value">不可观测</div>
                          <p>
                            现有合同与产能信息是代理，不能转换为真实库存数量。
                          </p>
                        </div>
                      )}
                    {!work.metrics.length && (
                      <div className="empty">
                        <p>当前主题尚未接入可比较的结构化指标。</p>
                      </div>
                    )}
                    {metricGroup !== "民调" &&
                      !work.metrics.some(
                        (m) =>
                          (metricGroup === "全部" ||
                            metricCategory(m) === metricGroup) &&
                          (
                            m.name +
                            " " +
                            m.scope +
                            " " +
                            m.unit +
                            " " +
                            m.method
                          )
                            .toLowerCase()
                            .includes(metricSearch.toLowerCase()),
                      ) && (
                        <p role="status">没有匹配指标，请更换关键词或分类。</p>
                      )}
                  </div>
                </>
              )}
              {tab === "scenarios" && (
                <>
                  <div className="panel-footer">
                    <h3>情景推演是什么？</h3>
                    <p>
                      比较持续消耗、升级扩散和缓和收束三条可能路径，明确成立条件、反证和资产传导。当前报道提供线索；只有行动变化与独立证据得到核验，才能更新情景判断。下列清单是判断所需的数据，不是情景发生概率。
                    </p>
                  </div>
                  <div className="scenario-grid">
                    {work.scenarios.map((s) => (
                      <article className="scenario" key={s.id}>
                        <h3>{s.name}</h3>
                        <span className="pill">{s.status}</span>
                        <label>成立条件</label>
                        <p>{s.condition}</p>
                        <label>反证 / 失效条件</label>
                        <p>{s.counter}</p>
                        <label>下一观察项</label>
                        <p>{s.watch}</p>
                        <label>若成立，对资产意味着什么</label>
                        <p>
                          {s.id === "escalate"
                            ? "若供应或运输持续受损，能源成本与通胀压力可能上升，进口方及长久期资产承压；避险需求可能支撑黄金，仍需核对实际利率。"
                            : s.id === "ease"
                              ? "若协议执行且供给运输恢复，能源风险溢价可能回落，进口行业成本改善；避险资产也可能回吐溢价。"
                              : "若行动持续但供应未进一步受损，新增冲击可能有限；重点观察补库订单、财政负担与行业盈利能否持续。"}
                        </p>
                        <label>判断还需要哪些数据</label>
                        <ul>
                          {s.requirements?.map((x) => (
                            <li key={x}>{x}</li>
                          ))}
                        </ul>
                        <p>{s.gaps}</p>
                        <label>待核验线索</label>
                        <div className="citation-buttons">
                          {s.evidence_ids.map((id, i) => (
                            <button key={id} onClick={() => openEvidence(id)}>
                              证据 {i + 1}
                            </button>
                          ))}
                          {!s.evidence_ids.length && (
                            <small>当前没有匹配材料</small>
                          )}
                        </div>
                      </article>
                    ))}
                  </div>
                  <div className="panel-footer">
                    不根据报道计数计算情景概率；每项情景需要研究员核验。
                  </div>
                </>
              )}
              {tab === "assets" && (
                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <h2>大类资产关联矩阵</h2>
                      <p>
                        当前市场观察与条件影响分开呈现；点击资产查看依据和待补数据。
                      </p>
                    </div>
                    <button
                      disabled={asking}
                      onClick={() => {
                        setQuestion(
                          "分析当前主题对大类资产的条件影响、反证和情景",
                        );
                        ask("分析当前主题对大类资产的条件影响、反证和情景");
                      }}
                    >
                      <MessageSquare size={14} />
                      分析资产影响
                    </button>
                  </div>
                  <div className="section-heading">
                    <div>
                      <h3>重点指标变化</h3>
                      <p>按当前主题与资产渠道选取，同类最多两项。</p>
                      <div className="priority-metrics">
                        {work.important_metrics?.map((m) => (
                          <button
                            className="priority-metric"
                            key={m.id}
                            onClick={() => openEvidence(m.source_ids[0], m)}
                          >
                            <strong>{chineseMetric(m.name)}</strong>
                            <span>
                              {num(m.current?.value, m.unit)}{" "}
                              {m.unit === "USD" ? "" : m.unit}
                            </span>
                            <small className={"metric-movement " + movementClass(m)}>
                              {m.delta === null
                                ? "暂无可比前值"
                                : `较 ${m.previous?.date} 变化 ${num(m.delta, m.delta_unit)} ${m.delta_unit === "USD" ? "" : m.delta_unit}`}
                            </small>
                            <small>
                              {m.current?.date} · {m.freshness} · 查看口径
                            </small>
                          </button>
                        ))}
                      </div>
                      <details>
                        <summary>
                          数据时效与缺口（{work.data_warnings?.length || 0} 项）
                        </summary>
                        {work.data_warnings?.map((w, i) => (
                          <p key={i}>
                            {chineseMetric(w.name)}：{w.status} ·{" "}
                            {w.date || "无日期"}
                          </p>
                        ))}
                        <p>
                          按快照或筛选截止日计算；默认日频七天、周频十四天、月频四十五天。滞后观测保留查阅，不参与当前市场判断。
                        </p>
                      </details>
                    </div>
                  </div>
                  <div className="table-scroll asset-table">
                    <table>
                      <thead>
                        <tr>
                          <th>资产</th>
                          <th>传导渠道</th>
                          <th>市场观察</th>
                          <th>当前研究观点</th>
                          <th>候选材料</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {work.assets.map((a) => (
                          <tr
                            key={a.id}
                            onClick={() => {
                              setAsset(a);
                              setSelected(null);
                            }}
                            tabIndex={0}
                            onKeyDown={(e) => e.key === "Enter" && setAsset(a)}
                          >
                            <td data-label="资产" className="asset-name">
                              {a.name}
                            </td>
                            <td data-label="传导渠道">{a.channel}</td>
                            <td data-label="市场观察">
                              <span className="pill subdued">
                                {a.direction}
                              </span>
                            </td>
                            <td data-label="研究观点" className="asset-view">
                              {a.view || a.mechanism}
                              {a.topic_paths?.map((p) => (
                                <p key={p.topic}>
                                  <strong>
                                    {names[p.topic]} · {p.channel}
                                  </strong>
                                  <br />
                                  {p.condition}
                                  <br />
                                  <small>{p.status}</small>
                                </p>
                              ))}
                              <small style={{ display: "block", marginTop: 8 }}>
                                待补：{a.missing_data}
                              </small>
                            </td>
                            <td data-label="候选材料">
                              {a.evidence_ids.length} 条线索
                            </td>
                            <td>
                              <ChevronRight size={15} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="panel-footer">
                    已接入市场观测；不同序列更新日期可能不同，见资产详情。当前标签描述市场变化，不代表已确认冲突造成该变化。
                  </div>
                </section>
              )}
            </div>
          )}
          {sid && page === "briefs" && (
            <div className="brief-layout">
              <section className="panel brief-list">
                <div className="section-heading">
                  <h2>简报档案</h2>
                </div>
                {briefs.map((b) => (
                  <button
                    key={b.id}
                    className={
                      brief?.id === b.id ? "brief-item selected" : "brief-item"
                    }
                    onClick={() => {
                      setBrief(b);
                      setDiff("");
                      setCompare("");
                    }}
                  >
                    <FileText size={17} />
                    <div>
                      <strong>{dateText(b.created_at)}</strong>
                      <small>{b.mode}</small>
                      <small>
                        快照 {b.snapshot_id.slice(0, 8)}{" "}
                        {b.snapshot_id !== sid ? "· 其他版本" : ""}
                      </small>
                    </div>
                  </button>
                ))}
                {!briefs.length && (
                  <div className="empty">
                    点击“生成双主题简报”创建首份档案。
                  </div>
                )}
              </section>
              <section className="panel brief-content">
                {brief ? (
                  <>
                    <div className="section-heading">
                      <div>
                        <h2>研究简报</h2>
                        <p>
                          历史档案 · {brief.mode} · 快照{" "}
                          {brief.snapshot_id.slice(0, 8)}
                        </p>
                      </div>
                      <a
                        className="button"
                        href={`/api/briefs/${brief.id}/download`}
                      >
                        <Download size={15} />
                        导出研究简报
                      </a>
                    </div>
                    <div className="compare-bar">
                      <select
                        aria-label="比较简报"
                        value={compare}
                        onChange={(e) => setCompare(e.target.value)}
                      >
                        <option value="">选择另一份简报比较</option>
                        {briefs
                          .filter((b) => b.id !== brief.id)
                          .map((b) => (
                            <option key={b.id} value={b.id}>
                              {dateText(b.created_at)} ·{" "}
                              {b.snapshot_id.slice(0, 8)}
                            </option>
                          ))}
                      </select>
                      <button
                        disabled={!compare}
                        onClick={() =>
                          api<{ diff: string }>(
                            `/brief-diff?before=${compare}&after=${brief.id}`,
                          )
                            .then((r) => setDiff(r.diff || "两份简报内容一致"))
                            .catch((e) => setError(e.message))
                        }
                      >
                        比较版本
                      </button>
                    </div>
                    <div className="brief-text">
                      {diff ? (
                        <pre>{diff}</pre>
                      ) : (
                        <Markdown remarkPlugins={[remarkGfm]}>
                          {briefPresentation(brief.markdown)}
                        </Markdown>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="empty">
                    <FileText />
                    <h3>选择简报查看完整内容</h3>
                    <p>档案保留生成模式、快照和来源，方便复核判断变化。</p>
                  </div>
                )}
              </section>
            </div>
          )}
          {sourcesOpen && (
            <div
              className="drawer-backdrop source-backdrop"
              onClick={() => {
                setSourcesOpen(false);
                setTab("map");
              }}
            >
              <aside
                className="drawer wide"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="drawer-heading">
                  <h2>数据源与覆盖</h2>
                  <button
                    aria-label="关闭数据源"
                    onClick={() => {
                      setSourcesOpen(false);
                      setTab("map");
                    }}
                  >
                    <X />
                  </button>
                </div>
                <p className="muted">{status?.crawler_root}</p>
                <div className="alert">
                  导入成功不代表实时采集正常。下表状态来自快照，不会自动刷新。
                </div>
                {work?.topics.map((t) => (
                  <div key={t.id}>
                    <h3>{t.name}</h3>
                    <div className="coverage-list">
                      {t.dimensions.map((d) => (
                        <div key={d.id}>
                          <span>{d.name}</span>
                          <small>
                            {d.status} · {d.count} 条
                          </small>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
                <h3>爬虫项目接入清单</h3>
                <p>
                  归并后记录按项目分别计数，同一新闻可能被多个爬虫采集，项目数量不可直接相加。原始抽取值保留归档，未自动当作可信指标。
                </p>
                {work?.projects?.map((p) => (
                  <div className="source-row" key={p.id}>
                    <strong>
                      {(
                        {
                          "xinhua-monitor": "新华网中文国际动态",
                          "ua-front-monitor": "俄乌军事战线",
                          "ukraine-aid-monitor": "乌克兰后援",
                          "russia-domestic-monitor": "俄罗斯内政",
                          "eu-domestic-monitor": "欧盟内政",
                          "ru-diplomacy-monitor": "俄乌外交",
                          "opinion-monitor": "美国民意",
                          "israel-monitor": "以色列动向",
                          "mideast-monitor": "中东各国",
                          "iran-domestic-monitor": "伊朗内政",
                          "diplomacy-monitor": "美伊外交",
                          "iran-escalation-monitor": "伊朗战备",
                          "naval-monitor": "海军动态",
                          "readiness-monitor": "战备表态",
                          "defense-spend-monitor": "军费与补给",
                          "finance-front-monitor": "金融战线",
                          "market-context-monitor": "市场补充",
                          "asset-transmission-monitor": "资产传导跟踪",
                        } as Record<string, string>
                      )[p.id] || "新增数据项目"}
                    </strong>
                    <span>
                      {p.state === "connected" ? "已接入" : "格式待适配"}
                    </span>
                    <small>
                      {p.records} 条记录 · 主题候选 {p.topic_records} 条 ·
                      失败来源 {p.failed_sources}/{p.sources} · 缺发布时间{" "}
                      {p.missing_published} · 缺采集时间 {p.missing_fetched}
                    </small>
                    <details>
                      <summary>
                        日报与采集核验文件（{p.documents.length}）
                      </summary>
                      {p.documents.map((d) => (
                        <a
                          className="reference"
                          key={d.archive + d.name}
                          href={"/api/archive/" + d.archive}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {d.name.startsWith("LATEST")
                            ? "爬虫研究日报"
                            : d.name === "ALL-observations.jsonl"
                              ? "历史指标观测档案"
                              : "采集与过滤核验文件"}{" "}
                          · {d.name}
                        </a>
                      ))}
                    </details>
                  </div>
                ))}
                <h3>来源状态</h3>
                {work?.sources.map((s) => (
                  <div className="source-row" key={s.id}>
                    <strong>{s.name}</strong>
                    <span
                      className={s.state.includes("fail") ? "warning-text" : ""}
                    >
                      {sourceStates[s.state] || "状态待核对"}
                    </span>
                    <small>
                      {s.count} 条 · 源内最新 {dateText(s.latest)} · 状态记录{" "}
                      {dateText(s.checked_at)}
                    </small>
                  </div>
                ))}
                <h3>模型连接</h3>
                <button
                  disabled={checkingModel}
                  onClick={async () => {
                    setCheckingModel(true);
                    setModelCheck("");
                    try {
                      const r = await api<{
                        ok: boolean;
                        message: string;
                        model?: string;
                      }>("/model/check", {});
                      setModelCheck(
                        r.message + (r.model ? " · " + r.model : ""),
                      );
                      await refreshStatus();
                    } catch {
                      setModelCheck("连接检查失败，请查看后端服务");
                    } finally {
                      setCheckingModel(false);
                    }
                  }}
                >
                  {checkingModel ? "正在检查…" : "测试模型连接"}
                </button>
                {modelCheck && <p role="status">{modelCheck}</p>}
                <p>
                  {status?.model.configured
                    ? `已配置 ${status.model.model}`
                    : "尚未配置。请在 .env 填写模型地址与密钥；保存后直接测试连接。服务不提供模型列表时再填写模型名称。"}
                </p>
                <p className="muted">
                  凭据只由后端读取，页面不接收或保存密钥。
                </p>
              </aside>
            </div>
          )}
          {chatOpen && (
            <aside className="chat-panel">
              <div className="drawer-heading">
                <div>
                  <h2>
                    <MessageSquare size={18} />
                    研究助手
                  </h2>
                  <small>
                    {names[topic]} · 快照 {sid.slice(0, 8) || "未选择"}
                  </small>
                </div>
                <button
                  aria-label="关闭研究助手"
                  onClick={() => {
                    setChatOpen(false);
                    setTab("map");
                  }}
                >
                  <X />
                </button>
              </div>
              <div className="chat-body">
                <div className="chat-intro">
                  基于已接入数据检索与分析。数值由程序计算，解释附来源。“本周”以所选快照导入日期为基准。
                </div>
                <div className="presets">
                  {[
                    "本周哪些指标发生变化？",
                    "当前判断有哪些反证？",
                    "还缺什么数据？",
                  ].map((q) => (
                    <button
                      disabled={asking || !sid}
                      key={q}
                      onClick={() => {
                        setQuestion(q);
                        ask(q);
                      }}
                    >
                      {q}
                    </button>
                  ))}
                </div>
                <select
                  aria-label="回放问答"
                  value={replay ? answer?.id || "" : ""}
                  onChange={(e) => {
                    const a = history.find((h) => h.id === e.target.value);
                    if (a) {
                      setAnswer(a);
                      setReplay(true);
                    }
                  }}
                >
                  <option value="">查看当前快照、主题与维度的问答档案</option>
                  {history.map((h) => (
                    <option key={h.id} value={h.id}>
                      {dateText(h.created_at)} {h.query.slice(0, 22)}
                    </option>
                  ))}
                </select>
                {asking && (
                  <div className="empty">
                    <RefreshCw className="spin" />
                    检索、计算及分析中…
                  </div>
                )}
                {answer && (
                  <div className="answer">
                    <div className="question-bubble">{answer.query}</div>
                    <div className="answer-mode">
                      {replay ? "历史回放 · " : ""}
                      {answer.mode}
                      {replay && (
                        <small>
                          原回答范围：{names[answer.topic || "all"]} ·{" "}
                          {answer.filters?.start || "不限起始日期"} 至{" "}
                          {answer.filters?.end || "不限结束日期"}
                        </small>
                      )}
                      <small>
                        {dateText(answer.created_at)} ·{" "}
                        {answer.snapshot_id.slice(0, 8)} · 数据截至{" "}
                        {dateText(answer.as_of)}
                      </small>
                    </div>
                    <p>{answer.message}</p>
                    {answer.facts.map((f, i) => (
                      <div className="fact" key={i}>
                        <label>{f.kind}</label>
                        <p>{f.text}</p>
                        {f.evidence_ids.map((id) => (
                          <button
                            className="citation"
                            onClick={() => openEvidence(id)}
                            key={id}
                          >
                            查看依据
                          </button>
                        ))}
                      </div>
                    ))}
                    {answer.inferences.map((f, i) => (
                      <div className="fact inference" key={i}>
                        <label>{f.kind}</label>
                        <p>{f.text}</p>
                        {f.evidence_ids.map((id) => (
                          <button
                            className="citation"
                            onClick={() => openEvidence(id)}
                            key={id}
                          >
                            查看引用
                          </button>
                        ))}
                      </div>
                    ))}
                    <h3>检索证据</h3>
                    {answer.citations.map((r, i) => (
                      <button
                        className="reference"
                        key={r.id}
                        onClick={() => openEvidence(r.id)}
                      >
                        <span>{i + 1}.</span>
                        {r.title}
                      </button>
                    ))}
                    <h3>信息缺口</h3>
                    <ul>
                      {answer.gaps.map((g, i) => (
                        <li key={i}>{g}</li>
                      ))}
                    </ul>
                    <div className="steps">{answer.steps.join(" → ")}</div>
                  </div>
                )}
              </div>
              <form
                className="chat-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  ask();
                }}
              >
                <textarea
                  aria-label="研究问题"
                  placeholder="例如：采购支出变化能说明什么？"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  maxLength={3000}
                />
                <button
                  className="primary"
                  disabled={asking || !sid || !question.trim()}
                  aria-label="发送问题"
                >
                  <Send size={17} />
                </button>
              </form>
            </aside>
          )}
        </main>
        <aside className="insight-panel">
          <div className="insight-heading">
            <h2>{names[topic]} · 研判</h2>
            <span className="pill">研究工作台</span>
          </div>
          <div className="insight-scroll">
            {loading && (
              <div className="insight-loading" role="status">
                正在加载当前范围，请稍候…
              </div>
            )}
            <div style={{ visibility: loading ? "hidden" : "visible" }}>
              <div className="insight-summary">
                <span className="eyebrow">当前快照概览</span>
                <h2>综合态势观察</h2>
                <p>
                  综合军事行动、外交谈判、政策与经济动态，跟踪局势变化及资产影响。
                </p>
                <div className="situation-summary">
                  {[
                    ["escalation", "升级相关"],
                    ["sustain", "持续相关"],
                    ["easing", "缓和相关"],
                  ].map(([key, label]) => (
                    <button
                      key={key}
                      onClick={() => {
                        if (page === "briefs") setPage("all");
                        setTab("evidence");
                        setSearch(label);
                        setChatOpen(false);
                        setSourcesOpen(false);
                      }}
                    >
                      <span>{label}</span>
                      <b>
                        {work?.records.filter((r) => r.situation_signal === key)
                          .length || 0}
                      </b>
                    </button>
                  ))}
                </div>
                <small>报道线索数量，不代表整体冲突强度</small>
                <div className="insight-stats">
                  <div>
                    <b>{work?.records.length || 0}</b>
                    <small>范围内记录</small>
                  </div>
                  <div>
                    <b>
                      {dimensionCount}
                      <em>/13</em>
                    </b>
                    <small>全框架有候选维度</small>
                  </div>
                </div>
                <small>
                  快照 {sid.slice(0, 8) || "尚未导入"} ·{" "}
                  {dateText(work?.snapshot.created_at)}
                </small>
              </div>
              <section className="insight-section">
                <h3>证据边界</h3>
                <div className="insight-note">
                  <b>事实与声明分开</b>
                  <p>官方表态证明该方作出声明，其内容仍需交叉核验。</p>
                </div>
                <div className="insight-note amber">
                  <b>判断与触发条件</b>
                  <p>
                    {topic === "ukraine"
                      ? "关注军事行动、援助实际交付与谈判执行。"
                      : "关注军事行动、能源运输、政策变化与外交进展。"}
                    持续跟踪是否出现升级、维持或缓和的证据。
                  </p>
                </div>
              </section>
              <section className="insight-section">
                <div className="insight-section-title">
                  <h3>大类资产关联</h3>
                  <button
                    onClick={() => {
                      setTab("assets");
                      setChatOpen(false);
                      setSourcesOpen(false);
                      if (page === "briefs") setPage("all");
                    }}
                  >
                    查看矩阵 <ChevronRight size={13} />
                  </button>
                </div>
                <div className="mini-assets">
                  {work?.assets.map((a) => (
                    <button
                      key={a.id}
                      onClick={() => {
                        setAsset(a);
                        setSelected(null);
                      }}
                    >
                      <b>{a.name}</b>
                      <span>{a.direction}</span>
                    </button>
                  ))}
                </div>
                <small>条件传导框架 · 市场观测见资产详情</small>
              </section>
              <section className="insight-section">
                <h3>最新证据线索</h3>
                {work?.records.slice(0, 5).map((r) => (
                  <button
                    key={r.id}
                    className="insight-news"
                    onClick={() => {
                      openEvidence(r.id);
                      setAsset(null);
                    }}
                  >
                    <small>{r.source_name}</small>
                    <small>发布时间：{dateText(r.published_at)}</small>
                    <small>采集时间：{dateText(r.fetched_at)}</small>
                    <span>{r.title}</span>
                  </button>
                ))}
                {!work?.records.length && (
                  <p className="muted">当前筛选没有可用记录</p>
                )}
              </section>
              <div className="insight-section model-state">
                {status?.translation?.running
                  ? `中文译文准备中：${status.translation.ready}/${status.translation.total}`
                  : status?.model.mode || "连接服务中"}{" "}
                · 公开来源
              </div>
            </div>
          </div>
        </aside>
      </div>
      {asset && (
        <div
          className="drawer-backdrop asset-backdrop"
          onClick={() => setAsset(null)}
        >
          <aside className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-heading">
              <h2>{asset.name} · 条件传导</h2>
              <button aria-label="关闭资产详情" onClick={() => setAsset(null)}>
                <X />
              </button>
            </div>
            <span className="pill">{asset.direction}</span>
            <h3>当前研究观点</h3>
            <p>{asset.view}</p>
            <small>{asset.assessment_kind}</small>
            {asset.topic_paths?.map((p) => (
              <section key={p.topic}>
                <h3>
                  {names[p.topic]} · {p.channel}
                </h3>
                <span className="pill">{p.status}</span>
                <p>{p.condition}</p>
                <h4>反证与失效条件</h4>
                <p>{p.counter}</p>
                <h4>下一观察项</h4>
                <p>{p.watch}</p>
                {p.evidence_ids.map((id, i) => (
                  <button
                    className="reference"
                    key={id}
                    onClick={() => openEvidence(id)}
                  >
                    查看该主题线索 {i + 1}
                  </button>
                ))}
              </section>
            ))}
            <h3>优先补充数据</h3>
            <p>{asset.missing_data}</p>
            <h3>传导机制</h3>
            <p>{asset.mechanism}</p>
            <h3>成立条件</h3>
            <p>{asset.condition}</p>
            <h3>反证与失效条件</h3>
            <p>{asset.counter}</p>
            <h3>下一观察项</h3>
            <p>{asset.watch}</p>
            <h3>相较上次判断</h3>
            <p>{asset.change}</p>
            <h3>市场观测与比较日期</h3>
            <p>{asset.market}</p>
            <h3>待核验线索</h3>
            <p className="muted">{asset.evidence_role}</p>
            {asset.evidence_ids.map((id) => (
              <button
                className="reference"
                key={id}
                onClick={() => openEvidence(id)}
              >
                <BookOpen size={15} />
                {work?.records.find((r) => r.id === id)?.title || id}
              </button>
            ))}
            <button
              className="primary"
              onClick={() => {
                setQuestion(`分析${asset.name}的关联证据、条件及反证`);
                setAsset(null);
                setChatOpen(true);
                setSourcesOpen(false);
                setTab("chat");
              }}
            >
              向研究助手追问
            </button>
          </aside>
        </div>
      )}
      {selected && (
        <div
          className="drawer-backdrop evidence-backdrop"
          onClick={() => setSelected(null)}
        >
          <aside className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-heading">
              <h2>
                {sourceMetric?.source_ids.includes(selected.id)
                  ? "指标含义与来源"
                  : "证据详情"}
              </h2>
              <button
                aria-label="关闭证据详情"
                onClick={() => setSelected(null)}
              >
                <X />
              </button>
            </div>
            {sourceMetric?.source_ids.includes(selected.id) && (
              <MetricGuide metric={sourceMetric} />
            )}
            <div className="detail-tags">
              <span className="pill">{tierName(selected.tier)}</span>
              <span className="pill">{selected.event_category_label}</span>
              <span className="pill">{selected.signal_label}</span>
              <span className="pill subdued">{selected.verification}</span>
            </div>
            <h2 className="record-title">{selected.title}</h2>
            {!selected.supplemental && (
              <ReviewPanel
                key={sid + selected.id}
                record={selected}
                sid={sid}
                onSaved={(r) => {
                  setSelected(r);
                  workspaceCache.current.clear();
                  setWork((w) =>
                    w
                      ? {
                          ...w,
                          records: w.records.map((x) =>
                            x.id === r.id ? r : x,
                          ),
                        }
                      : w,
                  );
                }}
              />
            )}
            {selected.supplemental && (
              <div className="alert">
                {selected.source_id === "huatai-middle-east-brief"
                  ? `高可信研究资料 · 简报日期 ${selected.report_as_of || "未注明"}。其中转述、预测和市场数字仍需回源核验，不计作独立事件确认。`
                  : `补充研究资料 · 报告自述截至 ${selected.report_as_of || "未注明"}。生成与发布时间未提供；观点及原引用尚未独立核验，不计作新增事件。`}
              </div>
            )}
            <dl>
              <dt>来源</dt>
              <dd>{selected.source_name}</dd>
              <dt>发布时间</dt>
              <dd>{dateText(selected.published_at)}</dd>
              <dt>采集时间</dt>
              <dd>{dateText(selected.fetched_at)}</dd>
              <dt>首次发现</dt>
              <dd>{selected.first_seen || "未提供"}</dd>
              <dt>事件时间</dt>
              <dd>{dateText(selected.event_at)}</dd>
              <dt>覆盖范围</dt>
              <dd>{selected.scope}</dd>
              <dt>分类依据</dt>
              <dd>{selected.classification}</dd>
            </dl>
            <h3>链接检查</h3>
            <p>
              {selected.link_health?.status || "尚未检查"}
              {selected.link_health?.http_status
                ? ` · HTTP ${selected.link_health.http_status}`
                : ""}
            </p>
            <small>
              {dateText(selected.link_health?.checked_at)} ·
              可访问不等于事实已核验
            </small>
            <h3>中文摘要</h3>
            <button
              disabled={translating || !status?.model.configured}
              onClick={async () => {
                setTranslating(true);
                try {
                  const r = await api<{ ok: boolean; message: string }>(
                    `/evidence/${selected.id}/translate?snapshot_id=${sid}`,
                    {},
                  );
                  setNotice(r.message);
                  if (r.ok) await openEvidence(selected.id, sourceMetric);
                } catch {
                  setError("翻译请求失败，原文保留");
                } finally {
                  setTranslating(false);
                }
              }}
            >
              {translating ? "正在翻译…" : "使用已配置模型重新翻译"}
            </button>
            <small className="muted">{selected.translation_note}</small>
            <blockquote>
              {selected.text || "该记录未提供原文片段，请打开原始来源核验。"}
            </blockquote>
            <details>
              <summary>对照原文（保留原始语言）</summary>
              <h3>{selected.original_title}</h3>
              <blockquote>{selected.original_text || "未提供原文"}</blockquote>
            </details>
            {selected.points.length > 0 && (
              <>
                <h3>关联地区</h3>
                {selected.points.map((p, i) => (
                  <p key={i}>
                    {p.label} ·{" "}
                    {p.precision === "explicit"
                      ? "报道中的具体地点"
                      : "报道关联地区"}
                  </p>
                ))}
              </>
            )}
            <div className="alert">
              地图展示新闻关联地区，具体事件以原始报道为准。
            </div>
            {selected.url && (
              <a
                className="button primary"
                href={selected.url}
                target="_blank"
                rel="noreferrer"
              >
                查看原始报道 <ArrowUpRight size={15} />
              </a>
            )}
            <h3>快照原始文件</h3>
            {selected.raw_refs.map((f) => (
              <a className="reference" key={f} href={"/api/archive/" + f}>
                <Download size={15} />
                {f}
              </a>
            ))}
            <small className="muted">记录ID：{selected.id}</small>
          </aside>
        </div>
      )}
    </div>
  );
}
