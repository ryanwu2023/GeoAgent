import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Archive,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  FileCheck2,
  Filter,
  GitBranch,
  History,
  ListChecks,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  ShieldCheck,
  Settings2,
  WifiOff,
  X,
} from "lucide-react";
import { api, apiPut } from "./api";

type CollectorStatus = "success" | "running" | "partial" | "failed" | "stale" | "offline";
type CollectorGroup = "新闻" | "政府" | "媒体" | "市场" | "未分类";

type CollectorRecord = {
  id: string;
  name: string;
  group: CollectorGroup;
  status: CollectorStatus;
  stage: string;
  elapsed: string;
  startedAt: string;
  finishedAt: string;
  attempts: string;
  records: number;
  outputs: number;
  lastSuccess: string;
  sourceHealth: string;
  errorType?: string;
  errorMessage?: string;
};

const statusMeta: Record<
  CollectorStatus,
  { label: string; className: string; icon: typeof CheckCircle2 }
> = {
  success: { label: "成功", className: "success", icon: CheckCircle2 },
  running: { label: "运行中", className: "running", icon: Activity },
  partial: { label: "部分成功", className: "partial", icon: AlertTriangle },
  failed: { label: "采集失败", className: "failed", icon: AlertTriangle },
  stale: { label: "数据过期", className: "stale", icon: Clock3 },
  offline: { label: "离线", className: "offline", icon: WifiOff },
};

const groupOptions: Array<CollectorGroup | "全部分组"> = [
  "全部分组",
  "新闻",
  "政府",
  "媒体",
  "市场",
  "未分类",
];

const demoCollectors: CollectorRecord[] = [
  { id: "xinhua-monitor", name: "新华网中文国际动态", group: "新闻", status: "success", stage: "已发布标准产物", elapsed: "3分12秒", startedAt: "09:01", finishedAt: "09:04", attempts: "1 / 2", records: 42, outputs: 3, lastSuccess: "今天 09:04", sourceHealth: "3 / 3 来源正常" },
  { id: "defense-spend-monitor", name: "军费与补给", group: "政府", status: "success", stage: "已完成输出门禁", elapsed: "1分48秒", startedAt: "09:02", finishedAt: "09:04", attempts: "1 / 2", records: 16, outputs: 2, lastSuccess: "今天 09:04", sourceHealth: "2 / 2 来源正常" },
  { id: "diplomacy-monitor", name: "美伊外交", group: "政府", status: "running", stage: "解析页面内容", elapsed: "18分钟", startedAt: "09:00", finishedAt: "执行中", attempts: "1 / 2", records: 28, outputs: 1, lastSuccess: "昨天 09:06", sourceHealth: "1 / 2 来源响应" },
  { id: "eu-domestic-monitor", name: "欧盟内政", group: "政府", status: "stale", stage: "等待下一次计划", elapsed: "—", startedAt: "昨天 09:00", finishedAt: "昨天 09:03", attempts: "1 / 2", records: 21, outputs: 2, lastSuccess: "昨天 09:03", sourceHealth: "1 / 2 来源过期" },
  { id: "finance-front-monitor", name: "金融战线", group: "市场", status: "success", stage: "已写入快照", elapsed: "2分26秒", startedAt: "09:03", finishedAt: "09:05", attempts: "1 / 2", records: 9, outputs: 2, lastSuccess: "今天 09:05", sourceHealth: "2 / 2 来源正常" },
  { id: "iran-domestic-monitor", name: "伊朗内政", group: "政府", status: "partial", stage: "保留部分有效来源", elapsed: "5分43秒", startedAt: "08:58", finishedAt: "09:04", attempts: "2 / 2", records: 31, outputs: 2, lastSuccess: "今天 09:04", sourceHealth: "2 / 3 来源正常", errorType: "SOURCE_PARTIAL", errorMessage: "一个来源返回空响应，已保留其他来源并标记为部分成功。" },
  { id: "iran-escalation-monitor", name: "伊朗战备", group: "新闻", status: "failed", stage: "请求超时", elapsed: "30分钟", startedAt: "08:45", finishedAt: "09:15", attempts: "2 / 2", records: 0, outputs: 0, lastSuccess: "昨天 09:18", sourceHealth: "0 / 2 来源完成", errorType: "TIMEOUT", errorMessage: "请求超时：无法连接到目标站点，已达到最大重试次数。" },
  { id: "israel-monitor", name: "以色列动向", group: "新闻", status: "success", stage: "已发布标准产物", elapsed: "2分51秒", startedAt: "09:04", finishedAt: "09:07", attempts: "1 / 2", records: 35, outputs: 3, lastSuccess: "今天 09:07", sourceHealth: "3 / 3 来源正常" },
  { id: "market-context-monitor", name: "市场补充", group: "市场", status: "offline", stage: "等待服务恢复", elapsed: "—", startedAt: "—", finishedAt: "—", attempts: "0 / 2", records: 0, outputs: 0, lastSuccess: "前天 09:02", sourceHealth: "服务不可达", errorType: "SERVICE_OFFLINE", errorMessage: "采集服务未报告心跳，暂无新的运行记录。" },
  { id: "mideast-monitor", name: "中东各国", group: "新闻", status: "success", stage: "已完成输出门禁", elapsed: "4分08秒", startedAt: "09:02", finishedAt: "09:06", attempts: "1 / 2", records: 57, outputs: 4, lastSuccess: "今天 09:06", sourceHealth: "4 / 4 来源正常" },
  { id: "naval-monitor", name: "海军动态", group: "媒体", status: "success", stage: "已写入快照", elapsed: "2分19秒", startedAt: "09:05", finishedAt: "09:07", attempts: "1 / 2", records: 18, outputs: 2, lastSuccess: "今天 09:07", sourceHealth: "2 / 2 来源正常" },
  { id: "opinion-monitor", name: "美国民意", group: "媒体", status: "partial", stage: "等待补充来源", elapsed: "3分51秒", startedAt: "09:01", finishedAt: "09:05", attempts: "1 / 2", records: 11, outputs: 1, lastSuccess: "今天 09:05", sourceHealth: "1 / 2 来源正常", errorType: "SOURCE_PARTIAL", errorMessage: "调查元数据不完整，保留报道线索但不生成可比较指标。" },
  { id: "readiness-monitor", name: "战备表态", group: "政府", status: "success", stage: "已完成输出门禁", elapsed: "1分32秒", startedAt: "09:06", finishedAt: "09:08", attempts: "1 / 2", records: 24, outputs: 2, lastSuccess: "今天 09:08", sourceHealth: "2 / 2 来源正常" },
  { id: "ru-diplomacy-monitor", name: "俄乌外交", group: "政府", status: "stale", stage: "等待下一次计划", elapsed: "—", startedAt: "昨天 09:02", finishedAt: "昨天 09:07", attempts: "1 / 2", records: 22, outputs: 2, lastSuccess: "昨天 09:07", sourceHealth: "1 / 2 来源过期" },
  { id: "russia-domestic-monitor", name: "俄罗斯内政", group: "新闻", status: "failed", stage: "解析响应失败", elapsed: "4分02秒", startedAt: "08:56", finishedAt: "09:00", attempts: "2 / 2", records: 0, outputs: 0, lastSuccess: "昨天 09:01", sourceHealth: "0 / 2 来源完成", errorType: "PARSE_ERROR", errorMessage: "响应字段发生变化，schema 门禁拒绝发布不完整产物。" },
  { id: "ua-front-monitor", name: "俄乌军事战线", group: "媒体", status: "success", stage: "已发布标准产物", elapsed: "5分10秒", startedAt: "09:00", finishedAt: "09:05", attempts: "1 / 2", records: 68, outputs: 4, lastSuccess: "今天 09:05", sourceHealth: "4 / 4 来源正常" },
  { id: "ukraine-aid-monitor", name: "乌克兰后援", group: "政府", status: "success", stage: "已写入快照", elapsed: "2分44秒", startedAt: "09:03", finishedAt: "09:06", attempts: "1 / 2", records: 14, outputs: 2, lastSuccess: "今天 09:06", sourceHealth: "2 / 2 来源正常" },
  { id: "taco-monitor", name: "TACO 市场指数", group: "市场", status: "partial", stage: "关键输入缺失", elapsed: "6分22秒", startedAt: "08:54", finishedAt: "09:00", attempts: "2 / 2", records: 7, outputs: 1, lastSuccess: "昨天 09:02", sourceHealth: "1 / 3 来源正常", errorType: "INPUT_MISSING", errorMessage: "CO1 关键输入不可用，本轮保留上次完整指数并标记为降级。" },
  { id: "asset-transmission-monitor", name: "资产传导跟踪", group: "市场", status: "success", stage: "已完成输出门禁", elapsed: "2分12秒", startedAt: "09:07", finishedAt: "09:09", attempts: "1 / 2", records: 13, outputs: 2, lastSuccess: "今天 09:09", sourceHealth: "2 / 2 来源正常" },
];

const statusOrder: Array<CollectorStatus | "all"> = [
  "all",
  "running",
  "failed",
  "partial",
  "stale",
  "offline",
  "success",
];

type CollectorRuntimePayload = {
  available: boolean;
  source: string;
  run_id?: string | null;
  run_status?: string | null;
  updated_at?: string | null;
  import_status?: string | null;
  snapshot_id?: string | null;
  import_error?: string | null;
  import_result?: {
    snapshot_id?: string | null;
    created?: boolean;
    records?: number;
    observations?: number;
    errors?: number;
    cleaning?: {
      raw_records?: number;
      unique_records?: number;
      duplicates_merged?: number;
      invalid_records?: number;
    };
  } | null;
  collectors: Array<
    Partial<CollectorRecord> & {
      id: string;
      name?: string;
      status?: string;
      started_at?: string;
      finished_at?: string;
      last_success?: string;
      source_health?: string;
      error_type?: string;
      error_message?: string;
    }
  >;
};

type CollectorRunSummary = {
  run_id: string;
  run_kind?: string;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
  collector_count: number;
  failed_count: number;
};

type CollectorRunsPayload = { runs: CollectorRunSummary[]; source: string };

type SnapshotSummary = {
  id: string;
  created_at: string;
  records: number;
  observations: number;
  sources: number;
  errors: number;
  cleaning: {
    raw_records?: number;
    unique_records?: number;
    duplicates_merged?: number;
    invalid_records?: number;
  };
  import_stats?: { new?: number; revised?: number; total?: number };
};

type SnapshotSummariesPayload = { snapshots: SnapshotSummary[]; source: string };

type CollectorEvent = {
  id: number;
  event_type: string;
  collector_id?: string | null;
  status?: string | null;
  message?: string | null;
  created_at: string;
};

type CollectorEventsPayload = {
  available: boolean;
  source: string;
  run_id: string;
  events: CollectorEvent[];
};

type CollectorLogPayload = {
  available: boolean;
  source: string;
  run_id: string;
  collector_id: string;
  stream: "stdout" | "stderr";
  content: string;
};

type CollectorSource = {
  id: string;
  publisher?: string;
  state?: string;
  entries?: number;
  kept?: number;
  http_status?: number | null;
  error?: string;
  consecutive_failures?: number;
};

type CollectorSourceGroup = {
  collector_id: string;
  updated_at?: string | null;
  artifact_run_id?: string | null;
  artifact_status?: "available" | "missing" | string;
  published_this_run?: boolean | null;
  artifact_files?: string[];
  summary: { total: number; healthy: number; stale: number; failed: number; disabled: number; entries: number; kept: number };
  sources: CollectorSource[];
};

type CollectorSourcesPayload = {
  available: boolean;
  run_id?: string | null;
  scope?: string;
  collectors: CollectorSourceGroup[];
};

type SourceRecordPreview = {
  id?: string;
  title?: string;
  summary?: string;
  publisher?: string;
  published?: string;
  fetched_at?: string;
  url?: string;
  [key: string]: unknown;
};

type SourceRecordsPayload = {
  available: boolean;
  total: number;
  records: SourceRecordPreview[];
};

type CollectorJobResponse = {
  request_id: string;
  run_id: string;
  status: string;
  error?: string | null;
  import_status?: string | null;
  snapshot_id?: string | null;
  import_error?: string | null;
};

type CollectorImportRetryResponse = CollectorJobResponse & {
  available: boolean;
  source: string;
  reused?: boolean;
  records?: number;
  observations?: number;
  errors?: number;
  cleaning?: {
    raw_records?: number;
    unique_records?: number;
    duplicates_merged?: number;
    invalid_records?: number;
  };
};

type ReviewStatus = "pending" | "accepted" | "rejected";
type CollectorReviewItem = {
  item_id: string;
  label: string;
  status: ReviewStatus;
  reviewer?: string | null;
  reason?: string | null;
  updated_at?: string | null;
};

type PipelineAuditDetail = {
  kind: "candidate" | "import";
  label: string;
  available: boolean;
  source: string;
  run_id: string;
  reason?: string | null;
  metrics?: Record<string, unknown>;
  run_status?: string | null;
  import_status?: string | null;
  snapshot_id?: string | null;
  import_result?: CollectorRuntimePayload["import_result"];
  import_error?: string | null;
};

type CollectorConfig = {
  interval_sec: number;
  exclude_ids: string[];
  auto_import: boolean;
  allow_partial_import: boolean;
  path?: string;
  platform?: string;
  source?: string;
  updated_at?: string;
};

type CollectorConfigPayload = {
  config: CollectorConfig;
  scope: string;
  description: string;
  collectors: Array<{ id: string; name: string; enabled: boolean; excluded: boolean }>;
};

type CollectorConfigDraft = {
  intervalMinutes: number;
  excludeIds: string[];
  allowPartial: boolean;
};

type PipelineView = "run" | "pool" | "snapshots";
type SourceStateFilter = "all" | "ok" | "failed" | "disabled" | "unknown";
type PipelineStage = {
  id: string;
  label: string;
  icon: typeof Activity;
  status: "success" | "running" | "warning" | "blocked" | "pending";
  summary: string;
  metrics: ReadonlyArray<readonly [string, string]>;
  check: string;
  action?: { label: string; handler: () => void };
};

const isCollectorStatus = (value: string | undefined): value is CollectorStatus =>
  value === "success" || value === "running" || value === "partial" || value === "failed" || value === "stale" || value === "offline";

const isCollectorGroup = (value: string | undefined): value is CollectorGroup =>
  value === "新闻" || value === "政府" || value === "媒体" || value === "市场" || value === "未分类";

function mapRuntimeCollector(item: CollectorRuntimePayload["collectors"][number]): CollectorRecord {
  return {
    id: item.id,
    name: item.name || item.id,
    status: isCollectorStatus(item.status) ? item.status : "failed",
    group: isCollectorGroup(item.group) ? item.group : "未分类",
    stage: item.stage || "运行报告未提供阶段",
    elapsed: item.elapsed || "—",
    startedAt: item.started_at || "—",
    finishedAt: item.finished_at || "—",
    attempts: item.attempts || "1 / 2",
    records: item.records ?? 0,
    outputs: item.outputs ?? 0,
    lastSuccess: item.last_success || "未提供",
    sourceHealth: item.source_health || "状态待核对",
    errorType: item.error_type || item.errorType,
    errorMessage: item.error_message || item.errorMessage,
  };
}

const demoMode = import.meta.env.VITE_COLLECTOR_DEMO === "true";

type CollectorMonitorProps = {
  selectedSnapshotId?: string;
  onWorkingSnapshotChange?: (snapshotId: string) => void;
  onSnapshotInspect?: (snapshotId: string) => void;
};

function progressFor(status: CollectorStatus) {
  return { success: 100, running: 64, partial: 100, failed: 100, stale: 100, offline: 0 }[status];
}

function metric(value: number | null | undefined, fallback = "未记录") {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : fallback;
}

function shortId(value: string | null | undefined) {
  return value ? `${value.slice(0, 10)}…` : "—";
}

function runKindLabel(value?: string) {
  return value === "scheduled-service" ? "定时任务" : value === "manual" ? "手动执行" : value || "采集批次";
}

function eventStatusLabel(value?: string | null) {
  return ({ running: "运行中", succeeded: "成功", success: "成功", failed: "失败", accepted: "已通过", pending: "待核对" } as Record<string, string>)[value || ""] || value || "已记录";
}

export default function CollectorMonitor({ selectedSnapshotId = "", onWorkingSnapshotChange, onSnapshotInspect }: CollectorMonitorProps) {
  const [collectors, setCollectors] = useState<CollectorRecord[]>(demoMode ? demoCollectors : []);
  const [dataSource, setDataSource] = useState<"demo" | "runtime" | "unavailable">(demoMode ? "demo" : "unavailable");
  const [pipelineView, setPipelineView] = useState<PipelineView>("run");
  const [apiState, setApiState] = useState<"unknown" | "empty" | "unavailable">("unknown");
  const [summaryRefreshKey, setSummaryRefreshKey] = useState(0);
  const [runtimeRunId, setRuntimeRunId] = useState("");
  const [runtimeRequestId, setRuntimeRequestId] = useState("");
  const [runtimeUpdated, setRuntimeUpdated] = useState("");
  const [runtimeRunStatus, setRuntimeRunStatus] = useState("");
  const [runtimeImportStatus, setRuntimeImportStatus] = useState("");
  const [runtimeSnapshotId, setRuntimeSnapshotId] = useState("");
  const [runtimeImportResult, setRuntimeImportResult] = useState<CollectorRuntimePayload["import_result"]>(null);
  const [snapshotSummaries, setSnapshotSummaries] = useState<SnapshotSummary[]>([]);
  const [workingSnapshotId, setWorkingSnapshotId] = useState(selectedSnapshotId);
  const [sourceGroups, setSourceGroups] = useState<CollectorSourceGroup[]>([]);
  const [poolSourceGroups, setPoolSourceGroups] = useState<CollectorSourceGroup[]>([]);
  const [inspectorTab, setInspectorTab] = useState<"detail" | "history" | "resources">("detail");
  const [historyRuns, setHistoryRuns] = useState<CollectorRunSummary[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [ledgerEvents, setLedgerEvents] = useState<CollectorEvent[]>([]);
  const [ledgerEventsAvailable, setLedgerEventsAvailable] = useState(false);
  const [reviewItems, setReviewItems] = useState<CollectorReviewItem[]>([]);
  const [pipelineDetail, setPipelineDetail] = useState<PipelineAuditDetail | null>(null);
  const [logStream, setLogStream] = useState<"stdout" | "stderr">("stderr");
  const [collectorLog, setCollectorLog] = useState<CollectorLogPayload | null>(null);
  const [collectorSources, setCollectorSources] = useState<CollectorSourceGroup | null>(null);
  const [statusFilter, setStatusFilter] = useState<CollectorStatus | "all">("all");
  const [groupFilter, setGroupFilter] = useState<CollectorGroup | "全部分组">("全部分组");
  const [query, setQuery] = useState("");
  const [sourceQuery, setSourceQuery] = useState("");
  const [sourceStateFilter, setSourceStateFilter] = useState<SourceStateFilter>("all");
  const [selectedSourceKey, setSelectedSourceKey] = useState("");
  const [sourceRecordPreview, setSourceRecordPreview] = useState<SourceRecordsPayload | null>(null);
  const [selectedId, setSelectedId] = useState("iran-escalation-monitor");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [notice, setNotice] = useState("");
  const [configOpen, setConfigOpen] = useState(false);
  const [configLoading, setConfigLoading] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);
  const [configError, setConfigError] = useState("");
  const [configPayload, setConfigPayload] = useState<CollectorConfigPayload | null>(null);
  const [configDraft, setConfigDraft] = useState<CollectorConfigDraft>({ intervalMinutes: 1440, excludeIds: ["taco-monitor"], allowPartial: false });

  useEffect(() => {
    let cancelled = false;
    const loadSummary = () => api<CollectorRuntimePayload>("/collector/summary")
      .then((payload) => {
        if (cancelled) return;
        const activeWithoutResults = payload.available && payload.run_status === "running" && !payload.collectors.length;
        if (!payload.available || (!payload.collectors.length && !activeWithoutResults)) {
          setApiState("empty");
          if (!demoMode) {
            setCollectors([]);
            setDataSource("unavailable");
          }
          return;
        }
        const mapped = payload.collectors.map(mapRuntimeCollector);
        setCollectors(mapped);
        setSelectedId((current) => mapped.some((item) => item.id === current) ? current : mapped[0]?.id || "");
        setDataSource("runtime");
        // While a manual job is active, its request id is the source of truth.
        // Do not let the last completed report overwrite the active run in the
        // monitor before that run has written results.json.
        if (!runtimeRequestId) {
          setRuntimeRunId(payload.run_id || "");
          setRuntimeUpdated(payload.updated_at || "");
          setRuntimeRunStatus(payload.run_status || "");
          setRuntimeImportStatus(payload.import_status || "");
          setRuntimeSnapshotId(payload.snapshot_id || "");
          setRuntimeImportResult(payload.import_result || null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setApiState("unavailable");
          if (!demoMode) {
            setCollectors([]);
            setDataSource("unavailable");
          }
        }
      });
    loadSummary();
    const timer = window.setInterval(loadSummary, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [runtimeRequestId, summaryRefreshKey]);

  useEffect(() => {
    let cancelled = false;
    if (!runtimeRunId) {
      setLedgerEvents([]);
      setLedgerEventsAvailable(false);
      return () => { cancelled = true; };
    }
    const query = "?limit=100";
    api<CollectorEventsPayload>(`/collector/runs/${encodeURIComponent(runtimeRunId)}/events${query}`)
      .then((payload) => {
        if (cancelled) return;
        setLedgerEvents(payload.events || []);
        setLedgerEventsAvailable(payload.available);
      })
      .catch(() => {
        if (!cancelled) {
          setLedgerEvents([]);
          setLedgerEventsAvailable(false);
        }
      });
    return () => { cancelled = true; };
  }, [runtimeRunId, selectedId]);

  useEffect(() => {
    let cancelled = false;
    if (!runtimeRunId) {
      setReviewItems([]);
      return () => { cancelled = true; };
    }
    api<{ items: CollectorReviewItem[] }>(`/collector/runs/${encodeURIComponent(runtimeRunId)}/review`)
      .then((payload) => {
        if (!cancelled) setReviewItems(payload.items || []);
      })
      .catch(() => {
        if (!cancelled) setReviewItems([]);
      });
    return () => { cancelled = true; };
  }, [runtimeRunId]);

  useEffect(() => {
    let cancelled = false;
    const loadPoolSources = () => api<CollectorSourcesPayload>("/collector/sources")
      .then((payload) => {
        if (!cancelled) setPoolSourceGroups(payload.available ? payload.collectors || [] : []);
      })
      .catch(() => {
        if (!cancelled) setPoolSourceGroups([]);
      });
    loadPoolSources();
    const timer = window.setInterval(loadPoolSources, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!runtimeRunId || !selectedId) {
      setCollectorLog(null);
      return () => { cancelled = true; };
    }
    api<CollectorLogPayload>(`/collector/runs/${encodeURIComponent(runtimeRunId)}/logs/${encodeURIComponent(selectedId)}?stream=${logStream}&tail=4000`)
      .then((payload) => {
        if (!cancelled) setCollectorLog(payload);
      })
      .catch(() => {
        if (!cancelled) setCollectorLog(null);
      });
    return () => { cancelled = true; };
  }, [runtimeRunId, selectedId, logStream]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedId) {
      setCollectorSources(null);
      return () => { cancelled = true; };
    }
    const params = new URLSearchParams({ collector_id: selectedId });
    if (runtimeRunId) params.set("run_id", runtimeRunId);
    api<CollectorSourcesPayload>(`/collector/sources?${params.toString()}`)
      .then((payload) => {
        if (!cancelled) setCollectorSources(payload.available ? payload.collectors[0] || null : null);
      })
      .catch(() => {
        if (!cancelled) setCollectorSources(null);
      });
    return () => { cancelled = true; };
  }, [selectedId, runtimeRunId]);

  useEffect(() => {
    if (!runtimeRequestId) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = () => api<CollectorJobResponse>(`/collector/jobs/${encodeURIComponent(runtimeRequestId)}`)
      .then((payload) => {
        if (cancelled) return;
        setRuntimeRunId(payload.run_id);
        setRuntimeRunStatus(payload.status);
        setRuntimeImportStatus(payload.import_status || "pending");
        setRuntimeSnapshotId(payload.snapshot_id || "");
        setRuntimeImportResult(null);
        if (["queued", "running"].includes(payload.status)) {
          timer = window.setTimeout(poll, 2000);
        } else {
          setRuntimeRequestId("");
        }
      })
      .catch(() => {
        if (!cancelled) timer = window.setTimeout(poll, 3000);
      });
    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [runtimeRequestId]);

  useEffect(() => {
    let cancelled = false;
    const loadHistory = () => api<CollectorRunsPayload>("/collector/runs?limit=20")
      .then((payload) => {
        if (!cancelled) setHistoryRuns(payload.runs);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setHistoryLoaded(true);
      });
    loadHistory();
    const timer = window.setInterval(loadHistory, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadSnapshots = () => api<SnapshotSummariesPayload>("/collector/snapshots")
      .then((payload) => {
        if (!cancelled) setSnapshotSummaries(payload.snapshots || []);
      })
      .catch(() => {
        if (!cancelled) setSnapshotSummaries([]);
      });
    loadSnapshots();
    const timer = window.setInterval(loadSnapshots, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const suffix = runtimeRunId ? `?run_id=${encodeURIComponent(runtimeRunId)}` : "";
    api<CollectorSourcesPayload>(`/collector/sources${suffix}`)
      .then((payload) => {
        if (!cancelled) setSourceGroups(payload.available ? payload.collectors || [] : []);
      })
      .catch(() => {
        if (!cancelled) setSourceGroups([]);
      });
    return () => { cancelled = true; };
  }, [runtimeRunId]);

  useEffect(() => {
    setWorkingSnapshotId(selectedSnapshotId);
  }, [selectedSnapshotId]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return collectors.filter((collector) => {
      const matchesStatus = statusFilter === "all" || collector.status === statusFilter;
      const matchesGroup = groupFilter === "全部分组" || collector.group === groupFilter;
      const matchesQuery = !normalized || `${collector.id} ${collector.name}`.toLowerCase().includes(normalized);
      return matchesStatus && matchesGroup && matchesQuery;
    });
  }, [collectors, groupFilter, query, statusFilter]);

  const selected = collectors.find((collector) => collector.id === selectedId) || collectors[0] || null;
  const runtimeFailedCount = collectors.filter((collector) => collector.status === "failed").length;
  const overallHealth = dataSource === "runtime" ? (runtimeFailedCount ? "需关注" : "正常") : dataSource === "demo" ? "演示" : "不可用";
  const overallHealthNote = dataSource === "runtime" ? `${runtimeFailedCount} 个失败 · 以最近报告为准` : dataSource === "demo" ? "显式演示模式" : "未读取到持久化运行报告";
  const runtimeStatusLabel = ({ success: "已完成", partial: "部分完成", failed: "失败", running: "运行中" } as Record<string, string>)[runtimeRunStatus] || "报告已落盘";
  const importStatusLabel = ({ succeeded: "已导入", failed: "导入失败", skipped_collection_status: "因采集状态跳过", skipped_output_gate: "输出门禁跳过", skipped_no_results: "无产物跳过", pending: "等待导入", not_requested: "未请求" } as Record<string, string>)[runtimeImportStatus] || "待确认";
  const runtimeTriggerLabel = dataSource === "runtime" ? "运行报告" : dataSource === "demo" ? "显式演示" : "不可用";
  const serviceHealthLabel = dataSource === "runtime" ? "报告可读" : dataSource === "demo" ? "演示模式" : apiState === "empty" ? "服务在线 · 无报告" : "API 不可用";
  const statusCounts = useMemo(
    () => collectors.reduce<Record<string, number>>((counts, collector) => {
      counts[collector.status] = (counts[collector.status] || 0) + 1;
      return counts;
    }, {}),
    [collectors],
  );

  function selectCollector(id: string) {
    setSelectedId(id);
    setInspectorOpen(true);
    setInspectorTab("detail");
  }

  function refreshRuntimeView() {
    setNotice("正在刷新运行报告和采集器状态…");
    setSummaryRefreshKey((value) => value + 1);
    window.setTimeout(() => setNotice(""), 3000);
  }

  async function loadHistoricalRun(runId: string) {
    setNotice(`正在读取运行 ${runId}…`);
    try {
      const payload = await api<CollectorRuntimePayload>(`/collector/runs/${encodeURIComponent(runId)}`);
      const mapped = (payload.collectors || []).map(mapRuntimeCollector);
      setCollectors(mapped);
      setSelectedId((current) => mapped.some((item) => item.id === current) ? current : mapped[0]?.id || "");
      setDataSource(payload.available && mapped.length ? "runtime" : "unavailable");
      setApiState(payload.available && mapped.length ? "unknown" : "empty");
      setRuntimeRequestId("");
      setRuntimeRunId(payload.run_id || runId);
      setRuntimeUpdated(payload.updated_at || "");
      setRuntimeRunStatus(payload.run_status || "");
      setRuntimeImportStatus(payload.import_status || "");
      setRuntimeSnapshotId(payload.snapshot_id || "");
      setRuntimeImportResult(payload.import_result || null);
      setInspectorTab("detail");
      setNotice(`已载入历史运行 ${runId}，来源状态已按该批次的采集器范围过滤。`);
      window.setTimeout(() => setNotice(""), 5000);
    } catch (error) {
      setNotice(`历史运行读取失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  async function chooseWorkingSnapshot(snapshotId: string) {
    setNotice(`正在保存工作快照 ${shortId(snapshotId)}…`);
    try {
      await apiPut<{ snapshot_id: string }>("/workspace-selection", { snapshot_id: snapshotId });
      setWorkingSnapshotId(snapshotId);
      onWorkingSnapshotChange?.(snapshotId);
      setNotice(`已将 ${shortId(snapshotId)} 设为工作快照，问答和简报将使用该版本。`);
      window.setTimeout(() => setNotice(""), 5000);
    } catch (error) {
      setNotice(`工作快照保存失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  async function queueRun(ids: string[], label: string) {
    setNotice(`正在提交“${label}”…`);
    try {
      const response = await api<CollectorJobResponse>("/collector/runs", { ids, retry_attempts: 2, retry_backoff_sec: 1, auto_import: true });
      setRuntimeRunId(response.run_id);
      setRuntimeRequestId(response.request_id);
      setRuntimeRunStatus("running");
      setRuntimeImportStatus(response.import_status || "pending");
      setRuntimeSnapshotId(response.snapshot_id || "");
      setRuntimeImportResult(null);
      setDataSource("runtime");
      setNotice(`已提交“${label}”，运行号 ${response.run_id}`);
    } catch (error) {
      setNotice(`提交“${label}”失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  async function retryCurrentRun() {
    if (!runtimeRunId) {
      setNotice("当前没有可重试的运行记录。");
      return;
    }
    setNotice("正在提交失败采集器重试…");
    try {
      const response = await api<CollectorJobResponse>(`/collector/runs/${encodeURIComponent(runtimeRunId)}/retry`, {});
      setRuntimeRunId(response.run_id);
      setRuntimeRequestId(response.request_id);
      setRuntimeRunStatus("running");
      setRuntimeImportStatus(response.import_status || "pending");
      setRuntimeSnapshotId(response.snapshot_id || "");
      setRuntimeImportResult(null);
      setNotice(`已提交失败项重试，运行号 ${response.run_id}`);
    } catch (error) {
      setNotice(`重试提交失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  function reviewStatus(itemId: string): ReviewStatus {
    return reviewItems.find((item) => item.item_id === itemId)?.status || "pending";
  }

  async function updateReview(itemId: string, status: ReviewStatus) {
    if (!runtimeRunId) {
      setNotice("当前没有可记录核对结果的运行批次。");
      return;
    }
    const previous = reviewItems;
    setReviewItems((items) => items.map((item) => item.item_id === itemId ? { ...item, status } : item));
    try {
      const payload = await apiPut<{ review: CollectorReviewItem }>(`/collector/runs/${encodeURIComponent(runtimeRunId)}/review/${encodeURIComponent(itemId)}`, {
        status,
        reviewer: "本地用户",
        reason: status === "accepted" ? "人工核对通过" : status === "rejected" ? "人工核对未通过" : "等待核对",
      });
      setReviewItems((items) => items.map((item) => item.item_id === itemId ? payload.review : item));
      setNotice(`${itemId} 已记录为 ${status === "accepted" ? "已通过" : status === "rejected" ? "需处理" : "待核对"}`);
      window.setTimeout(() => setNotice(""), 3000);
    } catch (error) {
      setReviewItems(previous);
      setNotice(`核对结果保存失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  async function openPipelineDetail(kind: "candidate" | "import") {
    if (!runtimeRunId) {
      setNotice("当前没有可查看的运行批次。");
      return;
    }
    try {
      const path = kind === "candidate" ? "candidate-audit" : "import-detail";
      const payload = await api<Omit<PipelineAuditDetail, "kind" | "label">>(`/collector/runs/${encodeURIComponent(runtimeRunId)}/${path}`);
      setPipelineDetail({ ...payload, kind, label: kind === "candidate" ? "候选加工审计" : "统一导入明细" });
    } catch (error) {
      setNotice(`明细读取失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  async function decideGate(decision: "approved" | "rejected") {
    if (!runtimeRunId) return;
    try {
      await api(`/collector/runs/${encodeURIComponent(runtimeRunId)}/gate`, {
        decision,
        reviewer: "本地用户",
        reason: decision === "approved" ? "允许本批次进入导入" : "阻止本批次进入导入",
      });
      const status: ReviewStatus = decision === "approved" ? "accepted" : "rejected";
      setReviewItems((items) => items.map((item) => item.item_id === "gate" ? { ...item, status } : item));
      setNotice(decision === "approved" ? "发布门禁已批准；可以继续重试导入。" : "发布门禁已拒绝；当前工作快照不会改变。");
      window.setTimeout(() => setNotice(""), 4500);
    } catch (error) {
      setNotice(`门禁决策保存失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  async function retryImport(allowPartialImport = false) {
    if (!runtimeRunId) return;
    setNotice("正在仅重试导入，不重新执行采集器…");
    try {
      const payload = await api<CollectorImportRetryResponse>(`/collector/runs/${encodeURIComponent(runtimeRunId)}/import-retry`, { allow_partial_import: allowPartialImport });
      setRuntimeImportStatus(payload.import_status || "succeeded");
      setRuntimeSnapshotId(payload.snapshot_id || "");
      setSummaryRefreshKey((value) => value + 1);
      setNotice(`导入已完成${payload.snapshot_id ? `，快照 ${shortId(payload.snapshot_id)}` : ""}。`);
      window.setTimeout(() => setNotice(""), 5000);
    } catch (error) {
      setNotice(`仅重试导入失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  function generateSnapshotFromPool() {
    if (!runtimeRunId) {
      setNotice("当前没有可生成快照的采集批次。");
      return;
    }
    if (runtimeRunStatus === "failed") {
      setNotice("当前批次采集失败，不能生成快照；请先重新执行采集并通过发布门禁。");
      return;
    }
    if (runtimeRunStatus === "partial" && reviewStatus("gate") !== "accepted") {
      setNotice("本批次存在失败采集器，请先在当前运行流程批准输出门禁。");
      return;
    }
    void retryImport(runtimeRunStatus === "partial");
  }

  async function openConfig() {
    setConfigOpen(true);
    setConfigLoading(true);
    setConfigError("");
    try {
      const payload = await api<CollectorConfigPayload>("/collector/config");
      setConfigPayload(payload);
      setConfigDraft({
        intervalMinutes: Math.max(1, Math.round(payload.config.interval_sec / 60)),
        excludeIds: payload.config.exclude_ids || [],
        allowPartial: Boolean(payload.config.allow_partial_import),
      });
    } catch (error) {
      setConfigError(error instanceof Error ? error.message : "配置读取失败");
    } finally {
      setConfigLoading(false);
    }
  }

  async function saveConfig() {
    if (!configPayload) return;
    setConfigSaving(true);
    setConfigError("");
    try {
      const response = await apiPut<{ config: CollectorConfig; applied: boolean; message: string }>("/collector/config", {
        interval_sec: Math.round(configDraft.intervalMinutes * 60),
        exclude_ids: configDraft.excludeIds,
        allow_partial_import: configDraft.allowPartial,
        apply: true,
      });
      setConfigOpen(false);
      setNotice(response.applied ? `调度配置已保存并应用：${response.message}` : `调度配置已保存，但后台任务未重载：${response.message}`);
      window.setTimeout(() => setNotice(""), 6500);
    } catch (error) {
      setConfigError(error instanceof Error ? error.message : "配置保存失败");
    } finally {
      setConfigSaving(false);
    }
  }

  const SelectedIcon = selected ? statusMeta[selected.status].icon : Database;
  const latestSnapshot = snapshotSummaries[0];
  const pendingSnapshotId = runtimeSnapshotId || (!runtimeRunId ? latestSnapshot?.id || "" : "");
  const sourceSummary = sourceGroups.reduce((total, item) => ({
    total: total.total + (item.summary?.total || 0),
    healthy: total.healthy + (item.summary?.healthy || 0),
    failed: total.failed + (item.summary?.failed || 0),
    empty: total.empty + Math.max(0, (item.summary?.total || 0) - (item.summary?.healthy || 0) - (item.summary?.failed || 0) - (item.summary?.stale || 0) - (item.summary?.disabled || 0)),
    missing: total.missing + (item.artifact_status === "missing" ? 1 : 0),
  }), { total: 0, healthy: 0, failed: 0, empty: 0, missing: 0 });
  const poolSourceSummary = poolSourceGroups.reduce((total, item) => ({
    total: total.total + (item.summary?.total || 0),
    healthy: total.healthy + (item.summary?.healthy || 0),
    failed: total.failed + (item.summary?.failed || 0),
    empty: total.empty + Math.max(0, (item.summary?.total || 0) - (item.summary?.healthy || 0) - (item.summary?.failed || 0) - (item.summary?.stale || 0) - (item.summary?.disabled || 0)),
    missing: total.missing + (item.artifact_status === "missing" ? 1 : 0),
  }), { total: 0, healthy: 0, failed: 0, empty: 0, missing: 0 });
  const allFetchedSourceRows = poolSourceGroups.flatMap((group) => group.sources.map((source) => ({
    ...source,
    collectorId: group.collector_id,
    updatedAt: group.updated_at,
  })));
  const fetchedSourceRows = allFetchedSourceRows.filter((source) => {
    const needle = sourceQuery.trim().toLowerCase();
    const matchesQuery = !needle || [source.id, source.publisher, source.collectorId].some((value) => String(value || "").toLowerCase().includes(needle));
    const normalizedState = source.state === "ok" ? "ok" : source.state === "disabled" ? "disabled" : source.state ? "failed" : "unknown";
    const matchesState = sourceStateFilter === "all" || normalizedState === sourceStateFilter;
    return matchesQuery && matchesState;
  });
  const selectedFetchedSource = allFetchedSourceRows.find((source) => `${source.collectorId}:${source.id}` === selectedSourceKey);

  useEffect(() => {
    let cancelled = false;
    if (!selectedFetchedSource) {
      setSourceRecordPreview(null);
      return () => { cancelled = true; };
    }
    const params = new URLSearchParams({ collector_id: selectedFetchedSource.collectorId, source_id: selectedFetchedSource.id, limit: "30" });
    api<SourceRecordsPayload>(`/collector/source-records?${params.toString()}`)
      .then((payload) => { if (!cancelled) setSourceRecordPreview(payload); })
      .catch(() => { if (!cancelled) setSourceRecordPreview(null); });
    return () => { cancelled = true; };
  }, [selectedFetchedSource?.collectorId, selectedFetchedSource?.id]);
  const sourcePoolEmptyReason = !collectors.length
    ? "当前没有可读取的采集器运行记录。"
    : poolSourceGroups.length
      ? "当前全局数据池没有可浏览的来源状态记录。"
      : "全局来源状态接口没有返回可浏览的来源产物。";
  const importCleaning = runtimeImportResult?.cleaning || (!runtimeRunId ? latestSnapshot?.cleaning : undefined) || {};
  const importRecords = runtimeImportResult?.records ?? (!runtimeRunId ? latestSnapshot?.records : undefined);
  const importObservations = runtimeImportResult?.observations ?? (!runtimeRunId ? latestSnapshot?.observations : undefined);
  const importErrors = runtimeImportResult?.errors ?? (!runtimeRunId ? latestSnapshot?.errors : undefined);
  const runCompleted = collectors.filter((item) => item.status === "success" || item.status === "partial" || item.status === "stale").length;
  const runFailed = collectors.filter((item) => item.status === "failed" || item.status === "offline").length;
  const gatePassed = runtimeImportStatus === "succeeded";
  const gateBlocked = !gatePassed && (runtimeImportStatus === "skipped_output_gate" || runtimeImportStatus === "skipped_collection_status" || runFailed > 0);
  const snapshotActionEnabled = Boolean(runtimeRunId && runtimeImportStatus !== "succeeded" && (runtimeRunStatus === "success" || (runtimeRunStatus === "partial" && reviewStatus("gate") === "accepted")));
  const snapshotNeedsReview = Boolean(pendingSnapshotId && pendingSnapshotId !== workingSnapshotId);

  function confirmWorkingSnapshot() {
    if (!pendingSnapshotId) {
      setNotice("当前没有可确认的快照。");
      return;
    }
    void chooseWorkingSnapshot(pendingSnapshotId);
  }

  function openPipelineView(view: PipelineView) {
    setPipelineView(view);
    setInspectorOpen(false);
  }

  function openRunAudit() {
    setPipelineView("pool");
    setInspectorOpen(true);
    setInspectorTab("detail");
    setNotice("已打开当前运行批次详情；可查看事件、日志、来源状态和失败原因。");
    window.setTimeout(() => setNotice(""), 4500);
  }

  function showFailedCollectors() {
    setPipelineView("run");
    setStatusFilter("failed");
    setInspectorOpen(true);
    setNotice("已切回当前运行流程，并筛选失败采集器。全局数据池本身不承载当前批次日志。");
    window.setTimeout(() => setNotice(""), 4500);
  }

  function openSourceAudit() {
    const failedCollector = collectors.find((item) => ["failed", "partial", "stale", "offline"].includes(item.status));
    const issueCollector = sourceGroups.find((item) =>
      item.summary.failed > 0 || item.summary.stale > 0 || item.summary.disabled > 0 ||
      item.summary.healthy < item.summary.total,
    );
    const targetId = failedCollector?.id || issueCollector?.collector_id;
    setPipelineView("pool");
    setStatusFilter("all");
    if (targetId) setSelectedId(targetId);
    setInspectorOpen(true);
    setInspectorTab("detail");
    setNotice(targetId ? `已进入 ${targetId} 的来源核对；右侧查看失败、过期、空响应或缺失产物。` : "已进入采集池与来源视图；选择采集器后可查看来源明细。");
    window.setTimeout(() => setNotice(""), 5000);
  }

  const pipelineStages: PipelineStage[] = [
    {
      id: "run",
      label: "运行批次",
      icon: Play,
      status: runtimeRunStatus === "running" ? "running" : runFailed ? "warning" : "success",
      summary: runtimeRunId || "等待运行批次",
      metrics: [["采集器", `${runCompleted}/${collectors.length}`], ["失败", metric(runFailed, "0")], ["触发", runtimeTriggerLabel]],
      check: "核对批次范围、排除项和运行时间",
      action: { label: "查看运行详情", handler: openRunAudit },
    },
    {
      id: "sources",
      label: "来源抓取",
      icon: Server,
      status: sourceSummary.failed ? "warning" : sourceSummary.total ? "success" : "pending",
      summary: sourceSummary.total ? `${sourceSummary.healthy}/${sourceSummary.total} 个来源正常` : "来源统计待接入",
      metrics: [["成功", sourceSummary.total ? `${sourceSummary.healthy}` : "未记录"], ["失败", sourceSummary.total ? `${sourceSummary.failed}` : "未记录"], ["空响应", sourceSummary.total ? `${sourceSummary.empty}` : "未记录"]],
      check: "核对失败来源、空响应和连续失败次数",
      action: { label: "进入来源核对", handler: openSourceAudit },
    },
    {
      id: "candidates",
      label: "候选加工",
      icon: ListChecks,
      status: importCleaning.raw_records ? "success" : "pending",
      summary: importCleaning.raw_records ? `${metric(importCleaning.raw_records)} 条进入统一导入` : "本次候选分层未记录",
      metrics: [["原始候选", metric(importCleaning.raw_records)], ["主题/桶通过", "未记录"], ["采集器内去重", "未记录"]],
      check: "核对窗口过滤、主题命中和拒绝候选审计",
      action: { label: "查看过滤审计", handler: () => void openPipelineDetail("candidate") },
    },
    {
      id: "gate",
      label: "输出检查",
      icon: ShieldCheck,
      status: gatePassed ? "success" : gateBlocked ? "blocked" : "pending",
      summary: gatePassed ? "允许进入数据池" : gateBlocked ? "未允许进入数据池" : "等待检查",
      metrics: [["采集状态", runtimeStatusLabel], ["进入数据池", gatePassed ? "允许" : "暂停"], ["原因", importStatusLabel]],
      check: "确认失败或不完整数据是否可以继续",
      action: { label: "查看输出检查", handler: () => openPipelineView("run") },
    },
    {
      id: "import",
      label: "统一导入",
      icon: Database,
      status: importRecords ? "success" : "pending",
      summary: importRecords ? `${metric(importRecords)} 条标准化记录` : "等待导入统计",
      metrics: [["原始记录", metric(importCleaning.raw_records)], ["标准化记录", metric(importRecords)], ["全局重复", metric(importCleaning.duplicates_merged)]],
      check: "核对重复合并、无效记录和指标数量",
      action: { label: "查看导入明细", handler: () => void openPipelineDetail("import") },
    },
    {
      id: "snapshot",
      label: "快照",
      icon: Archive,
      status: pendingSnapshotId ? "warning" : "pending",
      summary: pendingSnapshotId ? `${runtimeImportResult?.created === false ? "复用" : "可用"} ${shortId(pendingSnapshotId)}` : "未生成",
      metrics: [["记录", metric(importRecords)], ["指标", metric(importObservations)], ["错误", metric(importErrors, "0")]],
      check: "核对快照统计、错误和来源覆盖",
      action: { label: "查看版本谱系", handler: () => openPipelineView("snapshots") },
    },
    {
      id: "working",
      label: "工作快照",
      icon: FileCheck2,
      status: workingSnapshotId ? "success" : "warning",
      summary: workingSnapshotId ? `当前 ${shortId(workingSnapshotId)}` : "尚未人工确认",
      metrics: [["当前版本", shortId(workingSnapshotId)], ["待确认", snapshotNeedsReview ? "有" : "无"], ["切换方式", "人工"]],
      check: "确认这版数据可以用于问答和简报",
      action: { label: "选择工作快照", handler: () => openPipelineView("snapshots") },
    },
    {
      id: "analysis",
      label: "问答 / 简报",
      icon: GitBranch,
      status: workingSnapshotId ? "success" : "pending",
      summary: workingSnapshotId ? "可开始分析" : "等待工作快照",
      metrics: [["问答", workingSnapshotId ? "可用" : "锁定"], ["简报", workingSnapshotId ? "可生成" : "锁定"], ["依据", shortId(workingSnapshotId)]],
      check: "核对问题范围、引用来源和数据时效",
    },
  ];

  // 运行页只负责回答“这次抓取跑到哪里、哪里需要处理”。
  // 导入、快照和问答/简报属于后续工作区，分别在采集池和快照视图中展示。
  const collectionStages = pipelineStages.filter((stage) => stage.id === "run" || stage.id === "sources" || (stage.id === "gate" && gateBlocked));

  return (
    <section className="collector-monitor" aria-label="采集管线监控">
      <div className="collector-command-bar">
        <div className="collector-command-title">
          <span className="collector-kicker"><Activity size={14} /> 运行监控</span>
          <h1>采集管线监控</h1>
          <p>以采集批次为主线：选择批次、查看日志，再进入采集数据或配置定时任务。</p>
        </div>
        <div className="collector-command-actions">
          <span className="collector-demo-badge">
            {dataSource === "runtime"
              ? `运行账本 · ${runtimeRunId || "最近一次报告"}`
              : dataSource === "demo"
                ? "显式演示模式 · VITE_COLLECTOR_DEMO=true"
                : apiState === "empty"
                  ? "暂无持久化运行报告"
                  : "运行 API 不可用 · 未使用演示数据"}
          </span>
          <span className={`collector-health ${dataSource === "runtime" ? "" : "is-muted"}`}><i /> {serviceHealthLabel}</span>
          <span className="collector-updated">{runtimeUpdated ? `报告更新于 ${runtimeUpdated.replace("T", " ").slice(0, 16)}` : "报告更新时间未提供"}</span>
          <button onClick={refreshRuntimeView}><RefreshCw size={14} /> 刷新</button>
          <button onClick={openConfig}><Settings2 size={14} /> 定时任务配置</button>
          <button className="primary" onClick={() => queueRun([], "立即执行全量采集")}><Play size={14} /> 立即执行</button>
        </div>
      </div>

      {notice && <div className="collector-notice" role="status">{notice}<button aria-label="关闭提示" onClick={() => setNotice("")}><X size={14} /></button></div>}

      <div className="collector-summary-strip">
          <div><span>当前批次</span><strong>{runtimeRunId ? shortId(runtimeRunId) : "未选择"}</strong><small><i className={dataSource === "runtime" ? "report-dot" : "offline-dot"} /> {dataSource === "runtime" ? runtimeStatusLabel : "等待采集批次"}</small></div>
        <div><span>整体健康</span><strong className={overallHealth === "正常" ? "summary-value healthy" : "summary-value warning"}>{overallHealth}</strong><small>{overallHealthNote}</small></div>
        <div><span>最近快照</span><strong>{runtimeSnapshotId ? `${runtimeSnapshotId.slice(0, 10)}…` : (dataSource === "runtime" ? importStatusLabel : "尚未接入")}</strong><small>{dataSource === "runtime" ? `导入状态 · ${importStatusLabel}` : "没有运行报告可关联"}</small></div>
          <div><span>最近更新</span><strong>{runtimeUpdated ? runtimeUpdated.replace("T", " ").slice(0, 16) : "待接入"}</strong><small>{collectors.length ? `${collectors.length} 个采集器 · 以当前批次为准` : "等待真实采集数据"}</small></div>
      </div>

      <nav className="collector-view-switcher" aria-label="管线视图">
        <button className={pipelineView === "run" ? "selected" : ""} onClick={() => openPipelineView("run")}><Activity size={14} /> 当前运行流程</button>
        <button className={pipelineView === "pool" ? "selected" : ""} onClick={() => openPipelineView("pool")}><Server size={14} /> 浏览已抓取数据</button>
      </nav>

      {pipelineView === "run" && <section className="pipeline-run-view" aria-label="当前运行流程">
        <div className="pipeline-view-heading">
          <div><span className="collector-kicker"><Activity size={14} /> 批次工作台 · {runtimeRunId || "尚未选择批次"}</span><h2>采集批次与日志</h2><p>左侧选择批次，中间选择本批次采集源，右侧查看选中来源的状态、事件与日志。</p></div>
          <div className="pipeline-heading-actions"><span className={`pipeline-state-pill ${gateBlocked ? "warning" : gatePassed ? "success" : "pending"}`}>{gateBlocked ? "存在门禁阻断" : gatePassed ? "流程已落盘" : "等待运行"}</span><button className="batch-command" title="打开已抓取数据池，不会重新启动采集" onClick={() => openPipelineView("pool")}>浏览已抓取数据 <ChevronRight size={14} /></button>{gateBlocked && <><button className="batch-command approve" title="记录门禁批准，允许本批次继续进入后续导入/快照流程" onClick={() => void decideGate("approved")}>批准继续</button><button className="batch-command reject" title="记录门禁阻断，停止本批次进入后续导入/快照流程" onClick={() => void decideGate("rejected")}>阻断本批次</button></>}</div>
        </div>

        <section className="batch-log-workspace" aria-label="运行批次与日志">
          <div className="batch-list-panel">
            <div className="batch-panel-heading"><span className="collector-kicker"><History size={13} /> 批次列表</span><strong>{historyRuns.length ? `${historyRuns.length} 个批次` : "暂无批次"}</strong></div>
            <div className="batch-list">
              {historyRuns.map((run) => <button key={run.run_id} className={run.run_id === runtimeRunId ? "selected" : ""} onClick={() => void loadHistoricalRun(run.run_id)}>
                <span><b>{run.run_id}</b><small>{runKindLabel(run.run_kind)}</small></span><em className={run.failed_count ? "bad" : "ok"}>{run.failed_count ? `${run.failed_count} 个失败` : "全部完成"}</em><small>{run.updated_at ? run.updated_at.replace("T", " ").slice(0, 16) : "时间未提供"}</small>
              </button>)}
              {!historyRuns.length && <div className="batch-list-empty">正在等待采集批次…</div>}
            </div>
          </div>
          <div className="batch-source-panel">
            <div className="batch-panel-heading"><span className="collector-kicker"><Server size={13} /> 本批次采集源</span><strong>{collectors.length} 个</strong></div>
            <div className="batch-source-list">
              {collectors.map((collector) => { const MetaIcon = statusMeta[collector.status].icon; return <button key={collector.id} className={collector.id === selectedId ? "selected" : ""} onClick={() => selectCollector(collector.id)}><span className="batch-source-status"><MetaIcon size={14} /></span><span><b>{collector.name}</b><small>{collector.id} · {collector.stage}</small></span><em className={statusMeta[collector.status].className}>{statusMeta[collector.status].label}</em><small>{collector.outputs} 个产物 · {collector.finishedAt}</small></button>; })}
              {!collectors.length && <div className="batch-list-empty">该批次没有采集器记录。</div>}
            </div>
          </div>
          <div className="batch-detail-panel">
            <div className="batch-panel-heading"><span className="collector-kicker"><ListChecks size={13} /> 源详情 · {selected?.name || "未选择"}</span><strong>{selected ? statusMeta[selected.status].label : "—"}</strong></div>
            {selected ? <>
              <div className="batch-detail-summary"><span>阶段 <b>{selected.stage}</b></span><span>产物 <b>{selected.outputs}</b></span><span>耗时 <b>{selected.elapsed}</b></span><span>来源 <b>{selected.sourceHealth}</b></span></div>
              <div className="batch-source-detail"><span>错误信息</span><strong className={selected.errorMessage ? "bad" : "ok"}>{selected.errorType || "无错误"}</strong><p>{selected.errorMessage || "本次采集没有记录错误。"}</p></div>
              <div className="batch-event-list">{ledgerEvents.filter((event) => !event.collector_id || event.collector_id === selected.id).map((event) => <button key={event.id}><span><b>{event.event_type}</b><small>{event.created_at.replace("T", " ").slice(0, 19)}</small></span><em className={event.status === "failed" ? "bad" : event.status === "succeeded" ? "ok" : ""}>{eventStatusLabel(event.status)}</em><p>{event.message || "已记录"}</p></button>)}{!ledgerEvents.length && <div className="batch-list-empty">暂无事件记录。</div>}</div>
              <pre className="batch-log-tail">{collectorLog?.available ? collectorLog.content || "（日志为空）" : "当前源暂无可读日志尾部"}</pre>
            </> : <div className="batch-list-empty">从中间列表选择一个采集源查看详情。</div>}
          </div>
        </section>

      </section>}

      {pipelineView === "snapshots" && <section className="pipeline-snapshot-view" aria-label="快照版本谱系">
        <div className="pipeline-view-heading"><div><span className="collector-kicker"><History size={14} /> 数据版本</span><h2>快照版本</h2><p>这里浏览不可变版本；调整过滤条件不会修改旧版本，而是生成新的派生快照。</p></div><div className="pipeline-heading-actions"><span className="pipeline-state-pill pending">当前工作快照：{workingSnapshotId ? shortId(workingSnapshotId) : "未选择"}</span><button onClick={() => openPipelineView("run")}>返回抓取进度 <ChevronRight size={14} /></button></div></div>
        <div className="snapshot-lineage">
          {snapshotSummaries.length ? snapshotSummaries.map((snapshot, index) => {
            const isPending = snapshot.id === pendingSnapshotId && snapshot.id !== workingSnapshotId;
            const isWorking = snapshot.id === workingSnapshotId;
            return <article key={snapshot.id} className={`snapshot-lineage-item ${isPending ? "pending" : ""} ${isWorking ? "working" : ""}`}>
              <div className="snapshot-lineage-marker"><span>{index + 1}</span></div>
              <div className="snapshot-lineage-content"><div className="snapshot-lineage-heading"><div><strong>{shortId(snapshot.id)}</strong><small>{snapshot.created_at.replace("T", " ").slice(0, 16)}</small></div><div className="snapshot-badges">{isWorking && <em className="working">当前工作</em>}{isPending && <em className="pending">待确认</em>}</div></div>
                <div className="snapshot-stat-grid"><div><span>记录</span><b>{metric(snapshot.records)}</b></div><div><span>指标</span><b>{metric(snapshot.observations)}</b></div><div><span>来源</span><b>{metric(snapshot.sources)}</b></div><div><span>错误</span><b>{metric(snapshot.errors, "0")}</b></div><div><span>新记录</span><b>{metric(snapshot.import_stats?.new)}</b></div><div><span>修订</span><b>{metric(snapshot.import_stats?.revised)}</b></div></div>
                <div className="snapshot-cleaning-note">清洗：原始 {metric(snapshot.cleaning?.raw_records)} · 合并重复 {metric(snapshot.cleaning?.duplicates_merged)} · 无效 {metric(snapshot.cleaning?.invalid_records, "0")}</div>
                <div className="pipeline-action-row"><button className={isWorking ? "primary" : ""} onClick={() => void chooseWorkingSnapshot(snapshot.id)}><FileCheck2 size={14} /> {isWorking ? "当前工作快照" : "设为工作快照"}</button><button onClick={() => onSnapshotInspect?.(snapshot.id)}>查看 / 调整范围</button></div>
              </div>
            </article>;
          }) : <div className="pipeline-empty-state"><Database size={22} /><strong>暂无快照版本</strong><span>采集通过导入门禁后，版本会出现在这里。</span></div>}
        </div>
        <div className="pipeline-audit-panel snapshot-review-panel"><div className="pipeline-panel-heading"><div><span className="collector-kicker"><ShieldCheck size={13} /> 人工选择工作版本</span><h3>确认前建议核对</h3></div><span className="pipeline-panel-note">选择会持久化到 SQLite 工作区状态</span></div><div className="pipeline-check-list compact"><label><input type="checkbox" checked={reviewStatus("snapshot") === "accepted"} onChange={(event) => void updateReview("snapshot", event.target.checked ? "accepted" : "pending")} /><span><b>来源覆盖可以接受</b><small>确认关键来源没有大面积失败或过期。</small></span><em>{reviewStatus("snapshot") === "accepted" ? "已通过" : "待核对"}</em></label><label><input type="checkbox" checked={reviewStatus("import") === "accepted"} onChange={(event) => void updateReview("import", event.target.checked ? "accepted" : "pending")} /><span><b>清洗统计可解释</b><small>确认重复合并和无效记录数量符合预期。</small></span><em>{reviewStatus("import") === "accepted" ? "已通过" : "待核对"}</em></label><label><input type="checkbox" checked={reviewStatus("candidates") === "accepted"} onChange={(event) => void updateReview("candidates", event.target.checked ? "accepted" : "pending")} /><span><b>数据范围适合研究</b><small>确认当前快照可用于问答、指标和简报。</small></span><em>{reviewStatus("candidates") === "accepted" ? "已通过" : "待核对"}</em></label></div><div className="pipeline-action-row"><button className="primary" onClick={confirmWorkingSnapshot} disabled={!pendingSnapshotId || !snapshotNeedsReview}>确认当前待选版本</button><button onClick={() => openPipelineView("pool")}>查看采集池</button></div></div>
      </section>}

      {pipelineView === "pool" && <section className="pipeline-pool-view" aria-label="全局已抓取数据池">
      <div className="collector-pool-strip"><div><span>全局已抓取数据池</span><strong>{allFetchedSourceRows.length ? `${allFetchedSourceRows.length} 条来源记录` : "暂无可浏览来源记录"}</strong><small>这里浏览所有已落盘来源，不随当前运行批次切换；筛选只影响列表。</small></div><div><span>来源状态</span><strong>{poolSourceSummary.total ? `${poolSourceSummary.healthy} / ${poolSourceSummary.total}` : "未记录"}</strong><small>正常来源 · 全局数据池</small></div><div><span>失败 / 空响应 / 缺失</span><strong className={poolSourceSummary.failed || poolSourceSummary.missing ? "warning" : ""}>{poolSourceSummary.total ? `${poolSourceSummary.failed} / ${poolSourceSummary.empty} / ${poolSourceSummary.missing}` : "未记录"}</strong><small>失败、空响应、未提供产物</small></div><div className="collector-pool-actions"><button onClick={showFailedCollectors} title="返回当前运行流程并筛选失败采集器">查看失败来源</button><button disabled={!runtimeRunId || runtimeRunStatus === "running"} onClick={() => void retryCurrentRun()} title={runtimeRunId ? "重新提交当前批次中的失败采集器" : "当前没有可重试的批次"}>重试当前批次</button><button className="primary" disabled={!snapshotActionEnabled} title={runtimeImportStatus === "succeeded" ? "当前批次已经生成快照" : snapshotActionEnabled ? "使用当前批次已发布产物生成快照；不会把全局列表筛选条件写入快照" : "当前批次未通过采集/发布门禁，暂不能生成快照"} onClick={generateSnapshotFromPool}>{runtimeImportStatus === "succeeded" ? "快照已生成" : "从当前批次生成快照"}</button></div></div>

      <div className="fetched-data-layout">
      <section className="fetched-data-browser" aria-label="已抓取数据浏览">
        <div className="fetched-data-heading"><div><span className="collector-kicker"><Database size={13} /> 全局数据池 · 不限批次</span><h2>浏览已抓取来源记录</h2><p>列表来自所有已发布采集产物；第一列是来源稳定标识（用于追溯，不是文件夹名称）。当前运行批次只影响上方“补采”和快照操作。</p></div><div className="fetched-data-filters"><label className="fetched-data-filter"><Search size={14} /><input value={sourceQuery} onChange={(event) => setSourceQuery(event.target.value)} placeholder="搜索来源、发布方或采集器" aria-label="搜索已抓取数据" /></label><select value={sourceStateFilter} onChange={(event) => setSourceStateFilter(event.target.value as SourceStateFilter)} aria-label="来源状态筛选"><option value="all">全部来源状态</option><option value="ok">正常</option><option value="failed">失败 / 空响应</option><option value="disabled">已停用</option><option value="unknown">未记录</option></select></div></div>
        <div className="fetched-data-table" role="table" aria-label="已抓取来源列表">
          <div className="fetched-data-row heading" role="row"><span>来源标识 / 发布方</span><span>采集器</span><span>采集统计数</span><span>最近抓取</span><span>状态</span></div>
          {fetchedSourceRows.slice(0, 80).map((source) => { const sourceKey = `${source.collectorId}:${source.id}`; return <button key={sourceKey} className={`fetched-data-row ${sourceKey === selectedSourceKey ? "selected" : ""}`} role="row" onClick={() => setSelectedSourceKey(sourceKey)}>
            <span><b>{source.id}</b><small>{source.publisher || "发布方未记录"}</small></span><span>{source.collectorId}</span><span>{source.entries ?? source.kept ?? "未记录"}</span><span>{source.updatedAt ? source.updatedAt.replace("T", " ").slice(0, 16) : "未记录"}</span><em className={source.state === "ok" ? "ok" : source.state === "disabled" ? "disabled" : "bad"}>{source.state || "未知"}</em>
          </button>; })}
          {!fetchedSourceRows.length && <div className="fetched-data-empty"><Database size={20} /><strong>{allFetchedSourceRows.length ? "没有符合当前筛选条件的来源记录" : "全局数据池暂无可浏览的来源记录"}</strong><span>{allFetchedSourceRows.length ? "调整搜索词或来源状态筛选。" : sourcePoolEmptyReason}</span>{!allFetchedSourceRows.length && <div className="fetched-data-empty-actions"><button onClick={() => openPipelineView("run")}>查看当前批次日志</button><button className="primary" onClick={() => queueRun([], "重新执行采集")}>重新执行采集</button></div>}</div>}
        </div>
        {allFetchedSourceRows.length > 0 && <small className="fetched-data-more">当前显示 {Math.min(fetchedSourceRows.length, 80)} / {allFetchedSourceRows.length} 条来源记录；筛选仅作用于浏览。</small>}
      </section>

      {selectedFetchedSource && <section className="fetched-data-detail" aria-label="来源记录详情"><div><span className="collector-kicker"><ListChecks size={13} /> 来源记录详情</span><h3>{selectedFetchedSource.id}</h3><p>{selectedFetchedSource.publisher || "发布方未记录"} · 采集器 {selectedFetchedSource.collectorId}</p></div><div className="fetched-data-detail-grid"><span>状态 <b>{selectedFetchedSource.state || "未记录"}</b></span><span>采集统计 <b>{selectedFetchedSource.entries ?? selectedFetchedSource.kept ?? "未记录"}</b></span><span>可浏览条目 <b>{sourceRecordPreview ? sourceRecordPreview.total : "读取中…"}</b></span><span>最近抓取 <b>{selectedFetchedSource.updatedAt ? selectedFetchedSource.updatedAt.replace("T", " ").slice(0, 16) : "未记录"}</b></span><span>连续失败 <b>{selectedFetchedSource.consecutive_failures ?? "未记录"}</b></span></div><p className="fetched-data-detail-error">{selectedFetchedSource.error || "采集统计是来源返回的抓取数量；可浏览条目是去重、过滤后已经落盘的标准化记录，两者可能不同。"}</p><div className="fetched-record-preview"><div className="fetched-record-preview-heading"><span>已落盘条目预览</span><small>{sourceRecordPreview ? `已加载 ${sourceRecordPreview.records.length} / 共 ${sourceRecordPreview.total} 条` : "正在读取…"}</small></div>{sourceRecordPreview?.records.map((record, index) => <article key={String(record.id || index)}><strong>{record.title || "无标题记录"}</strong><span>{record.publisher || selectedFetchedSource.publisher || "发布方未记录"} · {record.published || record.fetched_at || "时间未记录"}</span><p>{record.summary || "无摘要"}</p>{record.url && <a href={record.url} target="_blank" rel="noreferrer">查看原文</a>}</article>)}{sourceRecordPreview && !sourceRecordPreview.records.length && <div className="fetched-record-empty">该来源没有可读取的条目文件。</div>}</div></section>}
      </div>

      <div className="collector-mobile-toolbar">
        <button onClick={() => setFiltersOpen(true)}><Filter size={15} /> 筛选</button>
        <span>{filtered.length} 个采集器</span>
        <button disabled={!selected} onClick={() => selected && setInspectorOpen(true)}><ChevronRight size={15} /> 当前详情</button>
      </div>

      <div className="collector-workspace">
        <aside className={`collector-filters ${filtersOpen ? "is-open" : ""}`}>
          <div className="collector-filter-heading">
            <div><span className="collector-kicker"><Filter size={13} /> 定位</span><h2>采集器筛选</h2></div>
            <button className="collector-mobile-close" aria-label="关闭筛选" onClick={() => setFiltersOpen(false)}><X size={17} /></button>
          </div>
          <label className="collector-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索来源、主题或采集器" aria-label="搜索来源、主题或采集器" /></label>
          <div className="collector-filter-group">
            <span>运行状态</span>
            {statusOrder.map((status) => {
              const count = status === "all" ? collectors.length : statusCounts[status] || 0;
              const label = status === "all" ? "全部状态" : statusMeta[status].label;
              return <button key={status} className={statusFilter === status ? "selected" : ""} onClick={() => setStatusFilter(status)}><span>{label}</span><b>{count}</b></button>;
            })}
          </div>
          <div className="collector-filter-group">
            <span>采集器分组</span>
            {groupOptions.map((group) => <button key={group} className={groupFilter === group ? "selected" : ""} onClick={() => setGroupFilter(group)}><span>{group}</span><b>{group === "全部分组" ? collectors.length : collectors.filter((collector) => collector.group === group).length}</b></button>)}
          </div>
          <div className="collector-filter-note"><Server size={14} /><p>{dataSource === "runtime" ? "状态来自最近一次持久化采集报告；它不等于当前服务实时心跳。" : dataSource === "demo" ? "当前为显式演示模式，不能用于判断真实采集状态。" : "没有可读取的持久化运行报告，已停止显示演示采集器。"}</p></div>
        </aside>

        <section className="collector-timeline" aria-label="采集器执行时间线">
          <div className="collector-timeline-heading">
            <div><span className="collector-kicker"><Clock3 size={13} /> {dataSource === "runtime" ? "采集器诊断" : "等待真实数据"}</span><h2>{dataSource === "runtime" ? "采集器运行状态" : "暂无采集器状态"} <small>{runtimeRunId || "—"}</small></h2></div>
            <div className="collector-timeline-meta"><span><i className="legend-dot running" /> 运行中</span><span><i className="legend-dot success" /> 成功</span><span><i className="legend-dot partial" /> 需关注</span><span><i className="legend-dot failed" /> 失败</span></div>
          </div>
          <div className="collector-timeline-caption"><span>{filtered.length} / {collectors.length} 个来源采集器</span><span>{runtimeUpdated ? `最近抓取 ${runtimeUpdated.replace("T", " ").slice(0, 16)}` : "等待抓取时间"}</span></div>
          <div className="collector-rows">
            {filtered.map((collector) => {
              const meta = statusMeta[collector.status];
              const Icon = meta.icon;
              return <button key={collector.id} className={`collector-row ${collector.id === selectedId ? "selected" : ""} status-${meta.className}`} onClick={() => selectCollector(collector.id)} aria-pressed={collector.id === selectedId}>
                <span className="collector-status-icon"><Icon size={17} /></span>
                <span className="collector-row-main"><strong>{collector.name}</strong><small>{collector.group} · {collector.id}</small></span>
                <span className="collector-row-stage"><small>当前阶段</small><span>{collector.stage}</span><i><b style={{ width: `${progressFor(collector.status)}%` }} /></i></span>
                <span className="collector-row-stats"><small>输出文件 {collector.outputs.toLocaleString("zh-CN")} 个</small><small>{collector.elapsed}</small></span>
                <span className="collector-row-status"><em>{meta.label}</em><small>{collector.finishedAt}</small></span>
                <ChevronRight size={16} className="collector-row-chevron" />
              </button>;
            })}
            {!filtered.length && <div className="collector-empty"><Search size={22} /><strong>{dataSource === "runtime" ? "本次报告没有匹配的采集器" : "暂无真实采集器数据"}</strong><span>{dataSource === "runtime" ? "调整状态、分组或关键词后重试。" : "启动采集服务或检查 API 连接后刷新。"}</span></div>}
          </div>
        </section>

        {selected && <aside className={`collector-inspector ${inspectorOpen ? "is-open" : ""}`} aria-label="采集器详情">
          <div className="collector-inspector-heading"><div><span className="collector-kicker"><SelectedIcon size={13} /> 选中采集器</span><h2>{selected.name}</h2><small>{selected.group} · {selected.id}</small></div><button aria-label="关闭详情" onClick={() => setInspectorOpen(false)}><X size={17} /></button></div>
          <div className={`collector-inspector-status status-${statusMeta[selected.status].className}`}><SelectedIcon size={17} /><strong>{statusMeta[selected.status].label}</strong><span>{selected.sourceHealth}</span></div>
          <div className="collector-inspector-tabs"><button className={inspectorTab === "detail" ? "selected" : ""} onClick={() => setInspectorTab("detail")}>运行详情</button><button className={inspectorTab === "history" ? "selected" : ""} onClick={() => setInspectorTab("history")}>历史记录</button><button className={inspectorTab === "resources" ? "selected" : ""} onClick={() => setInspectorTab("resources")}>相关资源</button></div>
          {inspectorTab === "detail" && <>
            <div className="collector-detail-grid"><div><span>当前步骤</span><strong>{selected.stage}</strong></div><div><span>重试次数</span><strong>{selected.attempts}</strong></div><div><span>输出文件</span><strong>{selected.outputs} 个</strong></div><div><span>运行时长</span><strong>{selected.elapsed}</strong></div></div>
            <div className="collector-detail-block"><span>最近一次成功</span><strong>{selected.lastSuccess}</strong></div>
            <div className="collector-detail-block"><span>错误信息（最近一条）</span><article className={selected.errorMessage ? "has-error" : ""}>{selected.errorType ? <b>{selected.errorType}</b> : <b>无错误</b>}<p>{selected.errorMessage || "本次执行没有记录错误。"}</p><small>{selected.finishedAt} · 日志尾部</small></article></div>
            <div className="collector-detail-block"><span>运行事件</span>{ledgerEventsAvailable && ledgerEvents.length ? <div className="collector-event-list">{ledgerEvents.slice(-4).map((event) => <article key={event.id}><b>{event.event_type}</b><p>{event.message || event.status || "已记录"}</p><small>{event.created_at.replace("T", " ").slice(0, 19)}</small></article>)}</div> : <article><b>账本事件未提供</b><p>{dataSource === "runtime" ? "该历史报告尚未关联 SQLite 运行账本。" : "演示数据尚未连接真实运行事件。"}</p></article>}</div>
            <div className="collector-detail-block"><span>日志尾部</span><div className="collector-log-toolbar"><button className={logStream === "stderr" ? "selected" : ""} onClick={() => setLogStream("stderr")}>stderr</button><button className={logStream === "stdout" ? "selected" : ""} onClick={() => setLogStream("stdout")}>stdout</button></div><pre className="collector-log-tail">{collectorLog?.available ? collectorLog.content || "（日志为空）" : "当前运行尚无可读日志尾部"}</pre></div>
            <div className="collector-detail-block"><span>来源状态</span>{collectorSources ? <><div className="collector-source-summary"><b>{collectorSources.summary.healthy} / {collectorSources.summary.total} 正常</b><small>{collectorSources.summary.failed} 失败 · {collectorSources.summary.stale} 过期 · {collectorSources.summary.disabled} 停用 · {collectorSources.updated_at ? collectorSources.updated_at.replace("T", " ").slice(0, 16) : "时间未提供"}</small></div><div className="collector-source-list">{collectorSources.sources.slice().sort((a, b) => { const rank = (state?: string) => state === "ok" ? 0 : state === "disabled" ? 1 : 2; return rank(b.state) - rank(a.state); }).slice(0, 12).map((source) => <article key={source.id}><span><b>{source.id}</b><small>{source.publisher || "未提供发布方"}</small></span><em className={source.state === "ok" ? "ok" : source.state === "disabled" ? "disabled" : "bad"}>{source.state || "未知"}</em><small>{source.error || `${source.entries ?? 0} 条 · HTTP ${source.http_status ?? "—"}`}</small></article>)}</div>{collectorSources.sources.length > 12 && <small className="collector-source-more">仅显示 12 个来源；接口返回完整来源列表。</small>}</> : <article><b>暂无来源产物</b><p>当前采集器没有可读取的 `_source_status.json`、`_fetch_stats.json` 或 `_source_health.json`。</p></article>}</div>
            <div className="collector-detail-actions"><button className="primary" onClick={() => queueRun([selected.id], `补采 ${selected.name}`)}><RotateCcw size={15} /> 补采</button><button onClick={retryCurrentRun}><RefreshCw size={15} /> 重试</button></div>
            <p className="collector-inspector-footnote">日志来自运行报告或账本事件的受限尾部，后端会脱敏 token、cookie、authorization 等字段。</p>
          </>}
          {inspectorTab === "history" && <div className="collector-history-list">
            {!historyRuns.length && <div className="collector-history-empty">{historyLoaded ? "暂无可读取的持久化运行报告" : "正在加载运行历史…"}</div>}
            {historyRuns.map((run) => <button key={run.run_id} className={run.run_id === runtimeRunId ? "selected" : ""} onClick={() => void loadHistoricalRun(run.run_id)}><span><strong>{run.run_id}</strong><small>{run.run_kind || "collector-run"}</small></span><em>{run.failed_count ? `${run.failed_count} 个失败` : "全部完成"}</em><small>{run.updated_at ? run.updated_at.replace("T", " ").slice(0, 16) : "时间未提供"}</small></button>)}
          </div>}
          {inspectorTab === "resources" && <div className="collector-resource-panel"><span>输出 manifest</span><strong>{selected.outputs} 个文件 · {selected.records.toLocaleString("zh-CN")} 条记录</strong><p>输出 manifest 和快照导入记录来自当前运行接口；未提供的字段不会被补造。</p></div>}
        </aside>}
      </div>
      </section>}

      {pipelineDetail && <div className="pipeline-detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setPipelineDetail(null); }}>
        <section className="pipeline-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="pipeline-detail-title">
          <div className="collector-config-heading"><div><span className="collector-kicker"><ListChecks size={14} /> 运行批次 · {shortId(pipelineDetail.run_id)}</span><h2 id="pipeline-detail-title">{pipelineDetail.label}</h2></div><button aria-label="关闭明细" onClick={() => setPipelineDetail(null)}><X size={17} /></button></div>
          {!pipelineDetail.available && <div className="pipeline-detail-empty"><AlertTriangle size={18} /><strong>本批次没有可核对明细</strong><p>{pipelineDetail.reason || "运行报告没有持久化该阶段的统计。"}</p></div>}
          {pipelineDetail.available && <>
            <div className="pipeline-detail-meta"><span>来源：{pipelineDetail.source}</span><span>状态：{pipelineDetail.run_status || runtimeRunStatus || "未记录"}</span><span>导入：{pipelineDetail.import_status || runtimeImportStatus || "未记录"}</span></div>
            {pipelineDetail.metrics && <div className="pipeline-detail-metrics">{Object.entries(pipelineDetail.metrics).map(([key, value]) => <div key={key}><span>{key}</span><b>{typeof value === "object" ? JSON.stringify(value) : String(value ?? "未记录")}</b></div>)}</div>}
            {pipelineDetail.kind === "import" && <div className="pipeline-detail-import"><p>{pipelineDetail.import_error || (pipelineDetail.snapshot_id ? `已关联快照 ${shortId(pipelineDetail.snapshot_id)}` : "本批次尚未生成快照。")}</p><div className="pipeline-action-row"><button onClick={() => void retryImport(false)} disabled={pipelineDetail.import_status === "succeeded" || runtimeRunStatus === "partial"}>{pipelineDetail.import_status === "succeeded" ? "导入已完成" : "仅重试导入"}</button>{runtimeRunStatus === "partial" && <button className="primary" onClick={() => void retryImport(true)} disabled={reviewStatus("gate") !== "accepted"}>批准后导入部分成功数据</button>}</div></div>}
          </>}
        </section>
      </div>}

      {configOpen && <div className="collector-config-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !configSaving) setConfigOpen(false); }}>
        <section className="collector-config-dialog" role="dialog" aria-modal="true" aria-labelledby="collector-config-title">
          <div className="collector-config-heading">
            <div><span className="collector-kicker"><Settings2 size={14} /> 服务级配置</span><h2 id="collector-config-title">采集调度配置</h2></div>
            <button aria-label="关闭调度配置" onClick={() => setConfigOpen(false)} disabled={configSaving}><X size={17} /></button>
          </div>
          <p className="collector-config-intro">当前调度器按全局间隔运行一批 enabled 采集器；这里的配置保存到项目目录，并可重新加载本机后台任务。</p>
          {configLoading && <div className="collector-config-loading">正在读取项目配置…</div>}
          {!configLoading && configError && <div className="collector-config-error" role="alert">{configError}</div>}
          {!configLoading && !configError && configPayload && <>
            <div className="collector-config-field">
              <label htmlFor="collector-interval">全局采集间隔（分钟）</label>
              <div className="collector-config-input-row"><input id="collector-interval" type="number" min={1} max={10080} step={1} value={configDraft.intervalMinutes} onChange={(event) => setConfigDraft((draft) => ({ ...draft, intervalMinutes: Math.min(10080, Math.max(1, Number(event.target.value) || 1)) }))} /><span>分钟</span></div>
              <small>上一轮执行完成后等待该时长，再启动下一轮；不是单个采集器的独立 cron。</small>
            </div>

            <div className="collector-config-grid">
              <article className="collector-config-card"><span>自动导入快照</span><strong>{configPayload.config.auto_import ? "开启" : "关闭"}</strong><small>采集通过输出门禁后，由服务自动导入不可变快照。</small></article>
              <article className="collector-config-card"><span>简报生成</span><strong>需要手动生成</strong><small>简报基于当前选定快照单独生成，不随每轮采集强制触发。</small></article>
            </div>

            <label className="collector-config-toggle"><input type="checkbox" checked={configDraft.allowPartial} onChange={(event) => setConfigDraft((draft) => ({ ...draft, allowPartial: event.target.checked }))} /><span><b>允许部分成功进入导入门禁</b><small>关闭时只要有采集器失败，本轮就会跳过快照导入。</small></span></label>

            <div className="collector-config-collectors"><div className="collector-config-section-title"><span>排除采集器</span><small>勾选后本轮及后续计划不执行</small></div>{configPayload.collectors.map((collector) => <label key={collector.id} className={`collector-config-collector ${collector.enabled ? "" : "disabled"}`}><input type="checkbox" disabled={!collector.enabled} checked={collector.enabled && configDraft.excludeIds.includes(collector.id)} onChange={(event) => setConfigDraft((draft) => ({ ...draft, excludeIds: event.target.checked ? [...new Set([...draft.excludeIds, collector.id])] : draft.excludeIds.filter((id) => id !== collector.id) }))} /><span><b>{collector.name}</b><small>{collector.id}{!collector.enabled ? " · 已停用" : ""}</small></span>{collector.enabled && configDraft.excludeIds.includes(collector.id) && <em>已排除</em>}</label>)}</div>

            <div className="collector-config-warning">保存并应用会重载本机后台任务；服务可能从新配置立即开始下一轮采集。配置文件：<code>{configPayload.config.path || "data/service-config/collector-settings.json"}</code></div>
            <div className="collector-config-actions"><button onClick={() => setConfigOpen(false)} disabled={configSaving}>取消</button><button className="primary" onClick={saveConfig} disabled={configSaving}>{configSaving ? "正在应用…" : "保存并应用"}</button></div>
          </>}
        </section>
      </div>}
    </section>
  );
}
