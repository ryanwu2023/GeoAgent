export type CollectorDiagnosticInput = {
  started_at?: string | null;
  finished_at?: string | null;
  last_run_at?: string | null;
  last_success?: string | null;
  last_success_at?: string | null;
  error_type?: string | null;
  error_reason?: string | null;
  error_detail?: string | null;
  error_message?: string | null;
  suggested_action?: string | null;
};

const fallbackActions: Record<string, string> = {
  TIMEOUT: "先重试一次；若仍超时，检查网络与上游状态。",
  CONFIGURATION_REQUIRED: "按日志提示补齐配置或访问密钥后单独补采。",
  UPSTREAM_UNAVAILABLE: "稍后补采；连续失败时检查上游公告或备用来源。",
  ACCESS_DENIED: "核对授权和来源访问规则，不要连续高频重试。",
  RATE_LIMITED: "等待限流窗口结束，并降低并发或增加退避时间。",
  TLS_CERTIFICATE: "核对系统时间、证书链和代理设置，不要关闭证书校验。",
  SERVICE_INTERRUPTED: "确认采集服务稳定运行后，再执行补采。",
};

export function projectCollectorDiagnosis(item: CollectorDiagnosticInput) {
  const category = item.error_type || "";
  return {
    lastRunAt: item.last_run_at || item.finished_at || item.started_at || "",
    lastSuccessAt: item.last_success_at || item.last_success || "",
    errorCategory: category,
    errorReason: item.error_reason || item.error_message || "",
    errorDetail: item.error_detail || "",
    suggestedAction: item.suggested_action || fallbackActions[category] || "查看脱敏日志和来源状态，修复首个明确错误后再补采。",
  };
}
