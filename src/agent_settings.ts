export type AgentSettings = {
  per_agent_timeout_seconds: number;
  total_timeout_seconds: number;
  max_parallel: number;
  input_price_per_million: number | null;
  output_price_per_million: number | null;
  max_cost_cny: number | null;
  cost_limit_enabled?: boolean;
  source?: string;
};

export type AgentSettingsDraft = {
  per_agent_timeout_seconds: string;
  total_timeout_seconds: string;
  max_parallel: string;
  input_price_per_million: string;
  output_price_per_million: string;
  max_cost_cny: string;
};

export function settingsToDraft(settings: AgentSettings): AgentSettingsDraft {
  const optional = (value: number | null) => value === null ? "" : String(value);
  return {
    per_agent_timeout_seconds: String(settings.per_agent_timeout_seconds),
    total_timeout_seconds: String(settings.total_timeout_seconds),
    max_parallel: String(settings.max_parallel),
    input_price_per_million: optional(settings.input_price_per_million),
    output_price_per_million: optional(settings.output_price_per_million),
    max_cost_cny: optional(settings.max_cost_cny),
  };
}

export function validateAgentSettings(draft: AgentSettingsDraft): string {
  const perAgent = Number(draft.per_agent_timeout_seconds);
  const total = Number(draft.total_timeout_seconds);
  const parallel = Number(draft.max_parallel);
  if (!Number.isFinite(perAgent) || perAgent < 5 || perAgent > 300) return "单个子 Agent 超时须为 5 到 300 秒。";
  if (!Number.isFinite(total) || total < 10 || total > 900) return "多 Agent 总超时须为 10 到 900 秒。";
  if (total < perAgent) return "多 Agent 总超时不能小于单个子 Agent 超时。";
  if (!Number.isInteger(parallel) || parallel < 1 || parallel > 3) return "最大并行子 Agent 数须为 1 到 3。";
  for (const [value, label] of [[draft.input_price_per_million, "输入价格"], [draft.output_price_per_million, "输出价格"]] as const) {
    if (value !== "" && (!Number.isFinite(Number(value)) || Number(value) < 0)) return `${label}须为大于等于 0 的数值。`;
  }
  if (draft.max_cost_cny !== "" && (!Number.isFinite(Number(draft.max_cost_cny)) || Number(draft.max_cost_cny) <= 0)) {
    return "单次问答费用上限须为大于 0 的数值。";
  }
  if (draft.max_cost_cny !== "" && (draft.input_price_per_million === "" || draft.output_price_per_million === "")) {
    return "设置费用上限时，必须同时填写输入和输出每百万 Token 单价。";
  }
  return "";
}

export function draftToPayload(draft: AgentSettingsDraft): AgentSettings {
  const optional = (value: string) => value === "" ? null : Number(value);
  return {
    per_agent_timeout_seconds: Number(draft.per_agent_timeout_seconds),
    total_timeout_seconds: Number(draft.total_timeout_seconds),
    max_parallel: Number(draft.max_parallel),
    input_price_per_million: optional(draft.input_price_per_million),
    output_price_per_million: optional(draft.output_price_per_million),
    max_cost_cny: optional(draft.max_cost_cny),
  };
}
