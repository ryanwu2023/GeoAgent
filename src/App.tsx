import {SessionAccount} from "./SessionGate";
import {sessionFetch as fetch} from "./sessionFetch";
import { lazy, Suspense, useDeferredValue, useEffect, useRef, useState } from "react";
import IntegrationSettings from "./IntegrationSettings";
import McpWorkbench from "./McpWorkbench";
import BriefTemplateEditor from "./BriefTemplateEditor";
import AnalysisProcess from "./QaTrace";
import projectLabels from "../config/project-labels.json";
import { panelEntryScope } from "./hostViewContext";
import { useEmbedIdentity } from "./useEmbedIdentity";
import { ResearchDivider, ResearchDockHeading } from "./ResearchDockControls";
import { DEFAULT_RESEARCH_WIDTH } from "./researchLayout";
import type { CSSProperties } from "react";
import {
  Activity,
  AlertTriangle,
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
import { api, apiMeetings, apiPut, apiStream, dateText, num } from "./api";
import type {
  AnalysisRound,
  Answer,
  Asset,
  Brief,
  BriefTemplate,
  Evidence,
  EvidencePage,
  Metric,
  ExternalMcpRegistry,
  MapEvidence,
  Meeting,
  PageSummary,
  Status,
  Workspace,
} from "./types";
import ReviewPanel from "./ReviewPanel";
import MetricChart from "./MetricChart";
import {canImportNews, reconcileNewsServer, titleTranslationLabel} from "./source_status";
import MetricGuide from "./MetricGuide";
import CollectorMonitor from "./CollectorMonitor";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import BriefViewer from "./BriefViewer";
import MeetingCalendar from "./MeetingCalendar";
import { useHostViewContext } from "./useHostViewContext";
import { metricCategory } from "./metric_category";
import { clearRunId, mergeAnalysisTrace, normalizeAnswer, normalizeAnswerMarkdown, qaRunStorageKey, saveRunId, storedRunId } from "./qa_ui";

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

const movementClass = (m: Metric) =>
  m.delta == null || m.delta === 0 ? "flat" : m.delta > 0 ? "up" : "down";
const modelSummaryTabs = new Set(["metrics", "scenarios", "assets"]);

export default function App() {
  const [sourceMetric, setSourceMetric] = useState<Metric | null>(null);
  const [translating, setTranslating] = useState(false);
  const [mcpRegistry, setMcpRegistry] = useState<ExternalMcpRegistry>({version:1,servers:[]}),
    [mcpServerId, setMcpServerId] = useState(""),
    [mcpMessage, setMcpMessage] = useState(""),
    [mcpBusy, setMcpBusy] = useState(false),
    [mcpQuery, setMcpQuery] = useState("美伊局势 最新进展"),
    [mcpTopic, setMcpTopic] = useState<"usiran" | "ukraine">("usiran"),
    [mcpDimension, setMcpDimension] = useState("regional"),
    [mcpDateFrom, setMcpDateFrom] = useState(""),
    [mcpDateTo, setMcpDateTo] = useState(""),
    [mcpTopK, setMcpTopK] = useState(20);
  const [metricSearch, setMetricSearch] = useState(""),
    [metricGroup, setMetricGroup] = useState("全部");
  const [status, setStatus] = useState<Status | null>(null),
    [sid, setSid] = useState(""),
    [workingSid, setWorkingSid] = useState(""),
    [page, setPage] = useState("usiran"),
    [dimension, setDimension] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [work, setWork] = useState<Workspace | null>(null),
    [mapRecords, setMapRecords] = useState<MapEvidence[]>([]),
    [mapMeetings, setMapMeetings] = useState<Meeting[]>([]),
    [mapLoading, setMapLoading] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [loading, setLoading] = useState(false),
    [importing, setImporting] = useState(false);
  const [pageSummary, setPageSummary] = useState<PageSummary | null>(null),
    [pageSummaryLoading, setPageSummaryLoading] = useState(false),
    [pageSummaryRefresh, setPageSummaryRefresh] = useState(0);
  const [selected, setSelected] = useState<Evidence | null>(null),
    [selectedSnapshotId, setSelectedSnapshotId] = useState(""),
    [openingEvidenceId, setOpeningEvidenceId] = useState(""),
    [evidenceOpenError, setEvidenceOpenError] = useState(""),
    [evidenceRequest, setEvidenceRequest] = useState<{id:string;snapshotId:string}|null>(null),
    [asset, setAsset] = useState<Asset | null>(null),
    [sourcesOpen, setSourcesOpen] = useState(false),
    [search, setSearch] = useState(""),
    [tab, setTab] = useState("map");
  const [evidenceRows, setEvidenceRows] = useState<Evidence[]>([]),
    [evidenceTotal, setEvidenceTotal] = useState(0),
    [evidenceMore, setEvidenceMore] = useState(false),
    [evidenceLoading, setEvidenceLoading] = useState(false);
  const [chatOpen, setChatOpen] = useState(true),
    [question, setQuestion] = useState(""),
    [answer, setAnswer] = useState<Answer | null>(null),
    [analysisTrace, setAnalysisTrace] = useState<AnalysisRound[]>([]),
    [asking, setAsking] = useState(false),
    [reportDownloading, setReportDownloading] = useState(false),
    [replay, setReplay] = useState(false),
    [history, setHistory] = useState<Answer[]>([]);
  const [embedWorkspaceScope, setEmbedWorkspaceScope] = useState("");
  const openResearchAssistant = () => {
    setChatOpen(true);
    // Right-hand tabs only change visibility. Once captured, the workspace
    // stays stable even when a snapshot changes while the insight tab is open.
    const scope = panelEntryScope(embedWorkspaceScope, !!embedWorkspaceScope, workingSid);
    if (!scope) return;
    setEmbedWorkspaceScope(scope);
  };
  useEffect(() => {
    // Start the default dock after the first working snapshot arrives, never
    // from an empty/transient snapshot and never recapture it on tab changes.
    setEmbedWorkspaceScope(current => panelEntryScope(current, !!current, workingSid));
  }, [workingSid]);
  const [briefs, setBriefs] = useState<Brief[]>([]),
    [brief, setBrief] = useState<Brief | null>(null),
    [generating, setGenerating] = useState(false),
    [compare, setCompare] = useState(""),
    [diff, setDiff] = useState("");
  const [briefTemplates,setBriefTemplates]=useState<BriefTemplate[]>([]),
    [briefTemplateId,setBriefTemplateId]=useState(""),
    [briefTemplateOpen,setBriefTemplateOpen]=useState(false);
  const [resetKey, setResetKey] = useState(0);
  const queryVersion = useRef(0),
    selectionVersion = useRef(0),
    evidenceVersion = useRef(0),
    detailRequestVersion = useRef(0),
    pageSummaryVersion = useRef(0),
    workingSidRef = useRef("");
  const lastTopic = useRef("usiran");
  const [settingsOpen,setSettingsOpen] = useState(false);
  const [settingsSection,setSettingsSection] = useState<"embed"|"model"|"agent"|"mcp">("embed");
  const [mcpWorkbenchOpen,setMcpWorkbenchOpen] = useState(false);
  const [mcpWorkbenchView,setMcpWorkbenchView] = useState<"local"|"external">("local");
  const [connection,setConnection] = useState({origin:"http://127.0.0.1:9172",agent_id:"geo-research-assistant",product_user_id:"geoagent-local",embed_configured:false});
  const [connectionLoaded,setConnectionLoaded] = useState(false);
  const [connectionVersion,setConnectionVersion] = useState(0);
  const [chatExpanded,setChatExpanded] = useState(false);
  const [researchWidth, setResearchWidth] = useState(DEFAULT_RESEARCH_WIDTH);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const pichatOrigin = connection.origin;
  const pichatAgentId = connection.agent_id;
  const pichatEmbedConfigured = connection.embed_configured;
  const reloadConnection = async () => {
    try {
      const response = await fetch("/api/integrations/public",{cache:"no-store"});
      if(!response.ok)throw new Error("连接配置读取失败");
      setConnection(await response.json());
      setConnectionLoaded(true);
      setConnectionVersion(value=>value+1);
    } catch(error){setPichatError(error instanceof Error?error.message:"连接配置读取失败")}
  };
  useEffect(()=>{void reloadConnection()},[]);
  const acceptBriefTemplates=(rows:BriefTemplate[])=>{
    setBriefTemplates(rows);
    setBriefTemplateId(current=>rows.some(row=>row.id===current)?current:(rows.find(row=>row.is_default)?.id||rows[0]?.id||""));
  };
  useEffect(()=>{api<BriefTemplate[]>("/brief-templates").then(acceptBriefTemplates).catch(()=>{})},[]);
  if (page !== "briefs") lastTopic.current = page;
  const topic = lastTopic.current;
  const workspaceCache = useRef(
    new Map<string, { time: number; data: Workspace }>(),
  );
  const mapCache = useRef(new Map<string, MapEvidence[]>());
  const meetingMapCache = useRef(new Map<string, Meeting[]>());
  const mapTranslationScope = useRef("");
  mapTranslationScope.current = JSON.stringify([sid,topic,dimension,start,end]);
  const pichatFrameRef = useRef<HTMLIFrameElement | null>(null);
  const {ready:pichatReady,loading:pichatAuthRequired,error:pichatError,notice:pichatNotice,
    frameLoaded:pichatFrameLoaded,setError:setPichatError} = useEmbedIdentity(pichatFrameRef,
      pichatOrigin,pichatAgentId,connection.product_user_id,connectionVersion,
      connectionLoaded && pichatEmbedConfigured && !!embedWorkspaceScope);
  const viewBridge = useHostViewContext(pichatFrameRef,pichatOrigin,pichatAgentId,connectionVersion,
    connectionLoaded && pichatEmbedConfigured && !!embedWorkspaceScope, {version:'geoagent-view/v1',snapshot_id:workingSid,topic,dimension,start,end}, chatOpen);
  const pichatUrl = (() => {
    const url = new URL(
      `/work-embed/${encodeURIComponent(pichatAgentId)}`,
      pichatOrigin,
    );
    url.searchParams.set("org", "default");
    url.searchParams.set("hideNav", "1");
    // The host workbench is a dark surface.  PiChat's work-embed supports an
    // explicit theme query for cross-origin embeds; without it the iframe
    // falls back to the user's standalone light-theme preference and visually
    // breaks the GeoAgent panel.
    url.searchParams.set("theme", "dark");
    url.searchParams.set("embedParentOrigin", window.location.origin);
    url.searchParams.set("parentOrigin", window.location.origin);
    url.searchParams.set(
      "workspaceScope",
      // Preserve legacy history on entry, then freeze until explicit re-entry.
      embedWorkspaceScope,
    );
    return url.toString();
  })();
  async function refreshStatus() {
    const s = await api<Status>(sid ? `/status?snapshot_id=${encodeURIComponent(sid)}` : "/status");
    setStatus(s);
    return s;
  }
  async function openBrief(item: Brief) {
    setDiff("");
    setCompare("");
    if (item.markdown) {
      setBrief(item);
      return;
    }
    try {
      setBrief(await api<Brief>(`/briefs/${item.id}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "简报正文加载失败");
    }
  }
  useEffect(() => {
    workingSidRef.current = workingSid;
  }, [workingSid]);
  useEffect(() => {
    if (!status?.translation?.running || status.translation.snapshot_id !== sid || !sid) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const s = await api<Status>(`/status?snapshot_id=${encodeURIComponent(sid)}`);
        const w = await api<Workspace>(
          "/workspace?" +
            new URLSearchParams({
              snapshot_id: sid,
              topic,
              dimension,
              start,
              end,
              include_records: "false",
            }),
        );
        if (!cancelled) {
          setStatus(s);
          const key = new URLSearchParams({ snapshot_id: sid, topic, dimension, start, end, include_records: "false" }).toString();
          workspaceCache.current.set(key, { time: Date.now(), data: w });
          setWork(w);
        }
      } catch {}
    }, 60000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [status?.translation?.running, status?.translation?.snapshot_id, sid, topic, dimension, start, end]);
  useEffect(() => {
    if (status?.crawler?.state !== "running") return;
    const timer = setInterval(() => {
      refreshStatus().then((next) => {
        if (next.crawler?.state === "completed") {
          const nextId = next.working_snapshot_id || next.snapshots[0]?.id || "";
          if (nextId) {
            workspaceCache.current.clear();
            mapCache.current.clear();
            meetingMapCache.current.clear();
            setWorkingSid(nextId);
            setSid(nextId);
            setNotice(`后台采集与导入已完成，已切换至最新工作快照 ${nextId.slice(0, 8)}`);
          } else {
            setNotice("后台采集已完成，但尚未生成可用快照");
          }
        }
      }).catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [status?.crawler?.state]);
  useEffect(() => {
    if (!sourcesOpen || mcpWorkbenchOpen) return;
    let cancelled = false;
    api<ExternalMcpRegistry>("/integrations/external-mcp").then((registry) => {
      if (cancelled) return;
      setMcpRegistry(registry);
      setMcpServerId(selected => reconcileNewsServer(selected, registry.servers));
    }).catch((e) => { if (!cancelled) setMcpMessage(e.message); });
    return () => {cancelled = true;};
  }, [sourcesOpen, mcpWorkbenchOpen]);
  useEffect(() => {
    refreshStatus()
      .then((s) => {
        setWorkingSid(s.working_snapshot_id || "");
        const preferred = s.working_snapshot_id && s.snapshots.some((item) => item.id === s.working_snapshot_id)
          ? s.working_snapshot_id
          : s.snapshots[0]?.id;
        if (preferred) setSid(preferred);
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    let cancelled = false;
    function pollStatus() {
      api<Status>(sid ? `/status?snapshot_id=${encodeURIComponent(sid)}` : "/status")
        .then((s) => {
          if (!cancelled) {
            setStatus(s);
            const nextId = s.working_snapshot_id || "";
            if (nextId && nextId !== workingSidRef.current) {
              workspaceCache.current.clear();
              mapCache.current.clear();
              meetingMapCache.current.clear();
              workingSidRef.current = nextId;
              setWorkingSid(nextId);
              setSid(nextId);
              setNotice(`检测到新工作快照，已重新加载 ${nextId.slice(0, 8)}…`);
            } else {
              setWorkingSid(nextId);
            }
          }
        })
        .catch(() => {});
    }
    if (sid) pollStatus();
    const timer = window.setInterval(pollStatus, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sid]);
  useEffect(() => {
    closeDetails();
    return () => { detailRequestVersion.current++; };
  }, [sid, workingSid, topic, dimension, start, end]);
  useEffect(() => {
    if (!sid) return;
    const version = ++queryVersion.current;
    const key = new URLSearchParams({
      snapshot_id: sid,
      topic,
      dimension,
      start,
      end,
      include_records: "false",
    }).toString();
    const cached = workspaceCache.current.get(key);
    setError("");
    // Snapshots are immutable. Reuse the structured workspace while it is
    // current; the status poll clears this cache as soon as a new working
    // snapshot is published. Page entry still invokes /page-summary, whose
    // cache key covers the snapshot, filters, rules and review revision.
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
    if (!sid || tab !== "map") {
      setMapLoading(false);
      return;
    }
    const key = new URLSearchParams({ snapshot_id: sid, topic, dimension, start, end }).toString();
    const cached = mapCache.current.get(key);
    const cachedMeetings = meetingMapCache.current.get(key);
    if (cached && cachedMeetings) {
      setMapRecords(cached);
      setMapMeetings(cachedMeetings);
      setMapLoading(false);
      return;
    }
    // Invalidate every callback when the view changes, the map is hidden, or
    // the component unmounts. A slow old request must never replace this scope.
    let cancelled = false;
    setMapLoading(true);
    setMapRecords([]);
    Promise.all([
      api<MapEvidence[]>("/map-records?" + key),
      apiMeetings({ snapshot_id: sid, topic, include_inactive: false, limit: 500 }),
    ])
      .then(([records, meetingPayload]) => {
        if (cancelled) return;
        mapCache.current.set(key, records);
        meetingMapCache.current.set(key, meetingPayload.records);
        setMapRecords(records);
        setMapMeetings(meetingPayload.records);
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setMapLoading(false);
      });
    return () => { cancelled = true; };
  }, [sid, topic, dimension, start, end, tab]);
  useEffect(() => {
    const version = ++pageSummaryVersion.current;
    if (!modelSummaryTabs.has(tab)) {
      setPageSummary(null);
      setPageSummaryLoading(false);
      return;
    }
    if (!sid || sid !== workingSid || work?.snapshot.id !== sid) {
      setPageSummary(null);
      setPageSummaryLoading(false);
      return;
    }
    setPageSummary(null);
    setPageSummaryLoading(true);
    api<PageSummary>("/page-summary", {
      snapshot_id: sid,
      page: tab,
      topic,
      dimension,
      start,
      end,
    })
      .then((result) => {
        if (pageSummaryVersion.current === version) setPageSummary(result);
      })
      .catch((e) => {
        if (pageSummaryVersion.current === version) {
          setPageSummary({
            cache_key: "",
            snapshot_id: sid,
            page: tab as "metrics" | "scenarios" | "assets",
            topic,
            filters: { dimension, start, end },
            rule_version: status?.rule_version || "",
            created_at: new Date().toISOString(),
            status: "failed",
            mode: "模型总结失败",
            text: null,
            evidence_ids: [],
            cached: false,
            message: (e as Error).message,
          });
        }
      })
      .finally(() => {
        if (pageSummaryVersion.current === version) setPageSummaryLoading(false);
      });
  }, [tab, sid, workingSid, topic, dimension, start, end, work?.snapshot.id, status?.rule_version, pageSummaryRefresh]);
  const deferredSearch = useDeferredValue(search.trim());
  async function loadEvidence(offset = 0) {
    if (!sid) return;
    const version = ++evidenceVersion.current;
    setEvidenceLoading(true);
    try {
      const result = await api<EvidencePage>(
        "/evidence?" +
          new URLSearchParams({
            snapshot_id: sid,
            topic,
            dimension,
            start,
            end,
            q: deferredSearch,
            offset: String(offset),
            limit: "30",
          }),
      );
      if (version === evidenceVersion.current) {
        setEvidenceRows((current) => offset ? [...current, ...result.records] : result.records);
        setEvidenceTotal(result.total);
        setEvidenceMore(result.has_more);
      }
    } catch (e) {
      if (version === evidenceVersion.current) setError((e as Error).message);
    } finally {
      if (version === evidenceVersion.current) setEvidenceLoading(false);
    }
  }
  useEffect(() => {
    setEvidenceRows([]);
    setEvidenceTotal(0);
    setEvidenceMore(false);
    if (sid) loadEvidence(0);
  }, [sid, topic, dimension, start, end, deferredSearch]);
  useEffect(() => {
    selectionVersion.current++;
    setAnswer(null);
    setReplay(false);
    setHistory([]);
    setBrief((prev) => (prev?.snapshot_id === sid ? prev : null));
    setDiff("");
    if (workingSid) {
      const v = selectionVersion.current;
      api<Answer[]>("/answers?" + new URLSearchParams({snapshot_id:workingSid,compact:"true"}))
        .then((h) => {
          if (v === selectionVersion.current)
            setHistory(
              h.map(normalizeAnswer).filter(
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
  }, [workingSid, topic, dimension]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeDetails();
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
        sync?: { projects: number; files: number; bytes: number };
      }>("/import", {});
      await refreshStatus();
      setSid(r.id);
      await apiPut("/workspace-selection", { snapshot_id: r.id });
      setWorkingSid(r.id);
      setNotice(
        `${r.created ? "已冻结新快照" : "数据未变化，使用已有快照"} · ${r.count} 条归并记录${r.sync?.files ? ` · 已同步 ${r.sync.projects} 个爬虫的 ${r.sync.files} 个新文件` : ""}${r.errors.length ? ` · ${r.errors.length} 条解析异常` : ""} · 本操作只导入已发布采集产物，不会启动采集器`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setImporting(false);
    }
  }
  async function selectSnapshot(nextId: string) {
    const previousId = sid;
    setSid(nextId);
    if (!nextId) return;
    try {
      await apiPut("/workspace-selection", { snapshot_id: nextId });
      setWorkingSid(nextId);
      setNotice(`已将 ${nextId.slice(0, 10)}… 设为工作快照`);
      window.setTimeout(() => setNotice(""), 4000);
    } catch (e) {
      setSid(previousId);
      setError((e as Error).message);
    }
  }
  async function fetchMcpNews() {
    if (!canImportNews(mcpServerId,mcpRegistry.servers)) return;
    setMcpBusy(true);
    setMcpMessage("");
    try {
      const result = await api<{id:string;created:boolean;fetched:number;count:number}>("/mcp/fetch", {
        server_id: mcpServerId,
        query: mcpQuery,
        topic: mcpTopic,
        dimension: mcpDimension,
        top_k: mcpTopK,
        date_from: mcpDateFrom,
        date_to: mcpDateTo,
      });
      await refreshStatus();
      await selectSnapshot(result.id);
      setMcpMessage(`已获取 ${result.fetched} 条 MCP 新闻并${result.created ? "生成新快照" : "复用已有快照"} · ${result.id.slice(0, 8)}`);
    } catch (e) {
      setMcpMessage((e as Error).message);
    } finally {
      setMcpBusy(false);
    }
  }
  function closeDetails() {
    detailRequestVersion.current++;
    setSelected(null);
    setSelectedSnapshotId("");
    setOpeningEvidenceId("");
    setEvidenceOpenError("");
    setEvidenceRequest(null);
    setAsset(null);
    setSourceMetric(null);
  }
  function openAsset(nextAsset: Asset) {
    closeDetails();
    setAsset(nextAsset);
  }
  async function openEvidence(id: string, metric: Metric | null = null, evidenceSnapshotId = sid) {
    if (!evidenceSnapshotId) return;
    const version = ++detailRequestVersion.current;
    setEvidenceRequest({id,snapshotId:evidenceSnapshotId});
    setOpeningEvidenceId(id);
    setEvidenceOpenError("");
    try {
      const r = await api<Evidence>(`/evidence/${encodeURIComponent(id)}?snapshot_id=${encodeURIComponent(evidenceSnapshotId)}`);
      if (version !== detailRequestVersion.current) return;
      setSelected(r);
      setSelectedSnapshotId(evidenceSnapshotId);
      setSourceMetric(metric);
      setAsset(null);
    } catch (e) {
      if (version === detailRequestVersion.current) {
        const message=`证据 ${id.slice(0,8)}… 无法打开：${(e as Error).message}`;
        setEvidenceOpenError(message);
        setError(message);
      }
    } finally {
      if (version === detailRequestVersion.current) setOpeningEvidenceId("");
    }
  }
  async function translateMapTitles(ids: string[]) {
    if (!sid || !status?.model.configured || !ids.length) return {};
    const requestScope = mapTranslationScope.current;
    const requestSid = sid;
    try {
      const result = await api<{ok:boolean;translated:number;failed:string[];titles:Record<string,string>;message:string}>(
        "/translations/titles",
        {snapshot_id:sid,record_ids:ids},
      );
      if (requestScope !== mapTranslationScope.current) return {};
      if (!result.ok && result.message) setNotice(result.message);
      if (Object.keys(result.titles).length) {
        const applyTitles = (records: MapEvidence[]) => records.map(record =>
          result.titles[record.id] ? {...record,title:result.titles[record.id],title_translation_state:"ready" as const} : record,
        );
        setMapRecords(current => applyTitles(current));
        setEvidenceRows(current => current.map(record =>
          result.titles[record.id] ? {...record,title:result.titles[record.id],title_translation_state:"ready" as const} : record,
        ));
        for (const [key,cached] of workspaceCache.current) {
          if (new URLSearchParams(key).get("snapshot_id") === requestSid)
            workspaceCache.current.set(key,{...cached,data:{...cached.data,records:applyTitles(cached.data.records)}});
        }
        for (const [key,cached] of mapCache.current) {
          if (new URLSearchParams(key).get("snapshot_id") === requestSid)
            mapCache.current.set(key, applyTitles(cached));
        }
      }
      return result.titles;
    } catch (e) {
      if (requestScope === mapTranslationScope.current) setNotice(`中文标题生成失败：${(e as Error).message}`);
      return {};
    }
  }
  async function ask(q = question) {
    if (!q.trim() || !workingSid) return;
    const submittedQuestion = q.trim();
    setQuestion("");
    const previous_answer_id = answer?.id || "";
    const version = selectionVersion.current;
    setAsking(true);
    setAnswer(null);
    setAnalysisTrace([]);
    setReplay(false);
    openResearchAssistant();
    setSourcesOpen(false);
    const runStorageKey=qaRunStorageKey(workingSid,topic,dimension,start,end,submittedQuestion);
    let activeRunId=storedRunId(window.localStorage,runStorageKey);
    const requestBody=()=>({
      snapshot_id: workingSid,
      question: submittedQuestion,
      topic,
      dimension,
      start,
      end,
      previous_answer_id,
      run_id: activeRunId,
      compact: true,
    });
    const stream=()=>apiStream<AnalysisRound,Answer>("/ask/stream", {...requestBody(),question: submittedQuestion}, item => {
      if (version !== selectionVersion.current) return;
      setAnalysisTrace(current => mergeAnalysisTrace(current,item));
    }, run=>{
      activeRunId=run.run_id;
      saveRunId(window.localStorage,runStorageKey,run.run_id);
    });
    try {
      let a:Answer;
      try {
        a=await stream();
      } catch (firstError) {
        if(!activeRunId)throw firstError;
        let state:{status:string}|null=null;
        for(let attempt=0;attempt<20;attempt++){
          await new Promise(resolve=>setTimeout(resolve,750));
          try{state=await api<{status:string}>(`/qa-runs/${activeRunId}`)}catch{state=null}
          if(state&&state.status!=="running")break;
        }
        if(!state||state.status==="running")throw new Error(`连接中断，分析运行 ${activeRunId} 已保存，可再次提交同一问题继续。`);
        a=await stream();
      }
      if (version === selectionVersion.current) {
        clearRunId(window.localStorage,runStorageKey);
        const normalized=normalizeAnswer(a);
        setAnswer(normalized);
        setAnalysisTrace(normalized.analysis_trace || []);
        setHistory((h) => [normalized, ...h.filter(item=>item.id!==normalized.id)]);
        api<Workspace>(
          "/workspace?" +
            new URLSearchParams({
              snapshot_id: workingSid,
              topic,
              dimension,
              start,
              end,
              include_records: "false",
            }),
        ).then((w) => {
          if (version === selectionVersion.current) setWork(w);
        });
      }
    } catch (e) {
      if (version === selectionVersion.current) setQuestion(current=>current||submittedQuestion);
      setError((e as Error).message);
    } finally {
      setAsking(false);
    }
  }
  async function downloadAnswerReport(item: Answer) {
    if (!item.summary || reportDownloading) return;
    setReportDownloading(true);
    setError("");
    try {
      const response = await fetch(`/api/answers/${encodeURIComponent(item.id)}/report.html`);
      if (!response.ok) {
        const raw = await response.text();
        let detail = "HTML 报告生成失败";
        try { const data = JSON.parse(raw); detail = typeof data.detail === "string" ? data.detail : detail; } catch { if (raw) detail = raw; }
        throw new Error(detail);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      const filename = encodedName ? decodeURIComponent(encodedName) : "GeoAgent分析报告.html";
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setNotice(`HTML 报告已生成：${filename}`);
      window.setTimeout(() => setNotice(""), 5000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "HTML 报告生成失败");
    } finally {
      setReportDownloading(false);
    }
  }
  async function generate() {
    if (!workingSid) return;
    setGenerating(true);
    setError("");
    const version = selectionVersion.current;
    try {
      const b = await api<Brief>("/briefs", { snapshot_id: workingSid, template_id: briefTemplateId });
      setBriefs((bs) => [b, ...bs]);
      if (version === selectionVersion.current) {
        setPage("briefs");
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
    closeDetails();
    setTab(id === "all" ? "assets" : "map");
    setSourcesOpen(false);
  }
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
    <div ref={shellRef} className={`prototype-shell research-layout${chatExpanded ? " research-expanded" : ""}`}
      style={{ "--research-dock-width": `${researchWidth}%` } as CSSProperties}>
      {settingsOpen ? <IntegrationSettings initialSection={settingsSection} onClose={()=>{setSettingsOpen(false);void refreshStatus()}} onSaved={()=>void reloadConnection()}/> : null}
      {mcpWorkbenchOpen ? <McpWorkbench initialView={mcpWorkbenchView} context={{snapshot_id:workingSid,topic,dimension,start,end}} onClose={()=>setMcpWorkbenchOpen(false)}/> : null}
      {briefTemplateOpen ? <BriefTemplateEditor onClose={()=>setBriefTemplateOpen(false)} onChanged={acceptBriefTemplates}/> : null}
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
          <SessionAccount/>
          <button onClick={()=>{setSettingsSection("embed");setSettingsOpen(true)}}>连接配置</button>
          <button onClick={()=>{setMcpWorkbenchView("local");setMcpWorkbenchOpen(true)}}>MCP 工作台</button>
          <label className="snapshot-selector">
            <span>数据快照</span>
            <select
              aria-label="数据快照"
              title="选择不可变数据快照"
              value={sid}
              onChange={(e) => void selectSnapshot(e.target.value)}
            >
              {!sid && <option value="">尚未导入</option>}
              {status?.snapshots.map((s) => (
                <option key={s.id} value={s.id}>
                  {dateText(s.created_at)} · {s.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={importing}
            onClick={importData}
            title="手动从已发布采集产物导入快照，不会启动采集器"
            aria-label="手动导入快照（不会启动采集器）"
          >
            <RefreshCw size={15} className={importing ? "spin" : ""} />
            <span>更新快照</span>
          </button>
          <button
            className="primary"
            aria-label="生成双主题简报"
            title="基于当前选定快照生成双主题简报"
            disabled={!workingSid || generating}
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
      <div className="prototype-grid research-grid">
        <main className="monitor-panel" inert={chatExpanded} aria-hidden={chatExpanded || undefined}>
          <div className="monitor-tabs">
            {[
              ["map", "风险监测"],
              ["meetings", "会晤日历"],
              ["evidence", "证据台账"],
              ["scenarios", "情景推演"],
              ["sources", "数据源"],
              ["pipeline", "采集管线监控"],
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
                  closeDetails();
                  if (id === "briefs") {
                    setPage("briefs");
                    setSourcesOpen(false);
                  } else {
                    if (page === "briefs") setPage(lastTopic.current);
                    setTab(id);
                    setSourcesOpen(id === "sources");
                  }
                }}
              >
                {label}
              </button>
            ))}
          </div>
          {page !== "briefs" && !["pipeline", "meetings"].includes(tab) && (
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
          {!sid && tab !== "pipeline" && (
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
          {tab === "pipeline" && (
            <CollectorMonitor
              selectedSnapshotId={workingSid}
              onWorkingSnapshotChange={(nextId) => {
                setWorkingSid(nextId);
                setSid(nextId);
              }}
              onSnapshotInspect={(snapshotId) => {
                setSid(snapshotId);
                setTab("map");
                setNotice(`已打开快照 ${snapshotId.slice(0, 10)}… 的研究数据范围`);
                window.setTimeout(() => setNotice(""), 4000);
              }}
            />
          )}
          {work && page !== "briefs" && tab !== "pipeline" && (
            <div
              className={
                "center-view " + (tab === "map" ? "map-view" : "scroll-view")
              }
            >
              {modelSummaryTabs.has(tab) && (
                <section className={`page-model-summary ${pageSummary?.status || "loading"}`}>
                  <div className="page-model-summary-heading">
                    <div>
                      <span>模型综合判断</span>
                      <small>
                        快照 {sid.slice(0, 8)} · {pageSummary?.model || status?.model.model || "模型服务"}
                        {pageSummary?.cached ? " · 已读取缓存" : ""}
                      </small>
                    </div>
                    {(pageSummary?.status === "failed" || pageSummary?.status === "unavailable") && (
                      <button onClick={() => setPageSummaryRefresh((value) => value + 1)}>重新生成</button>
                    )}
                  </div>
                  {pageSummaryLoading ? (
                    <p><RefreshCw className="spin" size={14} />正在结合当前快照的全部指标、证据与研究规则生成总结…</p>
                  ) : pageSummary?.text ? (
                    <p>{pageSummary.text}</p>
                  ) : (
                    <p>{pageSummary?.message || "等待当前快照的模型总结。"}</p>
                  )}
                  {pageSummary?.text && <small>{pageSummary.message} · 生成于 {dateText(pageSummary.created_at)}</small>}
                  {!!pageSummary?.evidence_ids.length && (
                    <div className="page-model-summary-evidence">
                      {pageSummary.evidence_ids.slice(0, 8).map((id, index) => (
                        <button key={id} onClick={() => openEvidence(id)}>引用 {index + 1}</button>
                      ))}
                    </div>
                  )}
                </section>
              )}
              {tab === "map" &&
                <div
                  style={{ height: "100%", width: "100%", minWidth: 0, flex: 1 }}
                >
                  <Suspense fallback={<div className="empty"><RefreshCw className="spin" />正在加载地图…</div>}>
                    <MapView
                      records={mapRecords}
                      meetings={mapMeetings}
                      topic={topic}
                      resetKey={resetKey}
                      onTranslateTitles={status?.model.configured ? translateMapTitles : undefined}
                      translationStatus={status?.translation?.snapshot_id===sid ? status.translation : undefined}
                      onSelect={(r) => {
                        openEvidence(r.id);
                        setAsset(null);
                      }}
                      onMeetingSelect={(meeting) => {
                        openEvidence(meeting.evidence_id);
                        setAsset(null);
                      }}
                    />
                    {mapLoading && <div className="map-data-loading"><RefreshCw className="spin" size={14}/>正在加载地图动态…</div>}
                  </Suspense>
                </div>
              }
              {tab === "meetings" && (
                <MeetingCalendar
                  snapshotId={sid}
                  topic={topic as "all" | "usiran" | "ukraine"}
                  onEvidence={openEvidence}
                />
              )}
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
                        }}
                      />
                    </div>
                    <small>
                      共 {evidenceTotal} 条 · 当前加载 {evidenceRows.length} 条
                      {work.snapshot.cleaning?.duplicates_merged
                        ? ` · 已归并 ${work.snapshot.cleaning.duplicates_merged} 条重复记录`
                        : ""}
                    </small>
                  </div>
                  <div className="evidence-list">
                    {evidenceRows.map((r) => (
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
                          <small className="translation-state">{titleTranslationLabel(r,status?.translation?.snapshot_id===sid ? status.translation : undefined)}</small>
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
                    {!evidenceRows.length && !evidenceLoading && (
                      <div className="empty">
                        <BookOpen />
                        <h3>该范围尚无可用证据</h3>
                        <p>
                          可以调整筛选，或接入该维度的爬虫；不会用其他主题数据补齐。
                        </p>
                      </div>
                    )}
                  </div>
                  {evidenceMore && (
                    <button
                      className="load-more"
                      disabled={evidenceLoading}
                      onClick={() => loadEvidence(evidenceRows.length)}
                    >
                      {evidenceLoading ? "正在加载…" : "再加载 30 条"}
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
                      <h3>当前范围没有可用的民调指标</h3>
                      <p>
                        请核对所选快照、主题、维度和日期。新闻中的百分比不等于可比较的支持率序列；不会借用其他快照的数据补图。
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
                            onClick={() => openAsset(a)}
                            tabIndex={0}
                            onKeyDown={(e) => e.key === "Enter" && openAsset(a)}
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
                  <div><h2>简报档案</h2><p>选择模板生成，历史观点按快照保留</p></div>
                </div>
                <div className="brief-template-picker">
                  <label htmlFor="brief-template">生成模板</label>
                  <select id="brief-template" aria-label="简报模板" value={briefTemplateId} onChange={event=>setBriefTemplateId(event.target.value)}>{briefTemplates.map(row=><option key={row.id} value={row.id}>{row.name}{row.is_default?" · 默认":""}</option>)}</select>
                  <button onClick={()=>setBriefTemplateOpen(true)}>配置模板</button>
                  <button className="primary" disabled={!workingSid||generating} onClick={generate}>{generating&&<RefreshCw size={13} className="spin"/>}{generating?"正在分析证据…":"生成新简报"}</button>
                </div>
                {briefs.map((b) => (
                  <button
                    key={b.id}
                    className={
                      brief?.id === b.id ? "brief-item selected" : "brief-item"
                    }
                    onClick={() => void openBrief(b)}
                  >
                    <FileText size={17} />
                    <div className="brief-item-copy">
                      <div className="brief-item-date"><strong>{dateText(b.created_at)}</strong>{b.snapshot_id===sid&&<span>当前快照</span>}</div>
                      <small className={b.mode.includes("现场模型")?"mode-model":b.mode.includes("部分")?"mode-partial":"mode-local"}>{b.mode}</small>
                      {b.template_id&&<small>模板 {briefTemplates.find(row=>row.id===b.template_id)?.name||b.template_id} v{b.template_version||1}</small>}
                      <small>
                        快照 {b.snapshot_id.slice(0, 8)}
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
                        <div className="brief-meta">
                          <span className={`brief-mode ${brief.mode.includes("现场模型") ? "model" : brief.mode.includes("部分") ? "partial" : "local"}`}>{brief.mode}</span>
                          <span>快照 {brief.snapshot_id.slice(0, 8)}</span>
                          {brief.model ? <span>模型 {brief.model}</span> : <span>规则分析</span>}
                          {brief.window_start && brief.window_end && <span>分析窗口 {dateText(brief.window_start)}—{dateText(brief.window_end)}</span>}
                        </div>
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
                        <BriefViewer markdown={brief.markdown || ""} />
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
                <div className="button-row source-jumps" aria-label="数据接入快捷入口">
                  <button onClick={() => document.getElementById("crawler-orchestration")?.scrollIntoView({ behavior: "smooth" })}>后台爬虫</button>
                  <button onClick={() => document.getElementById("mcp-news-source")?.scrollIntoView({ behavior: "smooth" })}>MCP 新闻导入</button>
                  <button onClick={() => {setSettingsSection("model");setSettingsOpen(true)}}>后台模型状态</button>
                </div>
                <div className="alert">
                  覆盖统计来自当前快照；后台爬虫运行状态会自动刷新。导入成功不代表每个外部来源都采集正常。
                </div>
                <section className="integration-card" id="crawler-orchestration">
                  <div className="integration-heading">
                    <div>
                      <h3>后台爬虫任务</h3>
                      <p className="muted">服务启动后按注册顺序逐个运行，完成后自动生成快照。</p>
                    </div>
                    <span className={status?.crawler?.state === "running" ? "run-state running" : "run-state"}>
                      {status?.crawler?.state === "running" ? "运行中" : status?.crawler?.state === "completed" ? "已完成" : "未运行"}
                    </span>
                  </div>
                  <div className="run-summary">
                    <strong>{status?.crawler?.results.filter((r) => r.status === "succeeded").length || 0}</strong><span>成功</span>
                    <strong>{status?.crawler?.results.filter((r) => r.status === "failed").length || 0}</strong><span>失败</span>
                    <strong>{status?.crawler?.results.filter((r) => r.status === "skipped").length || 0}</strong><span>跳过</span>
                    <strong>{status?.crawler?.total || 0}</strong><span>总任务</span>
                  </div>
                  {status?.crawler?.current && <p>正在运行：<b>{status.crawler.current}</b></p>}
                  {status?.crawler?.results.slice(-5).reverse().map((result) => (
                    <div className="run-row" key={result.id}>
                      <span>{result.name}</span>
                      <code>{result.launcher || "没有启动文件"}</code>
                      <small className={result.status === "failed" ? "warning-text" : ""}>
                        {result.status === "succeeded" ? "成功" : result.status === "failed" ? "失败" : result.status === "skipped" ? "跳过" : "运行中"}
                      </small>
                    </div>
                  ))}
                  {status?.crawler?.import_error && <p className="warning-text">自动快照失败：{status.crawler.import_error}</p>}
                </section>
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
                      {(projectLabels as Record<string, string>)[p.id] || "新增数据项目"}
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
                <section className="integration-card mcp-card" id="mcp-news-source">
                  <div className="integration-heading">
                    <div>
                      <h3>MCP 新闻导入</h3>
                      <p className="muted">复用 MCP 工作台的外部服务配置。新闻进入新快照，标记为“MCP 导入，待核验”。</p>
                    </div>
                  </div>
                  <div className="config-grid">
                    <label className="wide-field">新闻服务<select value={mcpServerId} onChange={e=>setMcpServerId(e.target.value)}>
                      <option value="">{mcpRegistry.servers.length ? "请选择外部 MCP 服务" : "尚未配置外部 MCP 服务"}</option>
                      {mcpRegistry.servers.map(server=><option key={server.id} value={server.id}>{server.name}</option>)}
                    </select></label>
                  </div>
                  <div className="button-row">
                    <button onClick={()=>{setMcpWorkbenchView("external");setMcpWorkbenchOpen(true)}}>管理外部 MCP 连接</button>
                  </div>
                  <p className="muted">新闻导入要求服务提供 service_status 与 knowledge_search。连通性测试通过，不代表支持新闻导入；通用 MCP 服务仍可保留在工作台。</p>
                  {mcpRegistry.servers.find(server=>server.id===mcpServerId)?.news_import?.supported===false &&
                    <p role="status">所选服务未提供新闻导入所需工具，请选择其他服务。</p>}
                  <h4>获取新闻并生成快照</h4>
                  <div className="config-grid">
                    <label className="wide-field">检索内容<input value={mcpQuery} onChange={(e) => setMcpQuery(e.target.value)} placeholder="输入中文或英文检索词" /></label>
                    <label>主题<select value={mcpTopic} onChange={(e) => { const next=e.target.value as "usiran"|"ukraine"; setMcpTopic(next); setMcpDimension(next === "usiran" ? "regional" : "frontline"); setMcpQuery(next === "usiran" ? "美伊局势 最新进展" : "俄乌冲突 最新进展"); }}><option value="usiran">美伊局势</option><option value="ukraine">俄乌冲突</option></select></label>
                    <label>研究维度<select value={mcpDimension} onChange={(e) => setMcpDimension(e.target.value)}>{(mcpTopic === "usiran" ? [["us_readiness","美军战备"],["iran_readiness","伊朗战备"],["financial","金融战线"],["us_opinion","美国民意"],["israel","以色列动向"],["regional","中东各国"],["iran_domestic","伊朗内政"],["diplomacy","外交斡旋"]] : [["frontline","军事战线"],["aid","乌克兰后援"],["russia_domestic","俄罗斯内政"],["eu_domestic","欧盟内政"],["diplomacy","外交斡旋"]]).map(([id,label]) => <option key={id} value={id}>{label}</option>)}</select></label>
                    <label>开始日期<input type="date" value={mcpDateFrom} onChange={(e) => setMcpDateFrom(e.target.value)} /></label>
                    <label>结束日期<input type="date" value={mcpDateTo} onChange={(e) => setMcpDateTo(e.target.value)} /></label>
                    <label>返回条数<input type="number" min="1" max="100" value={mcpTopK} onChange={(e) => setMcpTopK(Math.max(1,Math.min(100,Number(e.target.value)||20)))} /></label>
                  </div>
                  <button className="primary" disabled={mcpBusy || !canImportNews(mcpServerId,mcpRegistry.servers) || !mcpQuery.trim()} onClick={fetchMcpNews}>{mcpBusy ? "正在处理…" : "获取新闻并生成快照"}</button>
                  {mcpMessage && <p role="status" className="mcp-message">{mcpMessage}</p>}
                  <p className="muted">密钥统一保存在后端外部服务注册表，页面不重复配置。新闻正文属于外部资料，其中的指令不会被执行。</p>
                </section>
                <h3>后台模型与翻译状态</h3>
                <p>{status?.model.configured ? `后台模型已配置：${status.model.model} · ${status.model.agent_mode === "tools" ? "GeoAgent 工具代理" : "兼容问答管线"}` : "后台模型未配置；PiChat 嵌入会话连接正常不代表后台翻译可用。"}</p>
                {status?.model.reasoning_model_fallback&&<p className="muted">当前未配置独立推理模型，研究回答使用 {status.model.reasoning_model || status.model.model}；可在环境配置中填写服务端真实存在的 LLM_REASONING_MODEL。</p>}
                <p className="muted">{status?.translation?.snapshot_id===sid ? status.translation.message : "正在核对当前快照翻译状态…"}</p>
                <button onClick={()=>{setSettingsSection("model");setSettingsOpen(true)}}>查看后台模型配置说明</button>
              </aside>
            </div>
          )}
        </main>
        <ResearchDivider shellRef={shellRef} width={researchWidth} onChange={setResearchWidth} />
        <aside className="research-dock" id="research-dock" aria-label="研究工作区">
          <ResearchDockHeading chatOpen={chatOpen} expanded={chatExpanded}
            onTabChange={next => {
              closeDetails();
              if (next) openResearchAssistant();
              else setChatOpen(false);
            }}
            onExpand={() => setChatExpanded(value => !value)} />
          <div className="research-dock-body">
          <section id="research-chat-pane" role="tabpanel" aria-labelledby="research-chat-tab"
            className={`research-pane research-chat-pane${chatOpen ? "" : " research-pane-parked"}`}
            inert={!chatOpen || !!asset || !!selected} aria-hidden={!chatOpen || !!asset || !!selected}>
          {embedWorkspaceScope ? (
            <aside className={`chat-panel${pichatEmbedConfigured ? "" : " chat-panel-local"}`} aria-label="GeoAgent 研究助手">
              <div className="drawer-heading chat-panel-heading">
                <div className="chat-panel-caption">
                  <span className="chat-panel-scope">{names[topic]} · 快照 {workingSid.slice(0, 8) || "未确认"}</span>
                  <details className="chat-panel-context">
                    <summary>连接与范围</summary>
                    <div>
                      <small>{pichatEmbedConfigured ? `PiChat Agent · ${pichatAgentId}` : `${status?.model.model || "本地证据检索"} · 内置问答`}</small>
                      <small>跟踪维度：{topicInfo?.dimensions.find(item => item.id === dimension)?.name || "全部维度"}</small>
                      <small>报道日期：{start || "不限开始"} — {end || "不限结束"}</small>
                      {pichatEmbedConfigured && <small role="status" aria-label="看板范围同步状态">{viewBridge.status}。仅同步快照、主题、维度、日期。</small>}
                      <small>范围变化仅用于下一轮提问；已有回答与会话不重建。</small>
                    </div>
                  </details>
                </div>
              </div>
              {pichatEmbedConfigured ? <div className="pichat-embed-shell">
                {!pichatReady && !pichatAuthRequired && !pichatError && !pichatNotice && (
                  <div className="pichat-embed-status">
                    <RefreshCw className="spin" size={16} /> 正在连接 PiChat Agent…
                  </div>
                )}
                {pichatAuthRequired && (
                  <div className="pichat-embed-status" role="status">
                    正在获取 PiChat 嵌入身份…
                  </div>
                )}
                {pichatNotice && <div className="pichat-embed-status" role="status">{pichatNotice}</div>}
                {pichatError && (
                  <div className="pichat-embed-status error" role="alert">{pichatError}
                    <button type="button" onClick={()=>setConnectionVersion(value=>value+1)}>重试连接</button>
                  </div>
                )}
                {connectionLoaded && embedWorkspaceScope ? <iframe
                  key={connectionVersion}
                  ref={pichatFrameRef}
                  title="GeoAgent PiChat 研究助手"
                  className="pichat-embed-frame"
                  src={pichatUrl}
                  onError={() =>
                    setPichatError("PiChat 面板加载失败，请在连接配置中检查服务地址。")
                  }
                  onLoad={() => {
                    void pichatFrameLoaded();
                  }}
                  allow="clipboard-read; clipboard-write"
                /> : null}
              </div> : <>
                <div className="chat-body">
                  <p className="chat-intro">
                    使用当前工作快照中的结构化指标和新闻证据回答。数字由程序查询，模型负责归纳，并保留引用和数据缺口。
                  </p>
                  <div className="presets">
                    {["本周哪些指标变化最大？", "哪些证据改变了当前判断？", "当前结论有哪些反证？", "还缺什么关键数据？"].map(item => (
                      <button type="button" key={item} disabled={!workingSid || asking} onClick={() => { setQuestion(item); void ask(item); }}>{item}</button>
                    ))}
                  </div>
                  {history.length > 0 && <select aria-label="历史问答" value={answer?.id || ""} onChange={event => setAnswer(history.find(item => item.id === event.target.value) || null)}>
                    <option value="">历史问答</option>
                    {history.map(item => <option key={item.id} value={item.id}>{item.query}</option>)}
                  </select>}
                  {!workingSid && <div className="empty"><p>请先在顶部快照版本中确认一个工作快照，再开始提问。</p></div>}
                  {asking && <div className="answer answer-working">
                    <div className="answer-working-label"><RefreshCw className="spin" size={16}/><span>正在结合当前快照进行多轮分析…</span></div>
                    <AnalysisProcess live rounds={analysisTrace.length?analysisTrace:[{round:1,stage:"analysis",title:"理解问题与初步回答",status:"running",summary:"正在理解问题、锁定范围并检索首轮内部资料。",details:["即将展示检索范围、阶段结论、复核问题和新增依据。"],search_queries:[],evidence_ids:[]}]} planned={status?.model.qa_rounds||3} openingEvidenceId={openingEvidenceId} onEvidence={id=>void openEvidence(id,null,workingSid)}/>
                  </div>}
                  {answer && !asking && <div className="answer">
                    <div className="question-bubble">{answer.query}</div>
                    <div className="answer-mode">{answer.mode}<small>数据截至 {dateText(answer.as_of)} · 快照 {answer.snapshot_id.slice(0, 8)}{answer.retrieval_window?.recent_after?` · 近期证据自 ${answer.retrieval_window.recent_after}`:""}</small></div>
                    {(answer.analysis_trace?.length||analysisTrace.length)>0&&<AnalysisProcess rounds={answer.analysis_trace||analysisTrace} planned={answer.model_rounds?.planned||answer.analysis_trace?.length||analysisTrace.length} openingEvidenceId={openingEvidenceId} onEvidence={id=>void openEvidence(id,null,answer.snapshot_id)}/>}
                    {evidenceOpenError&&<div className="inline-error" role="alert">{evidenceOpenError}</div>}
                    {answer.summary ? <div className="answer-summary"><header><span>综合研判</span><small>结论、证据、资产传导与观察点</small></header><Markdown remarkPlugins={[remarkGfm]}>{normalizeAnswerMarkdown(answer.summary.text)}</Markdown><div className="citation-buttons">{answer.summary.evidence_ids.map((id,index) => <button className="citation" key={id} aria-busy={openingEvidenceId===id} disabled={openingEvidenceId===id} onClick={() => openEvidence(id,null,answer.snapshot_id)}>{openingEvidenceId===id?"正在读取…":`查看依据 ${index+1}`}</button>)}</div></div> : <Markdown remarkPlugins={[remarkGfm]}>{normalizeAnswerMarkdown(answer.message)}</Markdown>}
                    {answer.summary && <p className="answer-status">{answer.message}</p>}
                    {answer.cost_estimate?.estimate_available && <p className="answer-status">本轮模型费用估算：¥{Number(answer.cost_estimate.estimated_cost_cny || 0).toFixed(4)}{answer.cost_estimate.limit_enabled && answer.cost_estimate.max_cost_cny != null ? ` / 上限 ¥${answer.cost_estimate.max_cost_cny}` : " · 未设置费用上限"}。该数值按配置单价估算，不代表服务商账单。</p>}
                    {answer.summary && <div className="answer-actions"><button type="button" onClick={()=>void downloadAnswerReport(answer)} disabled={reportDownloading}><Download size={14}/>{reportDownloading?"正在生成业务核心判断并整理报告…":"保存为 HTML 报告"}</button><small>导出时调用模型生成业务核心判断，原问答正文与引用保持不变。</small></div>}
                    {(answer.facts.length > 0 || answer.inferences.length > 0 || answer.citations.length > 0 || (answer.internal_sources?.length||0) > 0 || answer.gaps.length > 0) && <details className="answer-evidence">
                      <summary>研究依据与数据缺口</summary>
                      {answer.facts.length > 0 && <><h3>事实与程序计算</h3>{answer.facts.map((item,index) => <div className="fact" key={`fact-${index}`}><label>{item.kind}</label><p>{item.text}</p><div className="citation-buttons">{item.evidence_ids.map(id => <button className="citation" key={id} onClick={() => openEvidence(id,null,answer.snapshot_id)}>查看证据</button>)}</div></div>)}</>}
                      {answer.inferences.length > 0 && <><h3>模型推论</h3>{answer.inferences.map((item,index) => <div className="fact inference" key={`inference-${index}`}><label>{item.kind}</label><p>{item.text}</p><div className="citation-buttons">{item.evidence_ids.map(id => <button className="citation" key={id} onClick={() => openEvidence(id,null,answer.snapshot_id)}>查看依据</button>)}</div></div>)}</>}
                      {answer.citations.length > 0 && <><h3>引用来源</h3>{answer.citations.map(item => <button className="reference" key={item.id} onClick={() => openEvidence(item.id,null,answer.snapshot_id)}><BookOpen size={14}/><span>{item.title}</span></button>)}</>}
                      {(answer.internal_sources?.length||0) > 0 && <><h3>华泰内部消息补充</h3><p className="internal-source-boundary">内部消息用于补充线索，内容及上游摘要仍需结合官方或独立来源核验。</p>{answer.internal_sources!.map(item => <details className="internal-source" key={item.id}><summary><span>{item.ai_title||item.title||"未命名消息"}</span><small>{dateText(item.message_at||item.receive_time||"")}</small></summary><p>{item.content_abstract||item.ai_short||item.ai_content||"暂无可展示摘要"}</p><div>{[item.text_class,item.primary_tag,item.group_name].filter(Boolean).join(" · ")}</div></details>)}</>}
                      {answer.gaps.length > 0 && <><h3>数据缺口</h3><ul>{answer.gaps.map(item => <li key={item}>{item}</li>)}</ul></>}
                      {answer.steps.length > 0 && <div className="steps">{answer.steps.join(" → ")}</div>}
                    </details>}
                  </div>}
                </div>
                <form className="chat-form" onSubmit={event => { event.preventDefault(); void ask(); }}>
                  <textarea aria-label="研究问题" placeholder={workingSid ? "询问指标变化、证据、反证或资产影响…" : "请先确认工作快照"} value={question} disabled={!workingSid || asking} onChange={event => setQuestion(event.target.value)}/>
                  <button className="primary" type="submit" aria-label="发送问题" disabled={!workingSid || asking || !question.trim()}><Send size={16}/></button>
                </form>
              </>}
            </aside>
          ) : <div className="empty" role="status">
            {status && !workingSid ? (sid ? <>
              <p>请先确认工作快照，再开始研究问答。</p>
              <button type="button" className="primary" onClick={() => void selectSnapshot(sid)}>
                将当前快照设为工作快照
              </button>
            </> : "请先导入并选择工作快照，再开始研究问答。") : "正在加载工作快照，研究助手即将就绪…"}
          </div>}
          </section>
          <section id="research-insight-pane" role="tabpanel" aria-labelledby="research-insight-tab"
            className={`research-pane research-insight-pane${chatOpen ? " research-pane-parked" : ""}`}
            inert={chatOpen || !!asset || !!selected} aria-hidden={chatOpen || !!asset || !!selected}>
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
                        setSourcesOpen(false);
                      }}
                    >
                      <span>{label}</span>
                      <b>
                        {work?.signal_counts?.[key] ?? 0}
                      </b>
                    </button>
                  ))}
                </div>
                <small>报道线索数量，不代表整体冲突强度</small>
                <div className="insight-stats">
                  <div>
                    <b>{work?.records_count ?? 0}</b>
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
                  工作快照 {workingSid.slice(0, 8) || "尚未确认"} ·{" "}
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
                      onClick={() => openAsset(a)}
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
                {evidenceRows.slice(0, 5).map((r) => (
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
                    <small className="translation-state">{titleTranslationLabel(r,status?.translation?.snapshot_id===sid ? status.translation : undefined)}</small>
                  </button>
                ))}
                {!evidenceRows.length && !evidenceLoading && (
                  <p className="muted">当前筛选没有可用记录</p>
                )}
              </section>
              <div className="insight-section model-state">
                {status?.translation?.snapshot_id===sid ? status.translation.message : "正在核对当前快照翻译状态…"}{" "}
                · 公开来源
              </div>
            </div>
          </div>
        </aside>
          </section>
      {asset && (
        <div
          className="drawer-backdrop asset-backdrop"
          onClick={closeDetails}
        >
          <aside className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-heading">
              <h2>{asset.name} · 条件传导</h2>
              <button aria-label="关闭资产详情" onClick={closeDetails}>
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
                closeDetails();
                openResearchAssistant();
                setSourcesOpen(false);
              }}
            >
              向研究助手追问
            </button>
          </aside>
        </div>
      )}
      {!selected && evidenceRequest && (openingEvidenceId || evidenceOpenError) && (
        <div className="drawer-backdrop evidence-backdrop" onClick={closeDetails}>
          <aside className="drawer evidence-loading-drawer" role="dialog" aria-modal="true" aria-label="证据读取状态" onClick={(event)=>event.stopPropagation()}>
            <div className="drawer-heading">
              <h2><BookOpen size={17}/>证据详情</h2>
              <button aria-label="关闭证据读取状态" onClick={closeDetails}><X/></button>
            </div>
            {openingEvidenceId ? <div className="evidence-loading-state" role="status">
              <RefreshCw className="spin" size={21}/>
              <div><strong>正在读取证据全文</strong><p>首次打开历史快照可能需要几秒，读取完成后会自动显示来源、时间和原文。</p></div>
            </div> : <div className="evidence-loading-state error" role="alert">
              <AlertTriangle size={21}/>
              <div><strong>证据读取失败</strong><p>{evidenceOpenError}</p><button type="button" onClick={()=>void openEvidence(evidenceRequest.id,null,evidenceRequest.snapshotId)}>重新读取</button></div>
            </div>}
          </aside>
        </div>
      )}
      {selected && (
        <div
          className="drawer-backdrop evidence-backdrop"
          onClick={closeDetails}
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
                onClick={closeDetails}
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
            <p className="translation-state">{titleTranslationLabel(selected,status?.translation?.snapshot_id===selectedSnapshotId ? status.translation : undefined)}</p>
            {!selected.supplemental && (
              <ReviewPanel
                key={selectedSnapshotId + selected.id}
                record={selected}
                sid={selectedSnapshotId}
                onSaved={(r) => {
                  setSelected(r);
                  workspaceCache.current.clear();
                  mapCache.current.clear();
                  setPageSummaryRefresh((value) => value + 1);
                  setMapRecords((rows) => rows.map((x) => x.id === r.id ? r : x));
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
            <h3>{selected.text_translation_state==="untranslated" ? "原文摘要（暂无中文译文）" : "中文摘要"}</h3>
            {!status?.model.configured && <p className="muted">后台模型未配置，当前保留原文。可在“连接配置 → 后台模型”查看说明；嵌入 App Key 不用于后台翻译。</p>}
            <button
              disabled={translating || !status?.model.configured}
              onClick={async () => {
                const version = detailRequestVersion.current;
                setTranslating(true);
                try {
                  const r = await api<{ ok: boolean; message: string }>(
                    `/evidence/${selected.id}/translate?snapshot_id=${selectedSnapshotId}`,
                    {},
                  );
                  if (version !== detailRequestVersion.current) return;
                  setNotice(r.message);
                   if (r.ok) await openEvidence(selected.id, sourceMetric, selectedSnapshotId);
                } catch {
                  if (version === detailRequestVersion.current) setError("翻译请求失败，原文保留");
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
        </aside>
      </div>
    </div>
  );
}
